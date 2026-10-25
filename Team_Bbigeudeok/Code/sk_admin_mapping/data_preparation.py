"""01 노트북의 데이터 준비 단계. 통합 CSV가 유일한 분석 입력이다."""
from pathlib import Path
from collections import Counter, defaultdict
from io import BytesIO
from zipfile import ZipFile
from IPython.display import display, HTML
import copy
import hashlib
import html
import json
import sys
import numpy as np
import pandas as pd
import sk_admin_mapping as sk
from notebook_helpers import prepare_notebook_config, show_review_map
from project_settings import build_config, boundary_states, mapping_settings, validate_location_keys
from workflow_io import read_json, write_json, file_state, fingerprint, cache_valid, complete_state, read_result_csv
from column_labels import show_table as display

class DataPreparation:
    def __init__(self, project_root=None):
        '기준 통합 CSV와 공통 설정을 읽는다.'
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        self.work_directory = self.project_root / 'Code' / 'sk_admin_mapping'
        self.config = build_config(self.project_root)
        self.config['source_coordinates'].update({'crs': 'EPSG:5179', 'evidence': '사용자 요청에 따른 임시 가정: EPSG:5179, metre, xy, 배율 1. 공급자 확인 전이며 결과는 검토용.', 'unit': 'metre', 'xy_order': 'xy', 'scale_x': 1.0, 'scale_y': 1.0})
        self.crs_status = 'ASSUMED_UNVERIFIED'
        self.pop_columns = {'flow_age_pop': [f'{sex}_FLOW_POP_CNT_{age}' for sex in ('MAN', 'WMAN') for age in ('10G', '20G', '30G', '40G', '50G', '60GU')], 'flow_time_pop': [f'TMST_{hour:02d}' for hour in range(24)], 'flow_wkdy_pop': ['FLOW_POP_CNT_MON', 'FLOW_POP_CNT_TUS', 'FLOW_POP_CNT_WED', 'FLOW_POP_CNT_THU', 'FLOW_POP_CNT_FRI', 'FLOW_POP_CNT_SAT', 'FLOW_POP_CNT_SUN']}
        for self.table in self.config['tables']:
            self.table['population_columns'] = self.pop_columns[self.table['name']]
        self.input_directory = Path(self.config['input_dir'])
        self.output_directory = Path(self.config['output_dir'])
        self.result_directory = self.output_directory / 'canonical'
        self.result_directory.mkdir(parents=True, exist_ok=True)
        self.chunksize = 50000
        self.cell_version = 'regional_pipeline_v1'
        self.force_index = False
        self.force_map = False
        self.force_attach = False
        self.force_analysis = False
        self.group_columns = ['STD_YM', 'SIGUNGU_CD', 'SIGUNGU_NM', 'EMD_CD', 'EMD_NM']
        self.source_files = []
        for self.path in sorted(self.input_directory.rglob('*.csv')):
            self.table, self.filename_month = sk.table_for(self.path.name, self.config)
            if self.table:
                self.source_files.append({'path': self.path, 'table': self.table['name'], 'filename_month': self.filename_month})
        if not self.source_files:
            raise FileNotFoundError(f'대상 CSV가 없습니다: {self.input_directory}')
        print(f'대상 원본: {len(self.source_files)}개')
        print('결과 폴더:', self.result_directory)

    def map_sources(self):
        '전체 위치 키 대조 후 공간 매핑을 선택한다.'
        self.map_key = fingerprint({'version': self.cell_version, 'settings': mapping_settings(self.config), 'sources': [file_state(item['path']) for item in self.source_files], 'boundaries': boundary_states(self.config)})
        self.mapping_state_path = self.result_directory / 'mapping_cache.json'
        self.mapping_state = read_json(self.mapping_state_path)
        self.mapping_dir = None
        if cache_valid(self.mapping_state, self.map_key) and (self.result_directory / 'source_audit.json').is_file():
            self.mapping_dir = Path(self.mapping_state['mapping_dir'])
            self.index_dir = Path(self.mapping_state['index_dir'])
            print('검증된 통합본의 공간 매핑 캐시 재사용')
        else:
            candidates = list(self.output_directory.glob('map_*'))
            managed_mapping = self.result_directory / 'geometry' / 'map'
            if managed_mapping.is_dir():
                candidates.append(managed_mapping)
            for self.candidate in sorted(candidates, reverse=True):
                self.manifest = read_json(self.candidate / 'manifest.json')
                self.used = read_json(self.candidate / 'config_used.json')
                if self.manifest.get('status') != 'complete' or mapping_settings(self.used) != mapping_settings(self.config):
                    continue
                self.candidate_index = Path(self.manifest['index_dir'])
                if validate_location_keys(self.config, self.source_files, self.candidate_index, self.result_directory):
                    self.mapping_dir = self.candidate
                    self.index_dir = self.candidate_index
                    print('통합본의 전체 위치 키 대조 통과: 기존 공간 매핑 채택')
                    break
            if self.mapping_dir is None:
                print('위치 인덱스와 공간 매핑 새로 생성')
                self.index_dir = sk.index_command(self._run_config('geometry/index'))
                self.mapping_dir = sk.map_command(self._run_config('geometry/map'), str(self.index_dir))
                if not validate_location_keys(self.config, self.source_files, self.index_dir, self.result_directory):
                    raise ValueError('새 인덱스의 정규화 위치 키가 통합 입력과 다릅니다.')
        self.required_map_files = ['manifest.json', 'config_used.json', 'mapping.sqlite', 'normalized_location_mapping.csv', 'review_samples.csv']
        complete_state(self.mapping_state_path, self.map_key, [self.mapping_dir / name for name in self.required_map_files] + [self.result_directory / 'source_audit.json', self.index_dir / 'manifest.json', self.index_dir / 'location_index.sqlite'], mapping_dir=str(self.mapping_dir), index_dir=str(self.index_dir), input_basis='merged_csv', location_keys_verified=True)
        self.index_manifest = read_json(self.index_dir / 'manifest.json')
        print('공간 매핑 확인 상태:', '좌표계 임시 가정 / 공급자 미확인')

    def summarize_quality(self):
        '월별 공간 매칭 상태를 요약한다.'
        self.quality_state_path = self.result_directory / 'quality_cache.json'
        self.quality_summary_path = self.result_directory / 'match_summary_month_region.csv'
        self.quality_reason_path = self.result_directory / 'match_reason_counts.csv'
        self.quality_key = fingerprint({'version': self.cell_version, 'mapping': self.map_key, 'normalized_mapping': file_state(self.mapping_dir / 'normalized_location_mapping.csv')})
        if not self.force_analysis and cache_valid(read_json(self.quality_state_path), self.quality_key):
            print('전체 매칭 진단 캐시 재사용')
        else:
            write_json(self.quality_state_path, {'status': 'running', 'fingerprint': self.quality_key})
            self.status_counts = Counter()
            self.reason_counts = Counter()
            self.columns = ['STD_YM_KEY', 'SIGUNGU_CD', 'SIGUNGU_NM', 'MATCH_STATUS', 'REVIEW_REASONS']
            for self.chunk in pd.read_csv(self.mapping_dir / 'normalized_location_mapping.csv', usecols=self.columns, dtype=str, keep_default_na=False, chunksize=self.chunksize, encoding='utf-8-sig'):
                self.chunk['SIGUNGU_NM'] = self.chunk['SIGUNGU_NM'].replace('', '미판정')
                self.grouped = self.chunk.groupby(['STD_YM_KEY', 'SIGUNGU_CD', 'SIGUNGU_NM', 'MATCH_STATUS'], dropna=False).size()
                self.status_counts.update(self.grouped.to_dict())
                self.reasons = self.chunk[['STD_YM_KEY', 'SIGUNGU_CD', 'SIGUNGU_NM']].copy()
                self.reasons['REASON'] = self.chunk['REVIEW_REASONS'].str.split('|', regex=False)
                self.reasons = self.reasons.explode('REASON')
                self.reasons = self.reasons[self.reasons['REASON'].ne('')]
                self.grouped_reasons = self.reasons.groupby(['STD_YM_KEY', 'SIGUNGU_CD', 'SIGUNGU_NM', 'REASON'], dropna=False).size()
                self.reason_counts.update(self.grouped_reasons.to_dict())
            self.quality = pd.DataFrame([(*key, value) for key, value in sorted(self.status_counts.items())], columns=['STD_YM', 'SIGUNGU_CD', 'SIGUNGU_NM', 'MATCH_STATUS', 'N_LOCATIONS'])
            self.denominator = self.quality.groupby(['STD_YM', 'SIGUNGU_CD', 'SIGUNGU_NM'])['N_LOCATIONS'].transform('sum')
            self.quality['PERCENT_WITHIN_MONTH_REGION'] = self.quality['N_LOCATIONS'] / self.denominator * 100
            self.quality['CRS_STATUS'] = self.crs_status
            self.quality.to_csv(self.quality_summary_path, index=False, encoding='utf-8-sig')
            self.reasons = pd.DataFrame([(*key, value) for key, value in sorted(self.reason_counts.items())], columns=['STD_YM', 'SIGUNGU_CD', 'SIGUNGU_NM', 'REASON', 'N_LOCATIONS'])
            self.reasons.to_csv(self.quality_reason_path, index=False, encoding='utf-8-sig')
            complete_state(self.quality_state_path, self.quality_key, [self.quality_summary_path, self.quality_reason_path])
        self.quality_summary = read_result_csv(self.quality_summary_path)
        self.quality_reasons = read_result_csv(self.quality_reason_path)
        print('전체 위치 인덱스 기준 상태별 개수')
        display(self.quality_summary.groupby('MATCH_STATUS')['N_LOCATIONS'].sum().to_frame())
        display(self.quality_summary.head(20))
        display(self.quality_reasons.head(20))

    def attach_sources(self):
        '통합 CSV 문자열과 행 수를 보존하며 행정구역을 붙인다.'
        self.attach_state = self.result_directory / 'attachment_cache.json'
        self.attachment_registry = read_json(self.attach_state)
        self.attachment_registry.setdefault('entries', {})
        self.attachment_entries = []
        self.attachment_reports = []
        for self.item in self.source_files:
            self.source = self.item['path']
            self.table_name = self.item['table']
            self.source_id = str(self.source.resolve())
            self.attachment_key = fingerprint({'version': self.cell_version, 'source': file_state(self.source), 'mapping': self.map_key, 'mapping_db': file_state(self.mapping_dir / 'mapping.sqlite'), 'columns': self.config['columns'], 'population_columns': self.pop_columns[self.table_name], 'encoding': self.config.get('input_encoding'), 'separator': self.config.get('input_separator')})
            self.old_entry = self.attachment_registry['entries'].get(self.source_id, {})
            if not self.force_attach and cache_valid(self.old_entry, self.attachment_key):
                self.attached_dir = Path(self.old_entry['attached_dir'])
                print('결합 캐시:', self.source.name)
            else:
                print('원본 결합 실행:', self.source.name)
                self.attachment_registry['entries'][self.source_id] = {'status': 'running', 'fingerprint': self.attachment_key}
                write_json(self.attach_state, self.attachment_registry)
                self.attached_dir = sk.attach_command(self._run_config('attachments/' + self.table_name), source_path=str(self.source), member=None, map_requested=str(self.mapping_dir), population_requested=self.pop_columns[self.table_name])
                self.attachment_registry['entries'][self.source_id] = {'status': 'complete', 'fingerprint': self.attachment_key, 'attached_dir': str(self.attached_dir), 'outputs': [file_state(self.attached_dir / name) for name in ('manifest.json', 'source_with_admin.csv', 'attach_validation.json')]}
                write_json(self.attach_state, self.attachment_registry)
            self.report = read_json(self.attached_dir / 'attach_validation.json')
            if not self.report.get('rows_preserved') or not self.report.get('all_original_cells_preserved') or self.report['rows_before'] != self.report['rows_after'] or (not all((values.get('preserved') for values in self.report['population_fields'].values()))):
                raise ValueError(f'원본 보존 검사 실패: {self.source.name}')
            self.attachment_entries.append({**self.item, 'attached_dir': self.attached_dir, 'csv': self.attached_dir / 'source_with_admin.csv'})
            self.attachment_reports.append({'TABLE': self.table_name, 'SOURCE_FILE': self.source.name, 'ROWS_BEFORE': self.report['rows_before'], 'ROWS_AFTER': self.report['rows_after'], 'HOLD_ROWS_PRESERVED': self.report['held_rows_preserved'], 'NOT_IN_INDEX_ROWS_PRESERVED': self.report['not_in_index_rows_preserved'], 'ALL_ORIGINAL_CELLS_PRESERVED': True, 'POPULATION_VALUES_PRESERVED': True, 'ATTACHED_FILE': str(self.attached_dir / 'source_with_admin.csv')})
        self.attachment_summary = pd.DataFrame(self.attachment_reports)
        self.attachment_summary.to_csv(self.result_directory / 'attachment_summary.csv', index=False, encoding='utf-8-sig')
        display(self.attachment_summary)

    def aggregate_regions(self):
        '새 입력의 중복/결측을 분리하여 행정동별로 집계한다.'
        self.agg_state = self.result_directory / 'aggregation_cache.json'
        self.long_path = self.result_directory / 'regional_metrics_long.csv'
        self.exclusion_path = self.result_directory / 'aggregation_exclusions.csv'
        self.duplicate_path = self.result_directory / 'duplicate_location_groups.csv'
        self.wide_paths = {table: self.result_directory / f'regional_{table}.csv' for table in self.pop_columns}
        self.aggregation_key = fingerprint({'version': self.cell_version, 'mapping': self.map_key, 'index': file_state(self.index_dir / 'location_index.sqlite'), 'attachments': [{'table': item['table'], 'file': file_state(item['csv'])} for item in self.attachment_entries], 'population_columns': self.pop_columns, 'policy': 'OK_only;quarantine_all_duplicate_location_rows'})
        self.required_aggregation_files = [self.long_path, self.exclusion_path, self.duplicate_path, *self.wide_paths.values()]
        if not self.force_analysis and cache_valid(read_json(self.agg_state), self.aggregation_key):
            print('지역 집계 캐시 재사용')
        else:
            write_json(self.agg_state, {'status': 'running', 'fingerprint': self.aggregation_key})
            self.duplicates = self.duplicate_location_rows()
            self.duplicates.to_csv(self.duplicate_path, index=False, encoding='utf-8-sig')
            self.duplicate_ids = {table: set(self.duplicates.loc[self.duplicates['TABLE'].eq(table), 'LOCATION_ID'].astype(str)) for table in self.pop_columns}
            self.value_fields = ['SUM_VALUE', 'N_POINT_ROWS', 'N_VALID', 'N_MISSING_OR_MASKED', 'N_INVALID_NUMBER', 'N_NEGATIVE']
            self.accumulated = defaultdict(lambda: [0.0, 0, 0, 0, 0, 0])
            self.exclusions = []
            self.rename_admin = {'ADM_SIGUNGU_CD': 'SIGUNGU_CD', 'ADM_SIGUNGU_NM': 'SIGUNGU_NM', 'ADM_EMD_CD': 'EMD_CD', 'ADM_EMD_NM': 'EMD_NM'}
            self.missing_tokens = {'', 'NA', 'N/A', 'NULL', 'NONE', 'NAN'}
            for self.item in self.attachment_entries:
                self.table_name = self.item['table']
                self.population_columns = self.pop_columns[self.table_name]
                self.required = ['STD_YM', 'ADM_LOCATION_ID', 'ADM_MATCH_STATUS', *self.rename_admin, *self.population_columns]
                self.row_counts = Counter()
                for self.chunk in pd.read_csv(self.item['csv'], usecols=self.required, dtype=str, keep_default_na=False, chunksize=self.chunksize, encoding='utf-8-sig'):
                    self.row_counts['N_SOURCE_ROWS'] += len(self.chunk)
                    self.ok = self.chunk['ADM_MATCH_STATUS'].eq('OK')
                    self.row_counts['N_NON_OK_ROWS'] += int((~self.ok).sum())
                    self.row_counts['N_NOT_IN_INDEX_ROWS'] += int(self.chunk['ADM_MATCH_STATUS'].eq('NOT_IN_INDEX').sum())
                    self.eligible = self.chunk.loc[self.ok].copy()
                    self.location_id = self.eligible['ADM_LOCATION_ID'].str.replace('\\.0+$', '', regex=True)
                    self.duplicate_mask = self.location_id.isin(self.duplicate_ids[self.table_name])
                    self.row_counts['N_DUPLICATE_ROWS_HELD'] += int(self.duplicate_mask.sum())
                    self.eligible = self.eligible.loc[~self.duplicate_mask].copy()
                    self.row_counts['N_SPATIALLY_ACCEPTED_UNIQUE_ROWS'] += len(self.eligible)
                    if self.eligible.empty:
                        continue
                    self.base = self.eligible[['STD_YM', *self.rename_admin]].rename(columns=self.rename_admin)
                    self.base['STD_YM'] = self.base['STD_YM'].map(sk.month_key)
                    for self.column in self.population_columns:
                        self.raw = self.eligible[self.column].str.strip()
                        self.numeric = pd.to_numeric(self.raw, errors='coerce')
                        self.missing = self.raw.str.upper().isin(self.missing_tokens)
                        self.finite = pd.Series(np.isfinite(self.numeric.to_numpy(dtype=float)), index=self.numeric.index)
                        self.invalid = ~self.missing & ~self.finite
                        self.negative = ~self.missing & self.finite & self.numeric.lt(0)
                        self.valid = ~self.missing & self.finite & self.numeric.ge(0)
                        self.temp = self.base.copy()
                        self.temp['SUM_VALUE'] = self.numeric.where(self.valid, 0.0)
                        self.temp['N_POINT_ROWS'] = 1
                        self.temp['N_VALID'] = self.valid.astype('int64')
                        self.temp['N_MISSING_OR_MASKED'] = self.missing.astype('int64')
                        self.temp['N_INVALID_NUMBER'] = self.invalid.astype('int64')
                        self.temp['N_NEGATIVE'] = self.negative.astype('int64')
                        self.grouped = self.temp.groupby(self.group_columns, dropna=False, sort=False)[self.value_fields].sum()
                        for self.group_key, self.values in self.grouped.iterrows():
                            self.key = (self.table_name, *self.group_key, self.column)
                            self.total = self.accumulated[self.key]
                            self.total[0] += float(self.values['SUM_VALUE'])
                            for self.offset, self.field in enumerate(self.value_fields[1:], start=1):
                                self.total[self.offset] += int(self.values[self.field])
                self.exclusions.append({'TABLE': self.table_name, 'SOURCE_FILE': self.item['path'].name, **dict(self.row_counts)})
                print('지역 집계:', self.item['path'].name)
            self.records = []
            for self.key, self.values in self.accumulated.items():
                self.row = dict(zip(['TABLE', *self.group_columns, 'METRIC_FIELD'], self.key))
                self.row.update(dict(zip(self.value_fields, self.values)))
                if self.row['N_VALID'] == 0:
                    self.row['SUM_VALUE'] = None
                self.records.append(self.row)
            if not self.records:
                raise ValueError('집계할 정상·비중복 위치가 없습니다. 전체 매칭 진단과 중복 진단을 먼저 확인하세요.')
            self.regional = pd.DataFrame(self.records)
            self.regional['N_EXCLUDED_VALUES'] = self.regional['N_MISSING_OR_MASKED'] + self.regional['N_INVALID_NUMBER'] + self.regional['N_NEGATIVE']
            self.regional['VALUE_STATUS'] = np.select([self.regional['N_VALID'].eq(0), self.regional['N_EXCLUDED_VALUES'].gt(0)], ['NO_VALID_VALUE', 'PARTIAL_VALUES'], default='COMPLETE_VALUES')
            self.regional['MEAN_PER_VALID_POINT'] = self.regional['SUM_VALUE'] / self.regional['N_VALID'].replace(0, np.nan)
            self.group_key = ['TABLE', *self.group_columns]
            self.denominator = self.regional.groupby(self.group_key)['SUM_VALUE'].transform('sum')
            self.excluded_in_group = self.regional.groupby(self.group_key)['N_EXCLUDED_VALUES'].transform('sum')
            self.regional['SHARE_OF_TABLE_METRIC_SUM'] = (self.regional['SUM_VALUE'] / self.denominator).where(self.denominator.gt(0) & self.excluded_in_group.eq(0))
            self.weekday_labels = {'MON': '월', 'TUS': '화', 'WED': '수', 'THU': '목', 'FRI': '금', 'SAT': '토', 'SUN': '일'}

            def describe_metric(table_name, column):
                if table_name == 'flow_age_pop':
                    sex = '여성' if column.startswith('WMAN_') else '남성'
                    age = column.rsplit('_', 1)[1]
                    age_label = '60대 이상' if age == '60GU' else age.replace('G', '대')
                    return f'{sex}_{age_label}'
                if table_name == 'flow_time_pop':
                    return column[-2:] + '시'
                return self.weekday_labels[column.rsplit('_', 1)[1]] + '요일'
            self.regional['METRIC_LABEL'] = [describe_metric(table, column) for table, column in zip(self.regional['TABLE'], self.regional['METRIC_FIELD'])]
            self.regional['SOURCE_CRS'] = self.config['source_coordinates']['crs']
            self.regional['CRS_STATUS'] = self.crs_status
            self.regional['BOUNDARY_DATE'] = self.config['boundaries']['emd']['reference_date']
            self.regional['EMD_KIND'] = self.config['boundaries']['emd']['kind']
            self.regional['AGGREGATION_METHOD'] = 'sum_of_provider_point_values_assigned_to_region'
            self.regional = self.regional.sort_values(['TABLE', *self.group_columns, 'METRIC_FIELD'])
            self.regional.to_csv(self.long_path, index=False, encoding='utf-8-sig')
            pd.DataFrame(self.exclusions).to_csv(self.exclusion_path, index=False, encoding='utf-8-sig')
            for self.table_name, self.path in self.wide_paths.items():
                self.subset = self.regional[self.regional['TABLE'].eq(self.table_name)]
                self.wide = self.subset.pivot(index=self.group_columns, columns='METRIC_FIELD', values='SUM_VALUE').reset_index()
                self.wide.columns.name = None
                self.wide['CRS_STATUS'] = self.crs_status
                self.wide['BOUNDARY_DATE'] = self.config['boundaries']['emd']['reference_date']
                self.wide.to_csv(self.path, index=False, encoding='utf-8-sig')
            complete_state(self.agg_state, self.aggregation_key, self.required_aggregation_files)
        self.regional_metrics = read_result_csv(self.long_path)
        self.regional_by_table = {name: read_result_csv(path) for name, path in self.wide_paths.items()}
        self.aggregation_exclusions = read_result_csv(self.exclusion_path)
        display(self.aggregation_exclusions)
        display(self.regional_metrics.head(20))
        print('SUM_VALUE는 제공 점 값의 지역별 합계입니다. 시간대별 합을 하루의 고유 방문자 수로 해석하지 않습니다.')
        print('SHARE_OF_TABLE_METRIC_SUM은 해당 테이블 지표 합계 안의 구성비입니다. 세 테이블의 인구 값을 서로 더하지 않습니다.')

    def join_public_population(self):
        """관측월과 같은 주민등록인구를 행정동·월로 결합한다."""
        from resident_population import prepare_population, POPULATION_COLUMNS
        regions = self.regional_metrics[self.group_columns[1:]].drop_duplicates()
        resident, source, crosswalk, metadata = prepare_population(self.work_directory, regions)
        self.resident_path = self.result_directory / 'resident_population_monthly.csv'
        self.joined_path = self.result_directory / 'regional_metrics_with_resident.csv'
        self.external_summary_path = self.result_directory / 'external_join_summary.csv'
        self.external_wide_paths = {table:self.result_directory / f'regional_{table}_with_resident.csv' for table in self.pop_columns}
        self.external_state = self.result_directory / 'external_join_cache.json'
        key = fingerprint({'version':'monthly_mois_v1', 'regional':file_state(self.long_path), 'wide':[file_state(p) for p in self.wide_paths.values()], 'population':[file_state(p) for p in [source,crosswalk,metadata]], 'months':sorted(self.regional_metrics['STD_YM'].unique())})
        files = [self.resident_path,self.joined_path,self.external_summary_path,*self.external_wide_paths.values()]
        if not self.force_analysis and cache_valid(read_json(self.external_state),key):
            print('관측월별 주민등록인구 결합 캐시 재사용')
        else:
            write_json(self.external_state, {'status':'running','fingerprint':key})
            resident.to_csv(self.resident_path,index=False,encoding='utf-8-sig')
            external = resident.drop(columns=['SIGUNGU_CD','SIGUNGU_NM','EMD_NM'])
            def join(left):
                before = left.reset_index(drop=True).copy()
                result = before.merge(external,on=['EMD_CD','STD_YM'],how='left',validate='many_to_one',indicator='_external_merge',sort=False)
                if len(result)!=len(before) or not result[before.columns].reset_index(drop=True).equals(before):
                    raise ValueError('외부 결합 후 SK 행 수 또는 원래 집계 값이 달라졌습니다.')
                if not result['_external_merge'].eq('both').all():
                    raise ValueError('관측월별 외부 인구가 연결되지 않은 행이 있습니다.')
                result['EXT_JOIN_STATUS']='CODE_MONTH_MATCHED'
                return result.drop(columns='_external_merge')
            join(self.regional_metrics).to_csv(self.joined_path,index=False,encoding='utf-8-sig')
            reports=[]
            for table,left in self.regional_by_table.items():
                result=join(left)
                result.to_csv(self.external_wide_paths[table],index=False,encoding='utf-8-sig')
                reports.append({'TABLE':table,'ROWS_BEFORE':len(left),'ROWS_AFTER':len(result),'NO_EXTERNAL_CODE_MATCH':0,'MATCHED_BUT_POPULATION_UNAVAILABLE':int(result[POPULATION_COLUMNS].isna().any(axis=1).sum()),'EXT_STAT_YEAR':'2025','MONTHS':'202507~202512','OUTPUT':str(self.external_wide_paths[table])})
            pd.DataFrame(reports).to_csv(self.external_summary_path,index=False,encoding='utf-8-sig')
            complete_state(self.external_state,key,files,population_sources=[file_state(p) for p in [source,crosswalk,metadata]])
        self.regional_with_external=read_result_csv(self.joined_path)
        self.external_join_summary=read_result_csv(self.external_summary_path)
        display(self.external_join_summary)
        display(self.regional_with_external[['TABLE','STD_YM','SIGUNGU_NM','EMD_NM','METRIC_LABEL','SUM_VALUE','RESIDENT_POP_TOTAL','EXT_STAT_MONTH','EXT_JOIN_STATUS']].head(30))
        print('외부 인구는 2025년 7~12월의 월말 주민등록인구입니다. 외국인은 제외됩니다.')

    def _run_config(self, relative_name):
        """확인된 canonical 출력 경로만 갱신한다. 제공 데이터에는 접근하지 않는다."""
        import shutil
        output = (self.result_directory / relative_name).resolve()
        if not output.is_relative_to(self.result_directory.resolve()):
            raise ValueError("결과 폴더 밖의 경로입니다.")
        if output.exists(): shutil.rmtree(output)
        result = copy.deepcopy(self.config)
        result["_run_directory"] = str(output)
        return result

    def duplicate_location_rows(self):
        records = []
        for item in self.attachment_entries:
            counts = Counter()
            for chunk in pd.read_csv(item["csv"], usecols=["ADM_LOCATION_ID"], dtype=str, keep_default_na=False, chunksize=self.chunksize, encoding="utf-8-sig"):
                ids = chunk["ADM_LOCATION_ID"].str.replace(r"\.0+$", "", regex=True)
                counts.update(ids[ids.ne("")].value_counts().to_dict())
            records.extend((item["table"], key, count, 1) for key,count in counts.items() if count > 1)
        return pd.DataFrame(records, columns=["TABLE","LOCATION_ID","SOURCE_ROWS","FILE_COUNT"])

    def review_map(self):
        return show_review_map(self.config, self.mapping_dir)

