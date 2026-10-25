"""02 노트북의 탐색 및 실험 ①~④. 유동 변화 기술통계이며 위험 판정은 하지 않는다."""
import sys
from pathlib import Path
import json
import re
from copy import copy
import numpy as np
import pandas as pd
import geopandas as gpd
import folium
from branca.colormap import LinearColormap
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from IPython.display import display, Markdown, clear_output
import matplotlib.pyplot as plt
import matplotlib as mpl
import matplotlib.font_manager as fm
import sk_admin_mapping as sk
from project_settings import build_config
from workflow_io import read_json, read_csv, check_cache, file_state
from column_labels import show_table as display, setup_korean_font, COLUMN_LABELS, VALUE_LABELS


def position_monthly_changes(frame, months):
    """고정 유효 위치의 원래 수준을 보존하고 월간 변화·두 종류의 지속성을 계산한다."""
    keys = ['POSITION_KEY', 'CITY_CODE', 'DONG_CODE']
    out = frame[[*keys, 'MONTH', 'VALUE']].sort_values(['POSITION_KEY', 'MONTH']).reset_index(drop=True)
    if out.duplicated(['POSITION_KEY', 'MONTH']).any():
        raise ValueError('같은 위치·월이 중복됩니다.')
    if set(out['MONTH']) != set(months) or not out.groupby('POSITION_KEY')['MONTH'].nunique().eq(len(months)).all():
        raise ValueError('각 위치에 전체 관측월이 정확히 한 번씩 있어야 합니다.')
    if not out.groupby('POSITION_KEY')[['CITY_CODE', 'DONG_CODE']].nunique().eq(1).all().all():
        raise ValueError('기간 중 지역 배정이 달라진 위치가 있습니다.')
    if not np.isfinite(out['VALUE']).all() or out['VALUE'].lt(0).any():
        raise ValueError('유효한 비음수 유동 수준만 비교합니다. 결측을 0으로 채우지 않습니다.')
    values = out['VALUE'].to_numpy().reshape(-1, len(months))
    previous = np.full(values.shape, np.nan)
    previous[:, 1:] = values[:, :-1]
    base = np.broadcast_to(values[:, :1], values.shape)
    out['PREV'] = previous.ravel()
    out['BASE'] = base.ravel()
    out['DELTA'] = out['VALUE'] - out['PREV']
    out['BASE_DELTA'] = out['VALUE'] - out['BASE']
    # 0 분모만 계산 불가로 두며, 작은 분모와 극단적인 변화율도 그대로 남긴다.
    out['MOM_PCT'] = 100 * out['DELTA'] / out['PREV'].where(out['PREV'].gt(0))
    out['BASE_PCT'] = 100 * out['BASE_DELTA'] / out['BASE'].where(out['BASE'].gt(0))
    out['RATE_STATUS'] = np.select([out['MONTH'].eq(months[0]), out['PREV'].eq(0)], ['FIRST_MONTH', 'ZERO_PREVIOUS'], default='CALCULATED')
    out['BASE_RATE_STATUS'] = np.where(out['BASE'].eq(0), 'ZERO_BASE', 'CALCULATED')
    decrease = values < previous
    below = values < base
    run = np.zeros(len(values), dtype=int)
    below_run = run.copy()
    longest = run.copy()
    below_longest = run.copy()
    runs, below_runs, maxima, below_maxima = [], [], [], []
    for month_index in range(len(months)):
        run = np.where(decrease[:, month_index], run + 1, 0)
        below_run = np.where(below[:, month_index], below_run + 1, 0)
        longest = np.maximum(longest, run)
        below_longest = np.maximum(below_longest, below_run)
        runs.append(run.copy())
        below_runs.append(below_run.copy())
        maxima.append(longest.copy())
        below_maxima.append(below_longest.copy())
    for name, arrays in [('CURRENT_STREAK', runs), ('MAX_STREAK', maxima), ('BELOW_BASE_STREAK', below_runs), ('MAX_BELOW_BASE_STREAK', below_maxima)]:
        out[name] = np.column_stack(arrays).ravel()
    out['N_PAIRS'] = np.tile(np.arange(len(months)), len(values))
    out['N_DECREASE'] = np.cumsum(decrease, axis=1).ravel()
    out['MOM_DECREASE'] = pd.array(decrease.ravel(), dtype='boolean')
    out.loc[out['MONTH'].eq(months[0]), 'MOM_DECREASE'] = pd.NA
    out['BASE_DECREASE'] = below.ravel()
    return out


class ActivityExplorer:
    def __init__(self, project_root=None, *, load_population_context=True):
        '01의 완료된 결과와 현재 통합본 설정을 확인한다.'
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        sys.dont_write_bytecode = True
        pio.renderers.default = 'plotly_mimetype'
        self.work_directory = self.project_root / 'Code' / 'sk_admin_mapping'
        self.result_directory = self.work_directory / 'outputs' / 'canonical'
        from resident_population import MONTHS
        self.months = MONTHS.copy()
        self.chunksize = 50000
        self.crs_status = 'ASSUMED_UNVERIFIED'
        self.small_base_floor = None
        self.age_codes = ['10G', '20G', '30G', '40G', '50G', '60GU']
        self.age_labels = ['10대', '20대', '30대', '40대', '50대', '60대 이상']
        self.age_fields = [f'{sex}_FLOW_POP_CNT_{age}' for sex in ('MAN', 'WMAN') for age in self.age_codes]
        self.time_fields = [f'TMST_{h:02d}' for h in range(24)]
        self.raw_fields = {'flow_age_pop': self.age_fields, 'flow_time_pop': self.time_fields}
        self.basis_labels = {'ALL': '월별 전체 유효 위치', 'COMMON_LOCATION': '기간 내 공통 위치', 'COMMON_VALID': '공통 위치·모든 열/월 유효'}
        self.city_codes = {'강남구': '11230', '춘천시': '32010'}
        self.city_names = {v: k for k, v in self.city_codes.items()}
        self.caches = {}
        for self.name in ['mapping_cache', 'quality_cache', 'aggregation_cache', 'external_join_cache']:
            self.caches[self.name] = read_json(self.result_directory / f'{self.name}.json')
            check_cache(self.caches[self.name], self.name)
        for recorded in self.caches['external_join_cache'].get('population_sources', []):
            if file_state(recorded['path']) != recorded:
                raise ValueError('외부 인구 원자료가 바뀌었습니다. 01 노트북을 먼저 실행하세요.')
        self.mapping_directory = Path(self.caches['mapping_cache']['mapping_dir'])
        self.map_manifest = read_json(self.mapping_directory / 'manifest.json')
        self.config = build_config(self.project_root)
        self.notebook_cfg = self.config
        self.index_directory = Path(self.map_manifest['index_dir'])
        self.index_manifest = read_json(self.index_directory / 'manifest.json')
        for self.label, self.manifest, self.kind in [('매핑', self.map_manifest, 'map'), ('인덱스', self.index_manifest, 'index')]:
            if self.manifest.get('kind') != self.kind or self.manifest.get('status') != 'complete' or self.manifest.get('normalization_version') != 'decimal-exact-v1':
                raise ValueError(f'{self.label}: 예상한 완료 결과/정규화 버전이 아닙니다.')
        if self.config['source_coordinates']['crs'] != 'EPSG:5179':
            raise ValueError('확인한 로컬 결과와 원본 좌표계 설정이 다릅니다.')
        if self.config['boundaries']['emd']['kind'] != 'administrative':
            raise ValueError('행정 읍면동 경계가 아닙니다.')
        for self.level in ('sigungu', 'emd'):
            self.boundary = self.config['boundaries'][self.level]
            if self.boundary['reference_date'] != '2025-06-30' or self.boundary['code_system'] != 'SGIS_Korean_administrative_classification_8digit':
                raise ValueError(f'{self.level}: 경계 기준일/코드 체계가 달라졌습니다.')
        self.table_cfg = {t['name']: t for t in self.config['tables']}
        for self.table, self.fields in self.raw_fields.items():
            if self.table_cfg[self.table]['population_columns'] != self.fields:
                raise ValueError(f'{self.table}: 확인했던 인구 열 구성이 달라졌습니다.')
        self.registry = read_json(self.result_directory / 'attachment_cache.json')
        self.attachments = []
        self.validation_rows = []
        for self.source, self.entry in self.registry['entries'].items():
            check_cache(self.entry, Path(self.source).name)
            self.source = Path(self.source)
            self.matches = [(table, re.fullmatch(settings['filename_regex'], self.source.name)) for table, settings in self.table_cfg.items()]
            self.matches = [(t, match) for t, match in self.matches if match]
            if len(self.matches) != 1:
                raise ValueError(f'테이블을 하나로 결정할 수 없습니다: {self.source}')
            self.table, self.match = self.matches[0]
            self.month = '전체기간'
            self.attached_dir = Path(self.entry['attached_dir'])
            self.manifest = read_json(self.attached_dir / 'manifest.json')
            self.validation = read_json(self.attached_dir / 'attach_validation.json')
            if self.manifest.get('kind') != 'attach' or self.manifest.get('status') != 'complete' or self.manifest.get('normalization_version') != 'decimal-exact-v1' or (Path(self.manifest['map_dir']).resolve() != self.mapping_directory.resolve()) or (Path(self.validation['mapping_dir']).resolve() != self.mapping_directory.resolve()):
                raise ValueError(f'현재 매핑과 연결되지 않은 결합 결과입니다: {self.attached_dir}')
            self.preserved = self.validation['rows_before'] == self.validation['rows_after'] and self.validation['rows_preserved'] and self.validation['all_original_cells_preserved'] and all((v['preserved'] for v in self.validation['population_fields'].values()))
            if not self.preserved:
                raise ValueError(f'원본 보존 검사 기록이 통과하지 못했습니다: {self.attached_dir}')
            self.attachments.append({'TABLE': self.table, 'MONTH': self.month, 'PATH': self.attached_dir / 'source_with_admin.csv', 'SOURCE': self.source.name})
            self.validation_rows.append({'TABLE': self.table, 'MONTH': self.month, '원본행': self.validation['rows_before'], '결합행': self.validation['rows_after'], '원본보존': self.preserved, '보류행보존': self.validation['held_rows_preserved'], '인덱스미매칭': self.validation['not_in_index_rows_preserved']})
        self.attachments = pd.DataFrame(self.attachments).sort_values(['TABLE', 'MONTH'])
        if self.attachments.duplicated(['TABLE', 'MONTH']).any():
            raise ValueError('월·테이블당 결합 파일이 여러 개입니다. 임의 선택하지 않습니다.')
        self.input_audit = read_json(self.result_directory / 'source_audit.json')
        for self.recorded_input in self.input_audit['input_files']:
            if file_state(self.recorded_input['path']) != self.recorded_input:
                raise ValueError('기준 통합본이 바뀌었습니다. 01_데이터_준비.ipynb를 먼저 다시 실행하세요.')
        for self.table in self.raw_fields:
            if not set(self.months).issubset(self.input_audit['monthly_rows'][self.table]):
                raise ValueError(f'{self.table}: 통합본에 필요한 월이 없습니다.')
        self.quality = read_csv(self.result_directory / 'match_summary_month_region.csv', dtype=str)
        self.reasons = read_csv(self.result_directory / 'match_reason_counts.csv', dtype=str)
        self.exclusions = read_csv(self.result_directory / 'aggregation_exclusions.csv', dtype=str)
        self.duplicates = read_csv(self.result_directory / 'duplicate_location_groups.csv', dtype=str)
        self.existing = read_csv(self.result_directory / 'regional_metrics_long.csv', dtype=str)
        self.resident = read_csv(self.result_directory / 'resident_population_monthly.csv', dtype=str) if load_population_context else None
        self.external_summary = read_csv(self.result_directory / 'external_join_summary.csv', dtype=str)
        print('매핑:', self.mapping_directory)
        print('인덱스:', self.index_directory)
        print('실제 실행 좌표 설정: EPSG:5179 / metre / xy / 배율 1 (임시 가정)')
        print('좌표계 확인 상태:', VALUE_LABELS.get(self.crs_status, self.crs_status))
        print('분석 기간:', self.months)
        print('파일 보존 검사는 기존 검사 기록을 확인한 것입니다.')
        display(pd.DataFrame(self.validation_rows))
        self.case_city = '춘천시'
        self.case_age = '제공 연령 합계'
        self.case_kind = 'SUM'
        self.detail_columns = ['MONTH', 'BASIS', 'VALUE', 'PREV', 'DELTA', 'MOM_PCT', 'BASE', 'BASE_DELTA', 'BASE_PCT', 'CITY_MOM_PCT', 'MOM_MINUS_CITY_PP', 'CITY_BASE_PCT', 'BASE_MINUS_CITY_PP', 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_POOL', 'N_VALID', 'RATE_STATUS', 'COHORT_STATUS']

    def inspect_quality(self):
        '관측 위치 수와 결측/중복 진단을 확인한다.'
        self.count_fields = ['N_POINT_ROWS', 'N_VALID', 'N_MISSING_OR_MASKED', 'N_INVALID_NUMBER', 'N_NEGATIVE', 'N_EXCLUDED_VALUES']
        for self.col in self.count_fields:
            self.existing[self.col] = pd.to_numeric(self.existing[self.col], errors='raise')
        self.keys = ['TABLE', 'STD_YM', 'SIGUNGU_CD', 'SIGUNGU_NM', 'EMD_CD']
        if self.existing.groupby(self.keys)['N_POINT_ROWS'].nunique().gt(1).any():
            raise ValueError('같은 월·행정동·테이블에서 지표별 위치 수가 다릅니다.')
        self.location_counts = self.existing.groupby(self.keys, as_index=False).agg(관측위치=('N_POINT_ROWS', 'first'), 지표별유효수_최소=('N_VALID', 'min'), 지표별유효수_최대=('N_VALID', 'max'))
        self.city_counts = self.location_counts.groupby(['TABLE', 'STD_YM', 'SIGUNGU_NM'], as_index=False)[['관측위치', '지표별유효수_최소', '지표별유효수_최대']].sum()
        print('정상·비중복 위치 기준. 유효수 최소/최대는 지표별 진단입니다.')
        display(self.city_counts)
        display(self.existing.groupby('TABLE')[['N_MISSING_OR_MASKED', 'N_INVALID_NUMBER', 'N_NEGATIVE']].sum().rename_axis('테이블'))
        display(self.exclusions)
        display(self.duplicates)
        display(self.reasons.groupby('REASON')['N_LOCATIONS'].apply(lambda s: pd.to_numeric(s).sum()).to_frame('월을 포함한 위치 수'))
        self.regions = self.existing[['SIGUNGU_CD', 'SIGUNGU_NM', 'EMD_CD', 'EMD_NM']].drop_duplicates().rename(columns={'SIGUNGU_CD': 'CITY_CODE', 'SIGUNGU_NM': 'CITY', 'EMD_CD': 'DONG_CODE', 'EMD_NM': 'NAME'}).sort_values(['CITY_CODE', 'NAME']).reset_index(drop=True)
        if self.regions.duplicated(['CITY_CODE', 'DONG_CODE']).any() or self.regions['DONG_CODE'].duplicated().any():
            raise ValueError('행정동 코드와 이름의 관계가 하나로 결정되지 않습니다.')
        self.region_name = self.regions.set_index('DONG_CODE')['NAME'].to_dict()
        print('관측할 질문:')
        print('같은 지역의 같은 위치에서 특정 연령대의 감소가 여러 달 이어지는가?')
        print('다른 연령대 및 대상지역 전체의 변화와 얼마나 다른가?')
        print('감소 구간 수·최장 연속 감소는 기술통계이며 공식 판정 기준이 아닙니다.')

    def read_locations(self, tables=None):
        '월 독립 관측 위치 키로 통합본을 읽는다.'
        self.mapping_cols = ['LOCATION_ID', 'STD_YM_KEY', 'BLOCK_CD_KEY', 'X_COORD_KEY', 'Y_COORD_KEY', 'SIGUNGU_CD', 'EMD_CD', 'MATCH_STATUS']
        self.parts = []
        for self.chunk in read_csv(self.mapping_directory / 'normalized_location_mapping.csv', usecols=self.mapping_cols, dtype=str, keep_default_na=False, chunksize=self.chunksize):
            self.keep = self.chunk['MATCH_STATUS'].eq('OK') & self.chunk['STD_YM_KEY'].isin(self.months) & self.chunk['SIGUNGU_CD'].isin(self.city_names)
            self.parts.append(self.chunk.loc[self.keep].copy())
        self.locations = pd.concat(self.parts, ignore_index=True)
        if self.locations.empty or self.locations['LOCATION_ID'].duplicated().any():
            raise ValueError('정상 위치가 없거나 LOCATION_ID가 중복됩니다.')
        self.spatial_key = pd.MultiIndex.from_frame(self.locations[['BLOCK_CD_KEY', 'X_COORD_KEY', 'Y_COORD_KEY']])
        self.locations['POSITION_KEY'] = pd.factorize(self.spatial_key, sort=False)[0].astype('int32')
        self.position_identity = self.locations[['POSITION_KEY', 'BLOCK_CD_KEY', 'X_COORD_KEY', 'Y_COORD_KEY']].drop_duplicates('POSITION_KEY')
        self.lookup = self.locations[['LOCATION_ID', 'STD_YM_KEY', 'SIGUNGU_CD', 'EMD_CD', 'POSITION_KEY']].rename(columns={'LOCATION_ID': 'LOCATION_ID', 'STD_YM_KEY': 'MONTH', 'SIGUNGU_CD': 'CITY_CODE', 'EMD_CD': 'DONG_CODE'})
        if self.lookup.duplicated(['MONTH', 'POSITION_KEY']).any():
            raise ValueError('월·정규화 위치 튜플이 중복됩니다.')
        self.assignment_counts = self.lookup.groupby('POSITION_KEY')[['CITY_CODE', 'DONG_CODE']].nunique()
        self.stable_positions = set(self.assignment_counts.index[self.assignment_counts.eq(1).all(axis=1)])
        print('기간 중 지역 배정이 달라 공통 비교에서 보류할 위치:', int((~self.assignment_counts.eq(1).all(axis=1)).sum()))
        del self.locations, self.spatial_key, self.parts, self.assignment_counts
        self.missing_tokens = {'', 'NA', 'N/A', 'NULL', 'NONE', 'NAN'}
        self.location_data = {}
        self.metric_names_by_table = {}
        self.raw_samples = []
        self.value_diagnostics = []
        for self.table, self.fields in self.raw_fields.items():
            if tables is not None and self.table not in tables:
                continue
            self.frames = []
            self.duplicate_ids = set(self.duplicates.loc[self.duplicates['TABLE'].eq(self.table), 'LOCATION_ID'].str.replace('\\.0+$', '', regex=True))
            self.table_files = self.attachments[self.attachments['TABLE'].eq(self.table)]
            for self.item in self.table_files.to_dict('records'):
                self.needed = ['STD_YM', 'BLOCK_CD', 'X_COORD', 'Y_COORD', 'ADM_LOCATION_ID', 'ADM_MATCH_STATUS', 'ADM_SIGUNGU_CD', 'ADM_EMD_CD', *self.fields]
                for self.chunk in read_csv(self.item['PATH'], usecols=self.needed, dtype=str, keep_default_na=False, chunksize=self.chunksize):
                    self.chunk['LOCATION_ID'] = self.chunk['ADM_LOCATION_ID'].str.replace('\\.0+$', '', regex=True)
                    self.keep = self.chunk['ADM_MATCH_STATUS'].eq('OK') & ~self.chunk['LOCATION_ID'].isin(self.duplicate_ids)
                    self.f = self.chunk.loc[self.keep].merge(self.lookup, on='LOCATION_ID', how='left', validate='many_to_one', sort=False)
                    if self.f['POSITION_KEY'].isna().any():
                        raise ValueError(f"{self.item['PATH']}: 정상 행의 위치 키가 없습니다.")
                    if not self.f['STD_YM'].map(sk.month_key).eq(self.f['MONTH']).all() or not self.f['ADM_SIGUNGU_CD'].eq(self.f['CITY_CODE']).all() or (not self.f['ADM_EMD_CD'].eq(self.f['DONG_CODE']).all()):
                        raise ValueError(f"월·행정구역의 대응이 일치하지 않습니다: {self.item['PATH']}")
                    self.sample_cols = ['STD_YM', 'MONTH', 'CITY_CODE', 'DONG_CODE', 'POSITION_KEY', 'BLOCK_CD', 'X_COORD', 'Y_COORD', 'ADM_MATCH_STATUS', *self.fields]
                    self.sample = self.f.groupby(['MONTH', 'DONG_CODE'], sort=False).head(3)[self.sample_cols].copy()
                    self.sample['TABLE'] = self.table
                    self.raw_samples.append(self.sample)
                    self.numeric = pd.DataFrame(index=self.f.index)
                    for self.field in self.fields:
                        self.raw = self.f[self.field].str.strip()
                        self.values = pd.to_numeric(self.raw, errors='coerce')
                        self.finite = pd.Series(np.isfinite(self.values.to_numpy(dtype=float)), index=self.f.index)
                        self.missing = self.raw.str.upper().isin(self.missing_tokens)
                        self.valid = ~self.missing & self.finite & self.values.ge(0)
                        self.numeric[self.field] = self.values.where(self.valid)
                        for observed_month, row_indices in self.f.groupby('MONTH').groups.items():
                            self.value_diagnostics.append({'TABLE': self.table, 'MONTH': observed_month, 'FIELD': self.field,
                                'N_VALUES': len(row_indices), 'N_VALID': int(self.valid.loc[row_indices].sum()),
                                'N_MISSING': int(self.missing.loc[row_indices].sum()),
                                'N_INVALID': int((~self.missing & ~self.finite).loc[row_indices].sum()),
                                'N_NEGATIVE': int((~self.missing & self.finite & self.values.lt(0)).loc[row_indices].sum())})
                    self.out = self.f[['MONTH', 'CITY_CODE', 'DONG_CODE', 'POSITION_KEY']].copy()
                    self.out['POSITION_KEY'] = self.out['POSITION_KEY'].astype('int32')
                    if self.table == 'flow_age_pop':
                        for self.age_code, self.label in zip(self.age_codes, self.age_labels):
                            self.out[self.label] = self.numeric[f'MAN_FLOW_POP_CNT_{self.age_code}'] + self.numeric[f'WMAN_FLOW_POP_CNT_{self.age_code}']
                        self.out['제공 연령 합계'] = self.out[self.age_labels].sum(axis=1, min_count=len(self.age_labels))
                        self.metric_names = [*self.age_labels, '제공 연령 합계']
                    elif self.table == 'flow_wkdy_pop':
                        self.metric_names = self.weekday_labels.copy()
                        for self.field, self.label in zip(self.fields, self.metric_names):
                            self.out[self.label] = self.numeric[self.field]
                        self.out['평일 평균'] = self.numeric[self.weekday_fields[:5]].sum(axis=1, min_count=5) / 5
                        self.out['주말 평균'] = self.numeric[self.weekday_fields[5:]].sum(axis=1, min_count=2) / 2
                        self.metric_names = [*self.metric_names, '평일 평균', '주말 평균']
                    else:
                        self.metric_names = [f'{h:02d}시' for h in range(24)]
                        for self.field, self.label in zip(self.fields, self.metric_names):
                            self.out[self.label] = self.numeric[self.field]
                        self.out['24시간 값 합계'] = self.numeric.sum(axis=1, min_count=24)
                        self.metric_names = [*self.metric_names, '24시간 값 합계']
                    self.out['ROW_COMPLETE'] = self.numeric.notna().all(axis=1)
                    self.frames.append(self.out)
                print('읽음:', self.table, self.item['MONTH'])
            self.d = pd.concat(self.frames, ignore_index=True)
            self.duplicate_rows = self.d.duplicated(['MONTH', 'POSITION_KEY'], keep=False)
            print(self.table, '추가 중복 보류 행:', int(self.duplicate_rows.sum()))
            self.d = self.d.loc[~self.duplicate_rows].reset_index(drop=True)
            self.location_data[self.table] = self.d
            self.metric_names_by_table[self.table] = self.metric_names
        self.samples = pd.concat(self.raw_samples, ignore_index=True).groupby(['TABLE', 'MONTH', 'DONG_CODE'], sort=False).head(3).reset_index(drop=True)
        self.value_diagnostics = pd.DataFrame(self.value_diagnostics).groupby(['TABLE', 'MONTH'], as_index=False)[['N_VALUES', 'N_VALID', 'N_MISSING', 'N_INVALID', 'N_NEGATIVE']].sum()
        display(self.value_diagnostics)
        print('위 표는 위치 수가 아니라 원본 인구 열에 들어 있는 값의 개수입니다.')
        del self.frames, self.raw_samples, self.f, self.chunk, self.numeric, self.out

    def aggregate_cohorts(self):
        '전체/공통/엄격 공통 유효 위치를 별도로 집계한다.'
        self.total_age_label = '제공 연령 합계'
        self.total_time_label = '24시간 값 합계'
        self.cohorts = {}
        self.records = []
        for self.table, self.d in self.location_data.items():
            self.months_per_position = self.d.groupby('POSITION_KEY')['MONTH'].nunique()
            self.common = set(self.months_per_position.index[self.months_per_position.eq(len(self.months))]) & self.stable_positions
            self.complete_counts = self.d.loc[self.d['POSITION_KEY'].isin(self.common) & self.d['ROW_COMPLETE']].groupby('POSITION_KEY')['MONTH'].nunique()
            self.strict = set(self.complete_counts.index[self.complete_counts.eq(len(self.months))])
            self.cohorts[self.table] = {'COMMON': self.common, 'STRICT': self.strict}
            self.frames = {'ALL': self.d, 'COMMON_LOCATION': self.d.loc[self.d['POSITION_KEY'].isin(self.common)], 'COMMON_VALID': self.d.loc[self.d['POSITION_KEY'].isin(self.strict)]}
            for self.level in ('DONG', 'CITY'):
                self.group_keys = ['MONTH', 'CITY_CODE', 'DONG_CODE'] if self.level == 'DONG' else ['MONTH', 'CITY_CODE']
                if self.level == 'DONG':
                    self.tuples = [(m, c, dong) for m in self.months for c, dong in self.regions[['CITY_CODE', 'DONG_CODE']].itertuples(index=False, name=None)]
                else:
                    self.tuples = [(m, c) for m in self.months for c in self.city_names]
                self.skeleton = pd.MultiIndex.from_tuples(self.tuples, names=self.group_keys)
                self.observed = self.d.groupby(self.group_keys).size().reindex(self.skeleton, fill_value=0)
                self.n_common = self.frames['COMMON_LOCATION'].groupby(self.group_keys).size().reindex(self.skeleton, fill_value=0)
                self.n_strict = self.frames['COMMON_VALID'].groupby(self.group_keys).size().reindex(self.skeleton, fill_value=0)
                for self.basis, self.subset in self.frames.items():
                    self.grouped = self.subset.groupby(self.group_keys)
                    self.sums = self.grouped[self.metric_names_by_table[self.table]].sum(min_count=1).reindex(self.skeleton)
                    self.counts = self.grouped[self.metric_names_by_table[self.table]].count().reindex(self.skeleton, fill_value=0)
                    self.pools = self.grouped.size().reindex(self.skeleton, fill_value=0)
                    for self.key in self.skeleton:
                        self.identity = dict(zip(self.group_keys, self.key))
                        if self.level == 'CITY':
                            self.identity['DONG_CODE'] = '__CITY__'
                        for self.metric in self.metric_names_by_table[self.table]:
                            self.n_valid = int(self.counts.at[self.key, self.metric])
                            self.total = self.sums.at[self.key, self.metric]
                            self.records.append({'TABLE': self.table, 'BASIS': self.basis, 'LEVEL': self.level, **self.identity, 'METRIC': self.metric, 'SUM': self.total if self.n_valid else np.nan, 'MEAN': self.total / self.n_valid if self.n_valid else np.nan, 'N_OBS': int(self.observed.at[self.key]), 'N_COMMON': int(self.n_common.at[self.key]), 'N_STRICT': int(self.n_strict.at[self.key]), 'N_POOL': int(self.pools.at[self.key]), 'N_VALID': self.n_valid, 'N_INVALID': int(self.pools.at[self.key]) - self.n_valid})
        self.monthly_metrics = pd.DataFrame(self.records)
        self.monthly_metrics['CITY'] = self.monthly_metrics['CITY_CODE'].map(self.city_names)
        self.monthly_metrics['NAME'] = self.monthly_metrics['DONG_CODE'].map(self.region_name).fillna('대상지역 전체')
        print('각 테이블에서 공통 위치 집합을 별도로 계산했습니다.')
        print('COMMON_VALID는 해당 테이블의 모든 인구 열이 모든 달에 유효한 위치입니다.')
        print('남녀 합계와 연령 비교는 위치 단계에서 계산했습니다.')
        self.coverage = self.monthly_metrics.loc[self.monthly_metrics['LEVEL'].eq('CITY') & self.monthly_metrics['BASIS'].eq('COMMON_LOCATION') & (self.monthly_metrics['TABLE'].eq('flow_age_pop') & self.monthly_metrics['METRIC'].eq(self.total_age_label) | self.monthly_metrics['TABLE'].eq('flow_time_pop') & self.monthly_metrics['METRIC'].eq(self.total_time_label)), ['TABLE', 'CITY', 'MONTH', 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_VALID']]
        display(self.coverage.rename(columns={'N_OBS': '전체관측위치', 'N_COMMON': '공통관측위치', 'N_STRICT': '모든열_모든월_유효위치', 'N_VALID': '공통위치에서_해당합계유효위치'}))

    def calculate_changes(self):
        '전월/기준월 변화와 기존 연속 감소 기술통계를 계산한다.'
        self.series_parts = []
        for self.value_type in ('SUM', 'MEAN'):
            self.part = self.monthly_metrics.copy()
            self.part['VALUE_TYPE'] = self.value_type
            self.part['VALUE'] = self.part[self.value_type]
            self.series_parts.append(self.part)
        self.change_series = pd.concat(self.series_parts, ignore_index=True)
        self.series_keys = ['TABLE', 'BASIS', 'LEVEL', 'CITY_CODE', 'DONG_CODE', 'METRIC', 'VALUE_TYPE']
        self.change_series = self.change_series.sort_values([*self.series_keys, 'MONTH']).reset_index(drop=True)
        self.grouped = self.change_series.groupby(self.series_keys, sort=False, dropna=False)
        self.change_series['PREV'] = self.grouped['VALUE'].shift(1)
        self.change_series['BASE'] = self.grouped['VALUE'].transform(lambda s: s.iloc[0])
        self.change_series['DELTA'] = self.change_series['VALUE'] - self.change_series['PREV']
        self.change_series['BASE_DELTA'] = self.change_series['VALUE'] - self.change_series['BASE']
        self.change_series['MOM_PCT'] = 100 * self.change_series['DELTA'] / self.change_series['PREV'].where(self.usable_denominator(self.change_series['PREV']))
        self.change_series['BASE_PCT'] = 100 * self.change_series['BASE_DELTA'] / self.change_series['BASE'].where(self.usable_denominator(self.change_series['BASE']))
        self.change_series['BASE_INDEX'] = 100 * self.change_series['VALUE'] / self.change_series['BASE'].where(self.usable_denominator(self.change_series['BASE']))
        self.small_prev = self.change_series['PREV'].gt(0) & self.change_series['PREV'].le(self.small_base_floor) if self.small_base_floor is not None else pd.Series(False, index=self.change_series.index)
        self.change_series['RATE_STATUS'] = np.select([self.change_series['VALUE'].isna(), self.change_series['MONTH'].eq(self.months[0]), self.change_series['PREV'].isna(), self.change_series['PREV'].eq(0), self.small_prev], ['CURRENT_MISSING', 'FIRST_MONTH', 'PREVIOUS_MISSING', 'ZERO_PREVIOUS', 'SMALL_PREVIOUS'], default='CALCULATED')
        self.change_series['COHORT_STATUS'] = np.where(self.change_series['BASIS'].eq('COMMON_VALID'), 'FIXED_VALID_COHORT', 'DESCRIPTIVE_COVERAGE_OR_VALIDITY_CAN_CHANGE')
        for self.col in ['N_PAIRS', 'N_DECREASE', 'CURRENT_STREAK', 'MAX_STREAK']:
            self.change_series[self.col] = np.nan
        for self._, self.indices in self.change_series.loc[self.change_series['BASIS'].eq('COMMON_VALID')].groupby(self.series_keys, sort=False).groups.items():
            self.n_pairs = self.n_decrease = self.run = self.longest = 0
            for self.i in self.indices:
                self.current, self.previous = (self.change_series.at[self.i, 'VALUE'], self.change_series.at[self.i, 'PREV'])
                self.comparable = pd.notna(self.current) and pd.notna(self.previous)
                if self.comparable:
                    self.n_pairs += 1
                    if self.current < self.previous:
                        self.n_decrease += 1
                        self.run += 1
                        self.longest = max(self.longest, self.run)
                    else:
                        self.run = 0
                else:
                    self.run = 0
                if pd.notna(self.current):
                    self.change_series.loc[self.i, ['N_PAIRS', 'N_DECREASE', 'CURRENT_STREAK', 'MAX_STREAK']] = [self.n_pairs, self.n_decrease, self.run, self.longest]
        self.city_reference = self.change_series.loc[self.change_series['LEVEL'].eq('CITY'), ['TABLE', 'BASIS', 'CITY_CODE', 'MONTH', 'METRIC', 'VALUE_TYPE', 'MOM_PCT', 'BASE_PCT', 'BASE_INDEX']].rename(columns={'MOM_PCT': 'CITY_MOM_PCT', 'BASE_PCT': 'CITY_BASE_PCT', 'BASE_INDEX': 'CITY_BASE_INDEX'})
        self.change_series = self.change_series.merge(self.city_reference, on=['TABLE', 'BASIS', 'CITY_CODE', 'MONTH', 'METRIC', 'VALUE_TYPE'], how='left', validate='many_to_one')
        self.change_series['MOM_MINUS_CITY_PP'] = self.change_series['MOM_PCT'] - self.change_series['CITY_MOM_PCT']
        self.change_series['BASE_MINUS_CITY_PP'] = self.change_series['BASE_PCT'] - self.change_series['CITY_BASE_PCT']
        print('기준월:', self.months[0], '/ 최대 비교 구간:', len(self.months) - 1)
        print('변화율의 0 분모와 결측 분모는 계산하지 않습니다.')
        print('작은 분모 기준:', self.small_base_floor)
        print('도시 기준은 해당 대상지역 전체이며, 비교하는 행정동도 포함합니다.')
        print('SUM = 제공 점 값의 지역 합계 / MEAN = 유효 위치당 평균')

    def usable_denominator(self, values):
        ok = values.notna() & values.gt(0)
        if self.small_base_floor is not None:
            ok &= values.gt(self.small_base_floor)
        return ok

    def calculate_location_experiment(self):
        """실험 ①: 기존 성·연령 COMMON_VALID 집합으로 위치 변화 → 행정동 요약."""
        strict = self.cohorts['flow_age_pop']['STRICT']
        data = self.location_data['flow_age_pop']
        data = data.loc[data['POSITION_KEY'].isin(strict)].rename(columns={self.total_age_label: 'VALUE'})
        self.location_changes = position_monthly_changes(data, self.months)
        self.location_changes = self.location_changes.merge(self.position_identity, on='POSITION_KEY', how='left', validate='many_to_one')
        self.location_changes['CITY'] = self.location_changes['CITY_CODE'].map(self.city_names)
        self.location_changes['NAME'] = self.location_changes['DONG_CODE'].map(self.region_name)
        self.location_changes['BASIS'] = 'COMMON_VALID'
        group_keys = ['MONTH', 'CITY_CODE', 'DONG_CODE']
        grouped = self.location_changes.groupby(group_keys)
        totals = grouped.agg(VALUE=('VALUE', 'sum'), N_POOL=('POSITION_KEY', 'size'))
        # 기존 집계의 행정동 골격 및 진단 수를 재사용한다. 무관측 동은 0 합계로 채우지 않는다.
        existing = self.monthly_metrics.loc[
            self.monthly_metrics['TABLE'].eq('flow_age_pop') & self.monthly_metrics['BASIS'].eq('COMMON_VALID')
            & self.monthly_metrics['LEVEL'].eq('DONG') & self.monthly_metrics['METRIC'].eq(self.total_age_label)
        ].copy().set_index(group_keys)
        compared = totals.join(existing[['SUM', 'N_POOL']], rsuffix='_EXISTING', validate='one_to_one')
        if not np.allclose(compared['VALUE'], compared['SUM'], rtol=1e-12, atol=1e-8) or not compared['N_POOL'].eq(compared['N_POOL_EXISTING']).all():
            raise ValueError('위치 합계·위치 수가 기존 COMMON_VALID 행정동 집계와 다릅니다.')
        summary = existing[['CITY', 'NAME', 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_POOL']].join(totals[['VALUE']]).reset_index()
        summary = summary.sort_values(['CITY_CODE', 'DONG_CODE', 'MONTH']).reset_index(drop=True)
        series = summary.groupby(['CITY_CODE', 'DONG_CODE'], sort=False)
        summary['PREV'] = series['VALUE'].shift()
        summary['BASE'] = series['VALUE'].transform('first')
        summary['DELTA'] = summary['VALUE'] - summary['PREV']
        summary['BASE_DELTA'] = summary['VALUE'] - summary['BASE']
        summary['MOM_PCT'] = 100 * summary['DELTA'] / summary['PREV'].where(summary['PREV'].gt(0))
        summary['BASE_PCT'] = 100 * summary['BASE_DELTA'] / summary['BASE'].where(summary['BASE'].gt(0))
        summary['RATE_STATUS'] = np.select([summary['VALUE'].isna(), summary['MONTH'].eq(self.months[0]), summary['PREV'].eq(0)], ['NO_OBSERVATION', 'FIRST_MONTH', 'ZERO_PREVIOUS'], default='CALCULATED')
        summary['BASE_RATE_STATUS'] = np.select([summary['BASE'].isna(), summary['BASE'].eq(0)], ['NO_OBSERVATION', 'ZERO_BASE'], default='CALCULATED')
        measures = self.location_changes[group_keys].copy()
        measures['N_MOM_COMPARABLE'] = self.location_changes['PREV'].notna().astype(int)
        measures['N_BASE_COMPARABLE'] = 1
        for prefix, flag in [('MOM', 'MOM_DECREASE'), ('BASE', 'BASE_DECREASE')]:
            mask = self.location_changes[flag].fillna(False)
            measures[f'N_{prefix}_DECREASE'] = mask.astype(int)
            measures[f'{prefix}_DECREASE_BASE_FLOW'] = self.location_changes['BASE'].where(mask, 0)
        # 0~5의 정확한 현재 지속기간 분포. 특정 기간을 위험·위축 임계값으로 선택하지 않는다.
        for prefix, field in [('CURRENT', 'CURRENT_STREAK'), ('BELOW_BASE', 'BELOW_BASE_STREAK')]:
            for duration in range(len(self.months)):
                measures[f'N_{prefix}_STREAK_{duration}'] = self.location_changes[field].eq(duration).astype(int)
        statistics = measures.groupby(group_keys).sum().reset_index()
        summary = summary.merge(statistics, on=group_keys, how='left', validate='one_to_one')
        summary['N_MOM_COMPARABLE'] = summary['N_MOM_COMPARABLE'].fillna(0).astype(int)
        summary['N_BASE_COMPARABLE'] = summary['N_BASE_COMPARABLE'].fillna(0).astype(int)
        for prefix in ['MOM', 'BASE']:
            denominator = summary[f'N_{prefix}_COMPARABLE'].where(summary[f'N_{prefix}_COMPARABLE'].gt(0))
            summary[f'{prefix}_DECREASE_PCT'] = 100 * summary[f'N_{prefix}_DECREASE'] / denominator
            summary[f'{prefix}_DECREASE_BASE_WEIGHT_PCT'] = 100 * summary[f'{prefix}_DECREASE_BASE_FLOW'] / summary['BASE'].where(summary['BASE'].gt(0))
        first = summary['MONTH'].eq(self.months[0])
        summary.loc[first, ['N_MOM_DECREASE', 'MOM_DECREASE_BASE_FLOW', 'MOM_DECREASE_BASE_WEIGHT_PCT']] = np.nan
        for prefix in ['CURRENT', 'BELOW_BASE']:
            for duration in range(len(self.months)):
                count = f'N_{prefix}_STREAK_{duration}'
                summary[f'{prefix}_STREAK_{duration}_PCT'] = 100 * summary[count] / summary['N_POOL'].where(summary['N_POOL'].gt(0))
        summary['BASIS'] = 'COMMON_VALID'
        self.location_summary = summary
        print('실험 ①:', len(strict), '고정 위치 /', len(self.location_changes), '위치·월 /', len(summary), '행정동·월')
        print('연속 감소는 월간 구간 수, 기준월 아래 유지는 월수입니다. 동일 값은 지속을 끊습니다.')
        print('행정동 변화율은 유동 합계의 변화율이며 위치 변화율의 평균이 아닙니다.')

    def export_location_experiment(self):
        """기존 결과는 건드리지 않고 실험 ①의 두 CSV만 고정 경로에 저장한다."""
        from column_labels import korean_table
        output = self.result_directory / 'exports'
        output.mkdir(exist_ok=True)
        paths = [output / '실험1_위치별_월간유동변화.csv', output / '실험1_행정동별_월간유동변화.csv']
        for frame, path in zip([self.location_changes, self.location_summary], paths):
            korean_table(frame).to_csv(path, index=False, encoding='utf-8-sig')
        return paths

    def location_experiment_map(self, city, month, field='BASE_PCT'):
        """기존 간소화 경계·Folium으로 행정동만 표시한다. 관측 위치 점은 렌더링하지 않는다."""
        fields = {'MOM_PCT': '전월 대비 합계 변화율(%)', 'BASE_PCT': '7월 대비 합계 변화율(%)',
                  'MOM_DECREASE_PCT': '전월 대비 감소 위치 비율(%)', 'BASE_DECREASE_PCT': '7월 대비 감소 위치 비율(%)'}
        if field not in fields or month not in self.months:
            raise ValueError('지원하는 지도 지표와 관측월을 선택하세요.')
        all_months = self.location_summary.loc[self.location_summary['CITY_CODE'].eq(self.city_codes[city])]
        current = all_months.loc[all_months['MONTH'].eq(month)]
        columns = ['DONG_CODE', 'VALUE', 'PREV', 'BASE', 'N_POOL', 'N_MOM_COMPARABLE', 'N_BASE_COMPARABLE', field]
        geo = self.web_boundaries.loc[self.web_boundaries['CITY_CODE'].eq(self.city_codes[city])].merge(current[columns], on='DONG_CODE', how='left', validate='one_to_one')
        finite = all_months[field].dropna()
        if field in ['MOM_PCT', 'BASE_PCT']:
            limit = max(float(finite.abs().max()), 1) if len(finite) else 1
            cmap = LinearColormap(['#2166ac', '#ffffff', '#b2182b'], index=[-limit, 0, limit], vmin=-limit, vmax=limit)
        else:
            cmap = LinearColormap(['#ffffff', '#fee08b', '#d73027'], vmin=0, vmax=100)
        cmap.caption = f'{city} · {fields[field]} · 기간 전체 고정 범위'
        for column in columns[1:]:
            geo[column + '_TEXT'] = geo[column].map(self.number_text)
        def style(feature):
            value = feature['properties'].get(field)
            return {'fillColor': '#bdbdbd' if value is None or not np.isfinite(value) else '#ffffff' if value == 0 else cmap(value), 'fillOpacity': 0.8, 'color': '#555555', 'weight': 0.7}
        result = folium.Map(tiles='OpenStreetMap')
        bounds = geo.total_bounds
        result.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        folium.GeoJson(geo.to_json(), style_function=style, tooltip=folium.GeoJsonTooltip(
            fields=['NAME', 'DONG_CODE', *[c + '_TEXT' for c in columns[1:]]],
            aliases=['행정동', '행정동 코드', '현재 유동 합계', '전월 유동 합계', '7월 유동 합계', '고정 유효 위치 수', '전월 비교 위치 수', '7월 비교 위치 수', fields[field]], localize=False)).add_to(result)
        cmap.add_to(result)
        return result

    def location_experiment_view(self, city='강남구', dong=None, month=None, field='BASE_PCT'):
        """지역·월·행정동을 직접 선택해 지도와 원래 수준 추이를 확인한다."""
        month = month or self.months[-1]
        rows = self.location_summary.loc[self.location_summary['CITY_CODE'].eq(self.city_codes[city])]
        if dong is None:
            dong = rows.sort_values('NAME')['DONG_CODE'].iloc[0]
        if dong not in set(rows['DONG_CODE']):
            raise ValueError('선택 지역에 속한 행정동 코드를 사용하세요.')
        selected = rows.loc[rows['DONG_CODE'].eq(dong)]
        result_map = self.location_experiment_map(city, month, field)
        print(f'{city} / {month} / 제공 전체 연령 / COMMON_VALID / 회색=비교 불가, 흰색=0')
        print('합계는 제공 집계 값이며 고유 방문자 수가 아닙니다. 좌표계는 임시 가정·미확인입니다.')
        display(result_map)
        figure = self.line_plot(selected, 'VALUE', 'NAME', f'{city} · {self.region_name[dong]} · 동일 위치의 월별 유동 합계', '제공 점 일평균 값 합계')
        display(selected[['MONTH', 'VALUE', 'PREV', 'BASE', 'MOM_PCT', 'BASE_PCT', 'RATE_STATUS', 'BASE_RATE_STATUS', 'N_POOL', 'N_MOM_COMPARABLE', 'MOM_DECREASE_PCT', 'BASE_DECREASE_PCT', 'MOM_DECREASE_BASE_WEIGHT_PCT', 'BASE_DECREASE_BASE_WEIGHT_PCT']])
        display(selected[['MONTH', *[f'{p}_STREAK_{k}_PCT' for p in ['CURRENT', 'BELOW_BASE'] for k in range(len(self.months))]]])
        return result_map, figure

    def calculate_age_experiment(self):
        """실험 ②: 같은 위치 집합에서 각 연령을 비교하고 지역 합계와 구성비를 분리한다."""
        data = self.location_data['flow_age_pop']
        fixed = data.loc[data['POSITION_KEY'].isin(self.cohorts['flow_age_pop']['STRICT'])]
        if not np.allclose(fixed[self.age_labels].sum(axis=1), fixed[self.total_age_label], rtol=1e-12, atol=1e-8):
            raise ValueError('위치별 연령 합계가 기존 제공 전체 연령 합계와 다릅니다.')
        keys = ['LEVEL', 'CITY_CODE', 'DONG_CODE', 'MONTH']
        metrics = self.monthly_metrics.loc[self.monthly_metrics['TABLE'].eq('flow_age_pop') & self.monthly_metrics['BASIS'].eq('COMMON_VALID')]
        totals = metrics.loc[metrics['METRIC'].eq(self.total_age_label), [*keys, 'SUM']].rename(columns={'SUM': 'TOTAL_FLOW'})
        parts, diagnostics = [], []
        for age in self.age_labels:
            # 실험 ① 함수로 같은 위치의 6개월을 먼저 연결한다. 거대한 연령별 CSV는 저장하지 않는다.
            positions = position_monthly_changes(fixed.rename(columns={age: 'VALUE'}), self.months)
            diagnostics.append({'METRIC': age, 'N_POSITIONS': positions['POSITION_KEY'].nunique(), 'N_POSITION_MONTHS': len(positions),
                                'N_ZERO': int(positions['VALUE'].eq(0).sum()), 'N_ZERO_PREVIOUS': int(positions['PREV'].eq(0).sum()),
                                'N_ZERO_BASE': int(positions['BASE'].eq(0).sum())})
            for level in ['DONG', 'CITY']:
                position_keys = ['CITY_CODE', 'DONG_CODE', 'MONTH'] if level == 'DONG' else ['CITY_CODE', 'MONTH']
                grouped = positions.groupby(position_keys)
                sums = grouped['VALUE'].sum()
                base = metrics.loc[metrics['LEVEL'].eq(level) & metrics['METRIC'].eq(age)].copy()
                joined = base.set_index(position_keys)['SUM'].reindex(sums.index)
                if not np.allclose(sums, joined, rtol=1e-12, atol=1e-8):
                    raise ValueError(f'{age}/{level}: 위치 합계가 기존 COMMON_VALID 집계와 다릅니다.')
                # 지역 합계에도 같은 지속성 함수를 적용한다. 임시 번호는 지역별 시계열 ID이다.
                regional = base[['CITY_CODE', 'DONG_CODE', 'MONTH', 'SUM']].rename(columns={'SUM': 'VALUE'})
                regional['POSITION_KEY'] = pd.factorize(pd.MultiIndex.from_frame(regional[['CITY_CODE', 'DONG_CODE']]))[0]
                valid = regional['VALUE'].notna()
                calculated = position_monthly_changes(regional.loc[valid], self.months).drop(columns='POSITION_KEY') if valid.any() else pd.DataFrame(columns=['CITY_CODE', 'DONG_CODE', 'MONTH', 'VALUE'])
                regional_keys = ['CITY_CODE', 'DONG_CODE', 'MONTH']
                result = base.drop(columns=['SUM', 'MEAN']).merge(calculated, on=regional_keys, how='left', validate='one_to_one')
                measures = positions[position_keys].copy()
                measures['N_MOM_COMPARABLE'] = positions['PREV'].notna().astype(int)
                measures['N_BASE_COMPARABLE'] = 1
                measures['N_MOM_DECREASE'] = positions['MOM_DECREASE'].fillna(False).astype(int)
                measures['N_BASE_DECREASE'] = positions['BASE_DECREASE'].astype(int)
                for prefix, field in [('POSITION_CURRENT', 'CURRENT_STREAK'), ('POSITION_BELOW_BASE', 'BELOW_BASE_STREAK')]:
                    for duration in range(len(self.months)):
                        measures[f'N_{prefix}_STREAK_{duration}'] = positions[field].eq(duration).astype(int)
                result = result.merge(measures.groupby(position_keys).sum().reset_index(), on=position_keys, how='left', validate='one_to_one')
                for prefix in ['MOM', 'BASE']:
                    result[f'N_{prefix}_COMPARABLE'] = result[f'N_{prefix}_COMPARABLE'].fillna(0).astype(int)
                    denominator = result[f'N_{prefix}_COMPARABLE'].where(result[f'N_{prefix}_COMPARABLE'].gt(0))
                    result[f'{prefix}_DECREASE_PCT'] = 100 * result[f'N_{prefix}_DECREASE'] / denominator
                result.loc[result['MONTH'].eq(self.months[0]), 'N_MOM_DECREASE'] = np.nan
                for prefix in ['POSITION_CURRENT', 'POSITION_BELOW_BASE']:
                    for duration in range(len(self.months)):
                        result[f'{prefix}_STREAK_{duration}_PCT'] = 100 * result[f'N_{prefix}_STREAK_{duration}'] / result['N_POOL'].where(result['N_POOL'].gt(0))
                if 'RATE_STATUS' in result:
                    result.loc[result['VALUE'].isna(), ['RATE_STATUS', 'BASE_RATE_STATUS']] = 'NO_OBSERVATION'
                parts.append(result)
        self.age_location_diagnostics = pd.DataFrame(diagnostics)
        summary = pd.concat(parts, ignore_index=True).merge(totals, on=keys, how='left', validate='many_to_one')
        summary = summary.sort_values([*keys[:-1], 'METRIC', 'MONTH']).reset_index(drop=True)
        series = summary.groupby([*keys[:-1], 'METRIC'], sort=False)
        summary['SHARE_PCT'] = 100 * summary['VALUE'] / summary['TOTAL_FLOW'].where(summary['TOTAL_FLOW'].gt(0))
        summary['SHARE_STATUS'] = np.select([summary['TOTAL_FLOW'].isna(), summary['TOTAL_FLOW'].eq(0)], ['NO_OBSERVATION', 'ZERO_TOTAL'], default='CALCULATED')
        summary['PREV_SHARE_PCT'] = series['SHARE_PCT'].shift()
        summary['BASE_SHARE_PCT'] = series['SHARE_PCT'].transform(lambda s: s.iloc[0])
        summary['SHARE_MOM_PP'] = summary['SHARE_PCT'] - summary['PREV_SHARE_PCT']
        summary['SHARE_BASE_PP'] = summary['SHARE_PCT'] - summary['BASE_SHARE_PCT']
        summary['SHARE_MOM_STATUS'] = np.select([summary['MONTH'].eq(self.months[0]), summary['SHARE_PCT'].isna(), summary['PREV_SHARE_PCT'].isna()], ['FIRST_MONTH', 'CURRENT_SHARE_UNAVAILABLE', 'PREVIOUS_SHARE_UNAVAILABLE'], default='CALCULATED')
        summary['SHARE_BASE_STATUS'] = np.select([summary['SHARE_PCT'].isna(), summary['BASE_SHARE_PCT'].isna()], ['CURRENT_SHARE_UNAVAILABLE', 'BASE_SHARE_UNAVAILABLE'], default='CALCULATED')
        total_previous = series['TOTAL_FLOW'].shift()
        total_base = series['TOTAL_FLOW'].transform(lambda s: s.iloc[0])
        summary['TOTAL_MOM_PCT'] = 100 * (summary['TOTAL_FLOW'] - total_previous) / total_previous.where(total_previous.gt(0))
        summary['TOTAL_BASE_PCT'] = 100 * (summary['TOTAL_FLOW'] - total_base) / total_base.where(total_base.gt(0))
        summary['MOM_MINUS_TOTAL_PP'] = summary['MOM_PCT'] - summary['TOTAL_MOM_PCT']
        summary['BASE_MINUS_TOTAL_PP'] = summary['BASE_PCT'] - summary['TOTAL_BASE_PCT']
        # 전체 변화율의 연령별 기여도. 구성비 변화와 다르며 6개 연령의 합이 전체 변화율이다.
        summary['MOM_CHANGE_CONTRIB_PP'] = 100 * summary['DELTA'] / total_previous.where(total_previous.gt(0))
        summary['BASE_CHANGE_CONTRIB_PP'] = 100 * summary['BASE_DELTA'] / total_base.where(total_base.gt(0))
        city = summary.loc[summary['LEVEL'].eq('CITY'), ['CITY_CODE', 'MONTH', 'METRIC', 'MOM_PCT', 'BASE_PCT']].rename(columns={'MOM_PCT': 'CITY_MOM_PCT', 'BASE_PCT': 'CITY_BASE_PCT'})
        summary = summary.merge(city, on=['CITY_CODE', 'MONTH', 'METRIC'], how='left', validate='many_to_one')
        summary['MOM_MINUS_CITY_PP'] = summary['MOM_PCT'] - summary['CITY_MOM_PCT']
        summary['BASE_MINUS_CITY_PP'] = summary['BASE_PCT'] - summary['CITY_BASE_PCT']
        self.age_summary = summary
        print('실험 ②:', len(fixed) // len(self.months), '고정 위치 /', len(summary), '지역·월·연령 요약행')
        print('모든 연령은 같은 COMMON_VALID 위치 집합입니다. 지역 합계 지속성과 위치 지속기간 분포를 구분합니다.')
        print('시군구 기준에는 해당 행정동이 포함됩니다. 연령별 차이는 기술통계이며 유의성·타겟을 판정하지 않습니다.')

    def export_age_experiment(self):
        """실험 ② 지역·월·연령 요약 CSV만 저장한다."""
        from column_labels import korean_table
        path = self.result_directory / 'exports' / '실험2_연령대별_월간유동변화.csv'
        path.parent.mkdir(exist_ok=True)
        korean_table(self.age_summary).to_csv(path, index=False, encoding='utf-8-sig')
        return path

    def age_experiment_map(self, city, month, age='10대', field='BASE_PCT'):
        """실험 ① 지도를 재사용해 연령별 변화율을 행정동 단위로 표시한다."""
        if age not in self.age_labels or field not in ['MOM_PCT', 'BASE_PCT']:
            raise ValueError('제공 연령대와 전월/7월 변화율을 선택하세요.')
        # 실험 ①의 상태를 보존하며 기존 지도 렌더링 경로만 재사용한다.
        temporary = copy(self)
        temporary.location_summary = self.age_summary.loc[self.age_summary['LEVEL'].eq('DONG') & self.age_summary['METRIC'].eq(age)]
        result = temporary.location_experiment_map(city, month, field)
        for child in result._children.values():
            if isinstance(child, LinearColormap):
                child.caption = f'{city} · {age} · {COLUMN_LABELS[field]} · 동일 고정 위치 / 기간 전체 고정 범위'
        return result

    def age_experiment_view(self, city='강남구', dong=None, month=None, age='10대'):
        """연령별 원래 수준·변화율·구성비를 시군구와 선택 동에서 각각 비교한다."""
        month = month or self.months[-1]
        rows = self.age_summary.loc[self.age_summary['CITY_CODE'].eq(self.city_codes[city])]
        if dong is None:
            dong = self.regions.loc[self.regions['CITY_CODE'].eq(self.city_codes[city])].sort_values('NAME')['DONG_CODE'].iloc[0]
        selected = rows.loc[rows['LEVEL'].eq('DONG') & rows['DONG_CODE'].eq(dong)]
        if selected.empty:
            raise ValueError('선택 지역의 행정동 코드를 사용하세요.')
        print('시군구와 행정동은 같은 연령·같은 위치 기준으로 비교합니다. 시군구에는 선택 동이 포함됩니다.')
        figures = {}
        for label, subset in [('시군구 전체', rows.loc[rows['LEVEL'].eq('CITY')]), (self.region_name[dong], selected)]:
            for field, description, unit in [('VALUE', '월별 원래 유동 합계', '제공 점 일평균 값 합계'), ('BASE_PCT', '7월 대비 유동 수준 변화율', '%'), ('SHARE_PCT', '월별 유동 구성비', '%')]:
                figures[f'{label}_{field}'] = self.line_plot(subset, field, 'METRIC', f'{city} · {label} · {description}', unit)
        display(selected.loc[selected['MONTH'].eq(month), ['METRIC', 'MONTH', 'VALUE', 'BASE', 'MOM_PCT', 'BASE_PCT', 'SHARE_PCT', 'SHARE_MOM_PP', 'SHARE_BASE_PP', 'CURRENT_STREAK', 'BELOW_BASE_STREAK', 'CITY_BASE_PCT', 'BASE_MINUS_CITY_PP', 'BASE_CHANGE_CONTRIB_PP', 'BASE_DECREASE_PCT']])
        print('아래 지속성은 연령별 지역 유동 합계의 구간 수·월수입니다. 위치별 지속성 분포는 CSV의 별도 열입니다.')
        result_map = self.age_experiment_map(city, month, age)
        display(result_map)
        print(f'지도: {city} / {month} / {age} / 회색=비교 불가, 흰색=0 / 좌표계 임시 가정·미확인')
        return result_map, figures

    def calculate_time_experiment(self):
        """실험 ③: 시간대 자체의 고정 위치 집합으로 총량과 시간 구성을 분리한다."""
        hours = [f'{h:02d}시' for h in range(24)]
        data = self.location_data['flow_time_pop']
        fixed = data.loc[data['POSITION_KEY'].isin(self.cohorts['flow_time_pop']['STRICT'])]
        if not np.allclose(fixed[hours].sum(axis=1), fixed[self.total_time_label], rtol=1e-12, atol=1e-8):
            raise ValueError('위치별 시간대 합계가 기존 24시간 제공 값 합계와 다릅니다.')
        connected = position_monthly_changes(fixed.rename(columns={self.total_time_label: 'VALUE'}), self.months)
        self.time_location_diagnostics = {
            'TIME_FIXED_POSITIONS': int(connected['POSITION_KEY'].nunique()), 'TIME_POSITION_MONTHS': len(connected),
            'TIME_ZERO_VALUES': int(fixed[hours].eq(0).sum().sum()),
            'TIME_INVALID_VALUES': int((~np.isfinite(fixed[hours])).sum().sum()),
        }
        # 기존 집계 골격·진단을 재사용한다. 시간대마다 위치를 따로 걸러내지 않는다.
        metrics = self.monthly_metrics.loc[self.monthly_metrics['TABLE'].eq('flow_time_pop') & self.monthly_metrics['BASIS'].eq('COMMON_VALID')].copy()
        keys = ['LEVEL', 'CITY_CODE', 'DONG_CODE', 'METRIC', 'MONTH']
        regional = metrics[['CITY_CODE', 'DONG_CODE', 'MONTH', 'SUM']].rename(columns={'SUM': 'VALUE'})
        regional['POSITION_KEY'] = pd.factorize(pd.MultiIndex.from_frame(metrics[keys[:-1]]))[0]
        valid = regional['VALUE'].notna()
        changes = position_monthly_changes(regional.loc[valid], self.months)
        identity = pd.DataFrame({'POSITION_KEY': regional['POSITION_KEY'], 'LEVEL': metrics['LEVEL'], 'METRIC': metrics['METRIC']}).drop_duplicates('POSITION_KEY')
        changes = changes.merge(identity, on='POSITION_KEY', validate='many_to_one').drop(columns='POSITION_KEY')
        result = metrics.drop(columns=['SUM', 'MEAN']).merge(changes, on=keys, how='left', validate='one_to_one')
        result['VALUE_TYPE'] = 'SUM'
        result.loc[result['VALUE'].isna(), ['RATE_STATUS', 'BASE_RATE_STATUS']] = 'NO_OBSERVATION'
        self.time_totals = result.loc[result['METRIC'].eq(self.total_time_label)].copy()
        total_keys = ['LEVEL', 'CITY_CODE', 'DONG_CODE', 'MONTH']
        totals = self.time_totals[[*total_keys, 'VALUE', 'PREV', 'BASE']].rename(columns={'VALUE': 'TOTAL_24H_FLOW', 'PREV': 'PREV_TOTAL_24H_FLOW', 'BASE': 'BASE_TOTAL_24H_FLOW'})
        summary = result.loc[result['METRIC'].isin(hours)].merge(totals, on=total_keys, how='left', validate='many_to_one')
        summary = summary.sort_values([*keys[:-1], 'MONTH']).reset_index(drop=True)
        series = summary.groupby(keys[:-1], sort=False)
        summary['TIME_SHARE_PCT'] = 100 * summary['VALUE'] / summary['TOTAL_24H_FLOW'].where(summary['TOTAL_24H_FLOW'].gt(0))
        summary['TIME_SHARE_STATUS'] = np.select([summary['TOTAL_24H_FLOW'].isna(), summary['TOTAL_24H_FLOW'].eq(0)], ['NO_OBSERVATION', 'ZERO_24H_TOTAL'], default='CALCULATED')
        summary['TIME_PREV_SHARE_PCT'] = series['TIME_SHARE_PCT'].shift()
        summary['TIME_BASE_SHARE_PCT'] = series['TIME_SHARE_PCT'].transform(lambda s: s.iloc[0])
        summary['TIME_SHARE_MOM_PP'] = summary['TIME_SHARE_PCT'] - summary['TIME_PREV_SHARE_PCT']
        summary['TIME_SHARE_BASE_PP'] = summary['TIME_SHARE_PCT'] - summary['TIME_BASE_SHARE_PCT']
        summary['TIME_SHARE_MOM_STATUS'] = np.select([summary['MONTH'].eq(self.months[0]), summary['TIME_SHARE_PCT'].isna(), summary['TIME_PREV_SHARE_PCT'].isna()], ['FIRST_MONTH', 'CURRENT_SHARE_UNAVAILABLE', 'PREVIOUS_SHARE_UNAVAILABLE'], default='CALCULATED')
        summary['TIME_SHARE_BASE_STATUS'] = np.select([summary['TIME_SHARE_PCT'].isna(), summary['TIME_BASE_SHARE_PCT'].isna()], ['CURRENT_SHARE_UNAVAILABLE', 'BASE_SHARE_UNAVAILABLE'], default='CALCULATED')
        summary['HOUR_MOM_CHANGE_CONTRIB_PP'] = 100 * summary['DELTA'] / summary['PREV_TOTAL_24H_FLOW'].where(summary['PREV_TOTAL_24H_FLOW'].gt(0))
        summary['HOUR_BASE_CHANGE_CONTRIB_PP'] = 100 * summary['BASE_DELTA'] / summary['BASE_TOTAL_24H_FLOW'].where(summary['BASE_TOTAL_24H_FLOW'].gt(0))
        grouped = summary.groupby(total_keys)
        sum_check = grouped['VALUE'].sum(min_count=24)
        expected = grouped['TOTAL_24H_FLOW'].first()
        if not np.allclose(sum_check, expected, rtol=1e-12, atol=1e-8, equal_nan=True):
            raise ValueError('지역 시간대 합계와 24시간 합계가 다릅니다.')
        stats = grouped.agg(N_HOURS_MOM_COMPARABLE=('PREV', 'count'), N_HOURS_BASE_COMPARABLE=('BASE', 'count'),
                            N_HOURS_MOM_DECREASE=('DELTA', lambda s: s.lt(0).sum()), N_HOURS_BASE_DECREASE=('BASE_DELTA', lambda s: s.lt(0).sum()),
                            MAX_ABS_TIME_SHARE_MOM_PP=('TIME_SHARE_MOM_PP', lambda s: s.abs().max()),
                            MAX_ABS_TIME_SHARE_BASE_PP=('TIME_SHARE_BASE_PP', lambda s: s.abs().max())).reset_index()
        stats.loc[stats['N_HOURS_MOM_COMPARABLE'].eq(0), 'N_HOURS_MOM_DECREASE'] = np.nan
        stats.loc[stats['N_HOURS_BASE_COMPARABLE'].eq(0), 'N_HOURS_BASE_DECREASE'] = np.nan
        self.time_totals = self.time_totals.merge(stats, on=total_keys, how='left', validate='one_to_one')
        self.time_summary = summary
        print('실험 ③:', self.time_location_diagnostics['TIME_FIXED_POSITIONS'], '시간대 고정 위치 /', len(summary), '시간대 요약 /', len(self.time_totals), '24시간 합계 요약')
        print('성·연령 자료와 독립된 주변분포입니다. 24시간 제공 값 합계는 고유 방문자 수가 아닙니다.')

    def export_time_experiment(self):
        """실험 ③의 시간대 요약과 24시간 합계 요약만 고정 경로에 저장한다."""
        from column_labels import korean_table
        output = self.result_directory / 'exports'
        output.mkdir(exist_ok=True)
        paths = [output / '실험3_시간대별_월간유동변화.csv', output / '실험3_24시간합계_월간유동변화.csv']
        for frame, path in zip([self.time_summary, self.time_totals], paths):
            korean_table(frame).to_csv(path, index=False, encoding='utf-8-sig')
        return paths

    def time_experiment_view(self, city='강남구', dong=None, month=None):
        """기존 프로필을 사용해 총량과 구성비를 나란히 표시한다. 자동 유형 판정은 하지 않는다."""
        month = month or self.months[-1]
        if month not in self.months:
            raise ValueError('관측 기간의 월을 선택하세요.')
        level, code = ('CITY', '__CITY__') if dong is None else ('DONG', dong)
        selected = self.time_summary.loc[self.time_summary['CITY_CODE'].eq(self.city_codes[city]) & self.time_summary['LEVEL'].eq(level) & self.time_summary['DONG_CODE'].eq(code)]
        if selected.empty:
            raise ValueError('선택 지역에 속한 행정동 코드를 사용하세요.')
        totals = self.time_totals.loc[self.time_totals['CITY_CODE'].eq(self.city_codes[city]) & self.time_totals['LEVEL'].eq(level) & self.time_totals['DONG_CODE'].eq(code)].sort_values('MONTH')
        source = pd.concat([self.time_summary, self.time_totals], ignore_index=True)
        profile = self.time_profile(city, dong, source=source, show=False)
        name = '시군구 전체' if dong is None else self.region_name[dong]
        combined = make_subplots(rows=1, cols=2, subplot_titles=['24시간 제공 값 합계', '시간대별 구성비(%)'], column_widths=[0.38, 0.62], horizontal_spacing=0.12)
        combined.add_trace(go.Scatter(x=totals['MONTH'], y=totals['VALUE'], mode='lines+markers', name='24시간 제공 값 합계', connectgaps=False), row=1, col=1)
        combined.add_trace(profile['shares'].data[0], row=1, col=2)
        combined.update_xaxes(title_text='월', row=1, col=1)
        combined.update_yaxes(title_text='제공 시간대 값의 합계', row=1, col=1)
        combined.update_xaxes(title_text='시간대', row=1, col=2)
        combined.update_yaxes(title_text='월', row=1, col=2)
        combined.update_layout(title=f'{city} · {name} · 총량과 구성비를 별도로 확인', template='plotly_white', height=400, showlegend=False)
        hours = [f'{h:02d}시' for h in range(24)]
        changes = selected.pivot(index='MONTH', columns='METRIC', values='TIME_SHARE_BASE_PP').reindex(index=self.months, columns=hours)
        maximum = changes.abs().max().max()
        limit = float(maximum) if pd.notna(maximum) and maximum > 0 else 1
        heat = go.Figure(go.Heatmap(x=hours, y=self.months, z=changes.to_numpy(dtype=float), colorscale='RdBu_r', zmin=-limit, zmax=limit, zmid=0, colorbar_title='7월 대비 %p', hoverongaps=False))
        heat.update_layout(title=f'{city} · {name} · 7월 대비 시간대 구성비 변화', xaxis_title='시간대', yaxis_title='월', height=350)
        figures = {'profile': profile['profile'], 'total_and_shares': combined, 'share_changes': heat}
        print(f'{city} · {name} · 시간대 COMMON_VALID / 빈칸=미산출 / 고유 방문자 수 아님')
        print('성·연령×시간대는 계산하지 않습니다. 실험 ①과는 지역·월별 서로 다른 자료의 경향만 비교합니다.')
        for figure in figures.values():
            display(figure)
        display(totals[['MONTH', 'VALUE', 'PREV', 'BASE', 'MOM_PCT', 'BASE_PCT', 'RATE_STATUS', 'BASE_RATE_STATUS', 'N_POOL', 'N_HOURS_MOM_DECREASE', 'N_HOURS_BASE_DECREASE', 'MAX_ABS_TIME_SHARE_BASE_PP']])
        display(selected.loc[selected['MONTH'].eq(month), ['METRIC', 'VALUE', 'PREV', 'BASE', 'MOM_PCT', 'BASE_PCT', 'TIME_SHARE_PCT', 'TIME_SHARE_MOM_PP', 'TIME_SHARE_BASE_PP', 'TIME_SHARE_STATUS', 'HOUR_BASE_CHANGE_CONTRIB_PP']])
        return figures

    def read_weekday_locations(self):
        """실험 ④에서만 요일 입력을 활성화한다. 기존 기본 입력·설정을 바꾸지 않는다."""
        from zipfile import ZipFile
        from xml.etree import ElementTree as ET
        import hashlib
        self.weekday_fields = [f'FLOW_POP_CNT_{d}' for d in ['MON', 'TUS', 'WED', 'THU', 'FRI', 'SAT', 'SUN']]
        self.weekday_labels = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
        if self.table_cfg['flow_wkdy_pop']['population_columns'] != self.weekday_fields:
            raise ValueError('기존 설정의 요일 원본 열이 정의서와 다릅니다.')
        # 제공 정의서의 확인한 셀만 읽는다. 프로젝트에 Excel 패키지 의존성을 추가하지 않는다.
        layout = Path(self.config['layout_file'])
        ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with ZipFile(layout) as archive:
            strings = ET.fromstring(archive.read('xl/sharedStrings.xml')) if 'xl/sharedStrings.xml' in archive.namelist() else None
            shared = [''.join(t.text or '' for t in item.findall('.//m:t', ns)) for item in strings.findall('m:si', ns)] if strings is not None else []
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            def cell_text(coordinate):
                cell = sheet.find(f'.//m:c[@r="{coordinate}"]', ns)
                if cell is None:
                    raise ValueError(f'요일 정의서의 확인 셀이 없습니다: {coordinate}')
                if cell.get('t') == 'inlineStr':
                    return ''.join(t.text or '' for t in cell.findall('.//m:t', ns))
                value = cell.find('m:v', ns)
                text = value.text if value is not None else ''
                return shared[int(text)] if cell.get('t') == 's' else text
            meaning = cell_text('C72')
            fields = [cell_text(f'B{row}') for row in range(78, 85)]
        if '요일' not in meaning or '일평균' not in meaning or fields != self.weekday_fields:
            raise ValueError('요일별 일평균 의미·열을 확인하지 못했습니다. 평균식을 임의 적용하지 않습니다.')
        with layout.open('rb') as stream:
            layout_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.weekday_definition = {'path': str(layout), 'sheet': 'Sheet1', 'meaning_cell': 'C72', 'field_range': 'B78:B84',
                                   'row_meaning': meaning, 'fields': fields, 'sha256': layout_hash}
        self.raw_fields['flow_wkdy_pop'] = self.weekday_fields
        self.read_locations(tables=['flow_wkdy_pop'])

    def calculate_weekday_experiment(self):
        """요일 자체의 고정 위치에서 두 평균을 계산한 뒤 지역 수준과 합계 비율을 요약한다."""
        data = self.location_data['flow_wkdy_pop']
        fixed = data.loc[data['POSITION_KEY'].isin(self.cohorts['flow_wkdy_pop']['STRICT'])]
        weekdays, weekends = self.weekday_labels[:5], self.weekday_labels[5:]
        if not np.allclose(fixed['평일 평균'], fixed[weekdays].sum(axis=1) / 5, rtol=1e-12, atol=1e-8) or not np.allclose(fixed['주말 평균'], fixed[weekends].sum(axis=1) / 2, rtol=1e-12, atol=1e-8):
            raise ValueError('요일 원래 값과 평일·주말 평균식이 다릅니다.')
        metrics = self.monthly_metrics.loc[self.monthly_metrics['TABLE'].eq('flow_wkdy_pop') & self.monthly_metrics['BASIS'].eq('COMMON_VALID') & self.monthly_metrics['METRIC'].isin(['평일 평균', '주말 평균'])].copy()
        for metric in ['평일 평균', '주말 평균']:
            positions = position_monthly_changes(fixed.rename(columns={metric: 'VALUE'}), self.months)
            for level in ['DONG', 'CITY']:
                group_keys = ['CITY_CODE', 'DONG_CODE', 'MONTH'] if level == 'DONG' else ['CITY_CODE', 'MONTH']
                actual = positions.groupby(group_keys)['VALUE'].sum()
                expected = metrics.loc[metrics['LEVEL'].eq(level) & metrics['METRIC'].eq(metric)].set_index(group_keys)['SUM'].reindex(actual.index)
                if not np.allclose(actual, expected, rtol=1e-12, atol=1e-8):
                    raise ValueError(f'{metric}/{level}: 위치 계산과 기존 지역 집계가 다릅니다.')
        self.weekday_location_diagnostics = {
            'WKDY_FIXED_POSITIONS': int(fixed['POSITION_KEY'].nunique()), 'WKDY_POSITION_MONTHS': len(fixed),
            'WKDY_ZERO_WEEKDAY': int(fixed['평일 평균'].eq(0).sum()), 'WKDY_ZERO_WEEKEND': int(fixed['주말 평균'].eq(0).sum()),
            'WKDY_INVALID_VALUES': int((~np.isfinite(fixed[self.weekday_labels])).sum().sum()),
        }
        keys = ['LEVEL', 'CITY_CODE', 'DONG_CODE', 'METRIC', 'MONTH']
        regional = metrics[['CITY_CODE', 'DONG_CODE', 'MONTH', 'SUM']].rename(columns={'SUM': 'VALUE'})
        regional['POSITION_KEY'] = pd.factorize(pd.MultiIndex.from_frame(metrics[keys[:-1]]))[0]
        valid = regional['VALUE'].notna()
        changes = position_monthly_changes(regional.loc[valid], self.months)
        identity = pd.DataFrame({'POSITION_KEY': regional['POSITION_KEY'], 'LEVEL': metrics['LEVEL'], 'METRIC': metrics['METRIC']}).drop_duplicates('POSITION_KEY')
        changes = changes.merge(identity, on='POSITION_KEY', validate='many_to_one').drop(columns='POSITION_KEY')
        self.weekday_summary = metrics.drop(columns=['SUM', 'MEAN']).merge(changes, on=keys, how='left', validate='one_to_one')
        self.weekday_summary.loc[self.weekday_summary['VALUE'].isna(), ['RATE_STATUS', 'BASE_RATE_STATUS']] = 'NO_OBSERVATION'
        ratio_keys = ['LEVEL', 'CITY_CODE', 'DONG_CODE', 'MONTH']
        components = ['VALUE', 'PREV', 'BASE', 'DELTA', 'BASE_DELTA', 'MOM_PCT', 'BASE_PCT']
        weekday = self.weekday_summary.loc[self.weekday_summary['METRIC'].eq('평일 평균'), [*ratio_keys, 'CITY', 'NAME', 'BASIS', 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_POOL', *components]].rename(columns={c: f'WEEKDAY_{c}' for c in components})
        weekend = self.weekday_summary.loc[self.weekday_summary['METRIC'].eq('주말 평균'), [*ratio_keys, *components]].rename(columns={c: f'WEEKEND_{c}' for c in components})
        ratios = weekday.merge(weekend, on=ratio_keys, how='left', validate='one_to_one').sort_values([*ratio_keys[:-1], 'MONTH']).reset_index(drop=True)
        ratios['WEEKEND_WEEKDAY_RATIO'] = ratios['WEEKEND_VALUE'] / ratios['WEEKDAY_VALUE'].where(ratios['WEEKDAY_VALUE'].gt(0))
        ratios['WEEKDAY_RATIO_STATUS'] = np.select([ratios['WEEKDAY_VALUE'].isna(), ratios['WEEKDAY_VALUE'].eq(0)], ['NO_OBSERVATION', 'ZERO_WEEKDAY'], default='CALCULATED')
        series = ratios.groupby(ratio_keys[:-1], sort=False)
        ratios['PREV_WEEKEND_WEEKDAY_RATIO'] = series['WEEKEND_WEEKDAY_RATIO'].shift()
        ratios['BASE_WEEKEND_WEEKDAY_RATIO'] = series['WEEKEND_WEEKDAY_RATIO'].transform(lambda s: s.iloc[0])
        ratios['RATIO_MOM_DELTA'] = ratios['WEEKEND_WEEKDAY_RATIO'] - ratios['PREV_WEEKEND_WEEKDAY_RATIO']
        ratios['RATIO_BASE_DELTA'] = ratios['WEEKEND_WEEKDAY_RATIO'] - ratios['BASE_WEEKEND_WEEKDAY_RATIO']
        ratios['RATIO_MOM_STATUS'] = np.select([ratios['MONTH'].eq(self.months[0]), ratios['WEEKEND_WEEKDAY_RATIO'].isna(), ratios['PREV_WEEKEND_WEEKDAY_RATIO'].isna()], ['FIRST_MONTH', 'CURRENT_RATIO_UNAVAILABLE', 'PREVIOUS_RATIO_UNAVAILABLE'], default='CALCULATED')
        ratios['RATIO_BASE_STATUS'] = np.select([ratios['WEEKEND_WEEKDAY_RATIO'].isna(), ratios['BASE_WEEKEND_WEEKDAY_RATIO'].isna()], ['CURRENT_RATIO_UNAVAILABLE', 'BASE_RATIO_UNAVAILABLE'], default='CALCULATED')
        self.weekday_ratios = ratios
        print('실험 ④:', self.weekday_location_diagnostics['WKDY_FIXED_POSITIONS'], '요일 고정 위치 /', len(self.weekday_summary), '유형별 수준 /', len(ratios), '지역·월 비율')
        print('지역 비율은 주말 평균 합계 / 평일 평균 합계이며 위치별 비율의 평균이 아닙니다. 비율 차이는 원래 비율 단위입니다.')

    def export_weekday_experiment(self):
        """실험 ④의 평일·주말 수준 및 비율 CSV만 저장한다."""
        from column_labels import korean_table
        output = self.result_directory / 'exports'
        output.mkdir(exist_ok=True)
        paths = [output / '실험4_평일주말_월간유동변화.csv', output / '실험4_주말평일비율_월간변화.csv']
        for frame, path in zip([self.weekday_summary, self.weekday_ratios], paths):
            korean_table(frame).to_csv(path, index=False, encoding='utf-8-sig')
        return paths

    def weekday_experiment_view(self, city='강남구', dong=None, month=None):
        """평일·주말 원래 수준과 비율을 각각 표시한다. 임의 유지·위험 기준을 설정하지 않는다."""
        month = month or self.months[-1]
        if month not in self.months:
            raise ValueError('관측 기간의 월을 선택하세요.')
        level, code = ('CITY', '__CITY__') if dong is None else ('DONG', dong)
        selected = self.weekday_summary.loc[self.weekday_summary['CITY_CODE'].eq(self.city_codes[city]) & self.weekday_summary['LEVEL'].eq(level) & self.weekday_summary['DONG_CODE'].eq(code)]
        ratios = self.weekday_ratios.loc[self.weekday_ratios['CITY_CODE'].eq(self.city_codes[city]) & self.weekday_ratios['LEVEL'].eq(level) & self.weekday_ratios['DONG_CODE'].eq(code)]
        if selected.empty:
            raise ValueError('선택 지역에 속한 행정동 코드를 사용하세요.')
        name = '시군구 전체' if dong is None else self.region_name[dong]
        figures = {
            'levels': self.line_plot(selected, 'VALUE', 'METRIC', f'{city} · {name} · 평일·주말 평균 수준', '제공 위치의 요일 유형별 일평균 값 합계'),
            'ratio': self.line_plot(ratios, 'WEEKEND_WEEKDAY_RATIO', 'NAME', f'{city} · {name} · 주말/평일 비율', '주말 평균 / 평일 평균(배)'),
        }
        print('평일 5개·주말 2개 요일의 평균 수준입니다. 실제 월간 총방문자 수나 연령별 요일 활동이 아닙니다.')
        display(ratios[['MONTH', 'N_POOL', 'WEEKDAY_VALUE', 'WEEKEND_VALUE', 'WEEKDAY_BASE_PCT', 'WEEKEND_BASE_PCT', 'WEEKEND_WEEKDAY_RATIO', 'RATIO_MOM_DELTA', 'RATIO_BASE_DELTA', 'WEEKDAY_RATIO_STATUS']])
        display(selected.loc[selected['MONTH'].eq(month), ['METRIC', 'VALUE', 'PREV', 'BASE', 'DELTA', 'BASE_DELTA', 'MOM_PCT', 'BASE_PCT', 'RATE_STATUS', 'BASE_RATE_STATUS']])
        return figures

    def load_boundaries(self, *, include_population_context=True):
        '검증된 행정동 경계와 관측월별 주민등록인구를 읽는다.'
        self.boundary_settings = self.config['boundaries']['emd']
        self.boundary_path = Path(self.boundary_settings['path'])
        if not self.boundary_path.is_file():
            raise FileNotFoundError(f'분석용 지도에 필요한 행정동 경계: {self.boundary_path}')
        self.boundary = gpd.read_file(self.boundary_path, engine='pyogrio', columns=[self.boundary_settings['code_field'], self.boundary_settings['name_field'], 'BASE_DATE'])
        if self.boundary.crs is None or self.boundary.crs.to_epsg() != 5179:
            raise ValueError('확인했던 경계 CRS(EPSG:5179)와 다릅니다.')
        if not self.boundary['BASE_DATE'].astype(str).eq('20250630').all():
            raise ValueError('경계 파일의 BASE_DATE가 20250630과 다릅니다.')
        self.boundary = self.boundary.rename(columns={self.boundary_settings['code_field']: 'DONG_CODE', self.boundary_settings['name_field']: 'BOUNDARY_NAME'})
        self.boundary['DONG_CODE'] = self.boundary['DONG_CODE'].astype(str).str.strip()
        if self.boundary['DONG_CODE'].duplicated().any():
            raise ValueError('경계 코드가 중복됩니다.')
        self.parent_settings = self.config['parent_crosswalk']
        self.parent = read_csv(self.parent_settings['path'], dtype=str, keep_default_na=False)
        self.parent = self.parent.rename(columns={self.parent_settings['emd_column']: 'DONG_CODE', self.parent_settings['sigungu_column']: 'CITY_CODE'})
        if self.parent['DONG_CODE'].duplicated().any():
            raise ValueError('행정동-시군구 대응표가 중복됩니다.')
        self.boundaries = self.boundary.merge(self.parent[['DONG_CODE', 'CITY_CODE']], on='DONG_CODE', validate='one_to_one')
        self.boundaries = self.boundaries.loc[self.boundaries['CITY_CODE'].isin(self.city_names)].copy()
        self.boundaries['NAME'] = self.boundaries['DONG_CODE'].map(self.region_name).fillna(self.boundaries['BOUNDARY_NAME'])
        if not set(self.regions['DONG_CODE']).issubset(set(self.boundaries['DONG_CODE'])):
            raise ValueError('기존 집계의 행정동 중 경계가 없는 코드가 있습니다.')
        self.web_boundaries = self.boundaries.copy()
        self.web_boundaries.geometry = self.web_boundaries.geometry.simplify(30, preserve_topology=True)
        self.web_boundaries = self.web_boundaries.to_crs(4326)
        if not include_population_context:
            return
        from resident_population import validate_population, POPULATION_COLUMNS
        validate_population(self.resident, self.regions.rename(columns={'DONG_CODE':'EMD_CD','CITY_CODE':'SIGUNGU_CD','CITY':'SIGUNGU_NM','NAME':'EMD_NM'}))
        self.region_context = self.resident.rename(columns={'EMD_CD':'DONG_CODE','STD_YM':'MONTH','SIGUNGU_CD':'CITY_CODE','SIGUNGU_NM':'CITY','EMD_NM':'NAME'}).copy()
        print('관측월별 주민등록인구입니다. 외국인 제외 / 월말 기준 / 주민 외출률이 아닙니다.')
        display(self.region_context[['CITY','NAME','MONTH',*POPULATION_COLUMNS]])

    def age_series(self, city, age=None, kind='SUM', basis='COMMON_VALID'):
        if age is None:
            age = self.total_age_label
        code = self.city_codes[city]
        return self.change_series.loc[self.change_series['TABLE'].eq('flow_age_pop') & self.change_series['CITY_CODE'].eq(code) & self.change_series['METRIC'].eq(age) & self.change_series['VALUE_TYPE'].eq(kind) & self.change_series['BASIS'].eq(basis)].copy()

    def number_text(self, value):
        return '결측/비교 불가' if pd.isna(value) else f'{value:,.2f}'

    def analysis_map(self, city, age, month, kind, basis, view):
        if view == '감소 지속성':
            basis = 'COMMON_VALID'
            field = 'MAX_STREAK'
        else:
            field = 'VALUE' if view == '수준' else 'MOM_PCT'
        full = self.age_series(city, age, kind, basis)
        full = full.loc[full['LEVEL'].eq('DONG')]
        current = full.loc[full['MONTH'].eq(month)]
        columns = ['DONG_CODE', field, 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_POOL', 'N_VALID', 'PREV', 'BASE', 'RATE_STATUS', 'N_PAIRS', 'N_DECREASE']
        columns = list(dict.fromkeys(columns))
        geo = self.web_boundaries.loc[self.web_boundaries['CITY_CODE'].eq(self.city_codes[city])].merge(current[columns], on='DONG_CODE', how='left', validate='one_to_one')
        geo = geo.rename(columns={field: 'VAL'})
        finite = full[field].dropna()
        if view == '전월 변화율':
            limit = max(float(finite.abs().max()), 1.0) if len(finite) else 1.0
            cmap = LinearColormap(['#2166ac', '#ffffff', '#b2182b'], index=[-limit, 0, limit], vmin=-limit, vmax=limit)
            unit = '%'
        else:
            limit = float(len(self.months) - 1) if view == '감소 지속성' else max(float(finite.max()), 1.0) if len(finite) else 1.0
            cmap = LinearColormap(['#440154', '#31688e', '#35b779', '#fde725'], vmin=0, vmax=limit)
            unit = '연속 감소 월간 구간 수' if view == '감소 지속성' else '제공 점 일평균 값 합계' if kind == 'SUM' else '유효 위치당 일평균 값'
        cmap.caption = f'{city} / {age} / {view} / {unit} / 기간 전체 고정 범위'
        for col in ['PREV', 'BASE']:
            geo[col] = geo[col].map(self.number_text)
        geo['표시값'] = geo['VAL'].map(self.number_text)
        for col in ['N_OBS', 'N_COMMON', 'N_STRICT', 'N_POOL', 'N_VALID', 'N_PAIRS', 'N_DECREASE']:
            geo[col] = geo[col].map(lambda v: '없음/미산출' if pd.isna(v) else f'{int(v):,}')
        geo['RATE_STATUS'] = geo['RATE_STATUS'].fillna('NO_OBSERVATION').map(lambda value: VALUE_LABELS.get(value, value))

        def style(feature):
            value = feature['properties'].get('VAL')
            if value is None or not np.isfinite(value):
                fill = '#bdbdbd'
            elif value == 0:
                fill = '#ffffff'
            else:
                fill = cmap(value)
            return {'fillColor': fill, 'fillOpacity': 0.8, 'color': '#555555', 'weight': 0.7}
        m = folium.Map(tiles='OpenStreetMap')
        bounds = geo.total_bounds
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        folium.GeoJson(geo.to_json(), style_function=style, tooltip=folium.GeoJsonTooltip(fields=['NAME', 'DONG_CODE', '표시값', 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_POOL', 'N_VALID', 'PREV', 'BASE', 'RATE_STATUS', 'N_PAIRS', 'N_DECREASE'], aliases=['행정동', '코드', '값', '전체 관측 위치', '공통 관측 위치', '엄격 공통 유효 위치', '선택 기준 위치', '지표 유효 위치', '전월 원래 값', '기준월 원래 값', '변화율 상태', '유효 비교 구간', '감소 구간'], localize=False)).add_to(m)
        cmap.add_to(m)
        print(f'{month} | {city} | {age} | {view} | {self.basis_labels[basis]} | {unit}\n회색=결측/비교 불가, 흰색=0 | 경계 2025-06-30 | {VALUE_LABELS.get(self.crs_status, self.crs_status)}')
        if view == '감소 지속성':
            print('선택 월까지의 최장 연속 감소 구간 수입니다.')
        display(m)

    def line_plot(self, frame, field, group_col, title, y_title):
        fig = go.Figure()
        for label, rows in frame.groupby(group_col, sort=False):
            rows = rows.sort_values('MONTH')
            fig.add_trace(go.Scatter(x=rows['MONTH'], y=rows[field], mode='lines+markers', name=str(VALUE_LABELS.get(label, COLUMN_LABELS.get(label, label))), connectgaps=False))
        fig.update_layout(title=title, xaxis_title='월', yaxis_title=y_title, template='plotly_white', height=380)
        display(fig)
        return fig

    def time_profile(self, city, dong=None, *, source=None, show=True):
        source = self.change_series if source is None else source
        level, code = ('CITY', '__CITY__') if dong is None else ('DONG', dong)
        t = source.loc[source['TABLE'].eq('flow_time_pop') & source['CITY_CODE'].eq(self.city_codes[city]) & source['DONG_CODE'].eq(code) & source['LEVEL'].eq(level) & source['BASIS'].eq('COMMON_VALID') & source['VALUE_TYPE'].eq('SUM')].copy()
        hours = [f'{h:02d}시' for h in range(24)]
        hourly = t.loc[t['METRIC'].isin(hours)]
        values = hourly.pivot(index='MONTH', columns='METRIC', values='VALUE').reindex(index=self.months, columns=hours)
        denominator = values.sum(axis=1, min_count=24)
        shares = values.div(denominator.where(denominator.gt(0)), axis=0) * 100
        if show:
            print('전체 제공 연령의 시간대별 주변분포입니다.\nage와 같은 지역·월의 정보를 나란히 보는 것이며, 연령×시간대 자료가 아닙니다.\n시간대 합계는 하루 고유 방문자 수가 아닙니다.')
            print('시간대 테이블의 위치 집합은 age 테이블과 별도로 구성했습니다.')
            display(t.loc[t['METRIC'].eq(self.total_time_label), ['MONTH', 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_VALID', 'VALUE']].rename(columns={'VALUE': '24시간_제공값_합계'}))
            print('시간대별 원래 값:')
            display(values)
            print('24시간 제공 값 합계 내 구성비(%):')
            display(shares)
        fig = go.Figure()
        for month in self.months:
            fig.add_trace(go.Scatter(x=hours, y=values.loc[month], name=month, mode='lines+markers', connectgaps=False))
        name = '시군구 전체' if dong is None else self.region_name[dong]
        fig.update_layout(title=f'{city} {name} — 전체 연령 시간대 프로필', xaxis_title='시간대', yaxis_title='제공 점 일평균 값의 합계', template='plotly_white', height=380)
        if show:
            display(fig)
        heat = go.Figure(go.Heatmap(x=hours, y=self.months, z=shares.to_numpy(dtype=float), colorscale='Viridis', zmin=0, zmax=max(float(np.nanmax(shares.to_numpy())), 1) if shares.notna().any().any() else 1, colorbar_title='구성비 %', hoverongaps=False))
        heat.update_layout(title='월별 시간대 구성비 — 모든 월에 동일한 유효 위치', xaxis_title='시간대', yaxis_title='월', height=340)
        if show:
            display(heat)
        trends = go.Figure()
        for hour in hours:
            trends.add_trace(go.Scatter(x=self.months, y=values[hour], name=hour, mode='lines+markers', connectgaps=False, visible=True if hour in ['00시', '08시', '12시', '18시', '23시'] else 'legendonly'))
        trends.update_layout(title='시간대별 원래 값의 월별 추이 — 범례 클릭으로 추가 표시', yaxis_title='제공 점 일평균 값 합계', xaxis_title='월', template='plotly_white', height=380)
        if show:
            display(trends)
        return {'profile': fig, 'shares': heat, 'trends': trends}

    def dong_detail(self, city, dong, age=None, kind='SUM', show_time=True):
        if age is None:
            age = self.total_age_label
        code = self.city_codes[city]
        x = self.change_series.loc[self.change_series['TABLE'].eq('flow_age_pop') & self.change_series['CITY_CODE'].eq(code) & self.change_series['DONG_CODE'].eq(dong) & self.change_series['LEVEL'].eq('DONG') & self.change_series['METRIC'].eq(age) & self.change_series['VALUE_TYPE'].eq(kind)].copy()
        x = x.sort_values(['MONTH', 'BASIS'])
        print(f'{city} {self.region_name[dong]} | {age} | {VALUE_LABELS.get(kind, kind)} | {VALUE_LABELS.get(self.crs_status, self.crs_status)}')
        display(x[self.detail_columns])
        self.line_plot(x, 'VALUE', 'BASIS', '전체 위치와 공통 위치의 원래 값 비교', '제공 점 값 합계' if kind == 'SUM' else '유효 위치당 평균')
        strict = x.loc[x['BASIS'].eq('COMMON_VALID')]
        comparison = pd.concat([strict[['MONTH', 'BASE_INDEX']].assign(SERIES=self.region_name[dong]), strict[['MONTH', 'CITY_BASE_INDEX']].rename(columns={'CITY_BASE_INDEX': 'BASE_INDEX'}).assign(SERIES=f'{city} 전체')])
        self.line_plot(comparison, 'BASE_INDEX', 'SERIES', f'엄격 공통 위치 비교 — {self.months[0]}=100', '기준월 지수')
        self.line_plot(strict.melt(id_vars='MONTH', value_vars=['N_OBS', 'N_COMMON', 'N_STRICT'], var_name='기준', value_name='위치 수'), '위치 수', '기준', '관측 범위 확인', '위치 수')
        ages = self.change_series.loc[self.change_series['TABLE'].eq('flow_age_pop') & self.change_series['CITY_CODE'].eq(code) & self.change_series['DONG_CODE'].eq(dong) & self.change_series['LEVEL'].eq('DONG') & self.change_series['BASIS'].eq('COMMON_VALID') & self.change_series['VALUE_TYPE'].eq(kind) & self.change_series['METRIC'].isin(self.age_labels)].copy()
        print('연령 비교: 모든 연령에 같은 엄격 공통 유효 위치를 사용합니다.')
        display(ages.pivot(index='MONTH', columns='METRIC', values='VALUE').reindex(index=self.months, columns=self.age_labels))
        self.line_plot(ages, 'BASE_INDEX', 'METRIC', '제공된 전체 연령대 비교 — 기준월=100', '기준월 지수')
        display(ages.loc[ages['MONTH'].eq(self.months[-1]), ['METRIC', 'VALUE', 'BASE', 'BASE_PCT', 'CITY_BASE_PCT', 'BASE_MINUS_CITY_PP', 'N_VALID', 'N_DECREASE', 'MAX_STREAK']])
        geometry = self.boundaries.loc[self.boundaries['DONG_CODE'].eq(dong), 'geometry'].iloc[0]
        neighbors = self.boundaries.loc[self.boundaries['CITY_CODE'].eq(code) & self.boundaries.geometry.touches(geometry) & ~self.boundaries['DONG_CODE'].eq(dong), 'DONG_CODE'].tolist()
        print('경계가 접하는 주변 행정동:', [self.region_name.get(d, d) for d in neighbors])
        if neighbors:
            nearby = self.age_series(city, age, kind, 'COMMON_VALID')
            nearby = nearby.loc[nearby['LEVEL'].eq('DONG') & nearby['DONG_CODE'].isin([dong, *neighbors])]
            display(nearby.loc[nearby['MONTH'].eq(self.months[-1]), ['NAME', 'BASE_PCT', 'N_VALID', 'N_DECREASE', 'MAX_STREAK']])
            self.line_plot(nearby, 'BASE_INDEX', 'NAME', '주변 행정동과 기준월 지수 비교', '기준월 지수')
        print('월별 지역 인구 — 행정동·월당 한 행:')
        display(self.region_context.loc[self.region_context['DONG_CODE'].eq(dong)])
        print('정상 결합 행의 원본 문자열 표본 — 대표성 있는 표본은 아닙니다.')
        display(self.samples.loc[self.samples['TABLE'].eq('flow_age_pop') & self.samples['DONG_CODE'].eq(dong)].head(12))
        if show_time:
            self.time_profile(city, dong)

    def dashboard(self, city='강남구', age=None, month=None, kind='SUM', basis='COMMON_VALID', dong=None):
        if age is None:
            age = self.total_age_label
        if month is None:
            month = self.months[-1]
        if dong is None:
            dong = self.regions.loc[self.regions['CITY_CODE'].eq(self.city_codes[city]), 'DONG_CODE'].iloc[0]
        for view in ['수준', '전월 변화율', '감소 지속성']:
            self.analysis_map(city, age, month, kind, basis, view)
        current = self.age_series(city, age, kind, basis)
        print('선택 월 실제 수치:')
        display(current.loc[current['LEVEL'].eq('DONG') & current['MONTH'].eq(month), ['NAME', 'DONG_CODE', 'VALUE', 'MOM_PCT', 'BASE_PCT', 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_POOL', 'N_VALID', 'PREV', 'BASE', 'RATE_STATUS']].sort_values('VALUE', ascending=False))
        self.dong_detail(city, dong, age, kind, show_time=True)

    def show_cases(self):
        '기존 탐색 사례를 선택하여 표시한다.'
        self.case_city = '춘천시'
        self.case_age = self.total_age_label
        self.case_kind = 'SUM'
        self.cases = self.pick_cases(self.case_city, self.case_age, self.case_kind)
        if self.cases.empty:
            print('비교 가능한 사례가 없습니다. 위치 수·0 분모·결측을 먼저 확인하세요.')
        else:
            display(self.cases[['CASE', 'NAME', 'DONG_CODE', 'REASON', 'BASE', 'VALUE', 'BASE_PCT', 'CITY_BASE_PCT', 'BASE_MINUS_CITY_PP', 'N_OBS', 'N_COMMON', 'N_STRICT', 'N_VALID', 'N_PAIRS', 'N_DECREASE', 'MAX_STREAK']])
            if len(self.cases) < 3:
                print('현재 데이터에 감소/증가 사례가 모두 존재하지 않아 3개를 채우지 않았습니다.')
            self.age_candidates = self.change_series.loc[self.change_series['TABLE'].eq('flow_age_pop') & self.change_series['CITY_CODE'].eq(self.city_codes[self.case_city]) & self.change_series['LEVEL'].eq('DONG') & self.change_series['BASIS'].eq('COMMON_VALID') & self.change_series['VALUE_TYPE'].eq(self.case_kind) & self.change_series['MONTH'].eq(self.months[-1]) & self.change_series['METRIC'].isin(self.age_labels) & self.change_series['BASE_MINUS_CITY_PP'].notna()].copy()
            self.age_candidates['ABS_CITY_GAP'] = self.age_candidates['BASE_MINUS_CITY_PP'].abs()
            print('연령별 변화가 대상지역 전체와 다르게 나타나는 탐색 후보:')
            display(self.age_candidates.sort_values('ABS_CITY_GAP', ascending=False).head(12)[['NAME', 'METRIC', 'BASE', 'VALUE', 'BASE_PCT', 'CITY_BASE_PCT', 'BASE_MINUS_CITY_PP', 'N_VALID', 'N_DECREASE', 'MAX_STREAK']])
            for self.row in self.cases.to_dict('records'):
                print('\n선정 이유:', self.row['REASON'])
                self.dong_detail(self.case_city, self.row['DONG_CODE'], self.case_age, self.case_kind, show_time=True)
            print('\n발표용 관측 문장 — 실행 결과에 따라 작성:')
            for self.row in self.cases.to_dict('records'):
                print(f"- {self.case_city} {self.row['NAME']}의 {self.case_age} {VALUE_LABELS.get(self.case_kind, self.case_kind)}: 동일한 유효 위치 {int(self.row['N_VALID']):,}개에서 {self.months[0]} 대비 {self.months[-1]} 변화율 {self.row['BASE_PCT']:+.2f}%. 유효 비교 {int(self.row['N_PAIRS'])}구간 중 {int(self.row['N_DECREASE'])}구간 감소, 최장 연속 감소 {int(self.row['MAX_STREAK'])}구간. 대상지역 전체 변화율과의 차이는 {self.row['BASE_MINUS_CITY_PP']:+.2f}%p.")
        self.alternative_checks = pd.DataFrame({'추가 확인 항목': ['방학·학기 일정', '관광·방문 수요', '행사', '날씨', '상권·학교·시설 변화', '공급자 관측·집계 정책', '좌표계와 위치 의미'], '확인할 질문': ['비슷한 학교·대학 주변에서도 같은 월 변화가 나타나는가?', '관광지·주말/시간대 자료 및 외부 방문 통계와 연결되는가?', '실제 행사 기간과 변화 월이 겹치는가?', '해당 기간의 기온·강수 등의 자료와 일치하는가?', '시설 개폐·학사 일정·상권 변화 자료가 있는가?', '공통 위치에서도 추정 방식이나 관측 조건이 달라졌는가?', '공급자가 EPSG·단위·축·배율·점의 의미를 확인했는가?'], '현재 상태': ['추가 검증 필요'] * 7})
        display(self.alternative_checks)
        print('관측한 것은 제공 점 유동인구 값의 변화입니다.\n전체 위치와 공통 위치, 다른 연령대, 대상지역 전체를 비교했습니다.\n쉬었음 청년의 규모·확률, 고립 여부, 감소 원인, 개인의 상태는 결론 낼 수 없습니다.\nage와 time은 주변분포이므로 특정 연령대의 특정 시간대 활동을 주장할 수 없습니다.\n다음 질문: 공통 위치에서도 나타나는 연령별 변화가 외부 자료·다른 지역·추가 기간에서도 재현되는가?')

    def pick_cases(self, city, age=None, kind='SUM'):
        if age is None:
            age = self.total_age_label
        candidates = self.age_series(city, age, kind, 'COMMON_VALID')
        candidates = candidates.loc[candidates['LEVEL'].eq('DONG') & candidates['MONTH'].eq(self.months[-1]) & candidates['N_PAIRS'].eq(len(self.months) - 1) & candidates['BASE_PCT'].notna() & candidates['N_VALID'].gt(0)].copy()
        selected = []
        used = set()
        decline = candidates.loc[candidates['BASE_PCT'].lt(0)].sort_values(['MAX_STREAK', 'N_DECREASE', 'BASE_PCT'], ascending=[False, False, True])
        increase = candidates.loc[candidates['BASE_PCT'].gt(0)].sort_values('BASE_PCT', ascending=False)

        def add_one(frame, label, reason):
            remaining = frame.loc[~frame['DONG_CODE'].isin(used)]
            if not remaining.empty:
                row = remaining.iloc[0].copy()
                row['CASE'] = label
                row['REASON'] = reason
                selected.append(row)
                used.add(row['DONG_CODE'])
        add_one(decline, '감소 사례', '기준월보다 감소한 동 중 최장 연속 감소·감소 구간 수를 우선 비교')
        add_one(increase, '증가 사례', '기준월보다 증가한 동 중 기준월 대비 증가율이 큰 사례')
        small_change = candidates.assign(ABS_CHANGE=candidates['BASE_PCT'].abs()).sort_values('ABS_CHANGE')
        add_one(small_change, '변화폭 최소 사례', '남은 동 중 기준월 대비 변화율의 절댓값이 가장 작음; 유지 판정은 아님')
        return pd.DataFrame(selected)

    def show_controls(self):
        '버튼을 누를 때 지도와 표를 갱신한다.'
        try:
            import ipywidgets as widgets
        except ImportError:
            print('현재 커널에 ipywidgets가 없습니다. 아래 직접 호출 방식을 사용하세요.')
        else:
            self.city_widget = widgets.Dropdown(options=list(self.city_codes), value=self.case_city, description='대상지역')
            self.age_widget = widgets.Dropdown(options=[self.total_age_label, *self.age_labels], value=self.case_age, description='연령')
            self.month_widget = widgets.Dropdown(options=self.months, value=self.months[-1], description='월')
            self.kind_widget = widgets.Dropdown(options=[('제공 점 값 합계', 'SUM'), ('유효 위치당 평균', 'MEAN')], value=self.case_kind, description='지표')
            self.basis_widget = widgets.Dropdown(options=[(label, key) for key, label in self.basis_labels.items()], value='COMMON_VALID', description='집계 기준', layout=widgets.Layout(width='420px'))

            def dong_options(city):
                subset = self.regions.loc[self.regions['CITY_CODE'].eq(self.city_codes[city])]
                return list(zip(subset['NAME'], subset['DONG_CODE']))
            self.dong_widget = widgets.Dropdown(options=dong_options(self.case_city), description='행정동')
            self.button = widgets.Button(description='지도·표·추이 표시', button_style='primary')
            self.output = widgets.Output()

            def update_dongs(change):
                self.dong_widget.options = dong_options(change['new'])

            def render(_):
                with self.output:
                    clear_output(wait=True)
                    self.dashboard(city=self.city_widget.value, age=self.age_widget.value, month=self.month_widget.value, kind=self.kind_widget.value, basis=self.basis_widget.value, dong=self.dong_widget.value)
            self.city_widget.observe(update_dongs, names='value')
            self.button.on_click(render)
            display(widgets.VBox([widgets.HBox([self.city_widget, self.age_widget, self.month_widget]), widgets.HBox([self.kind_widget, self.dong_widget]), self.basis_widget, self.button, self.output]))

    def presentation_table(self):
        '발표용 지표 표를 표시한다.'
        self.presentation_city = self.presentation_city
        self.presentation_month = self.presentation_month
        self.presentation_basis = 'COMMON_VALID'
        self.selected_city_code = self.city_codes[self.presentation_city]
        self.age20 = self.get_metric('flow_age_pop', '20대', '20대_유동값')
        self.age_total = self.get_metric('flow_age_pop', self.total_age_label, '제공연령합계_유동값')
        self.time02 = self.get_metric('flow_time_pop', '02시', '02시_유동값')
        self.time_total = self.get_metric('flow_time_pop', self.total_time_label, '24시간_유동값합계')
        self.pop = self.region_context.loc[self.region_context['CITY_CODE'].eq(self.selected_city_code) & self.region_context['MONTH'].eq(self.presentation_month), ['DONG_CODE', 'NAME', 'RESIDENT_POP_TOTAL', 'RESIDENT_POP_20G']].copy()
        self.pop['RESIDENT_POP_TOTAL'] = pd.to_numeric(self.pop['RESIDENT_POP_TOTAL'], errors='coerce')
        self.ppt_summary = self.pop.merge(self.age20, on='DONG_CODE', how='left').merge(self.age_total, on='DONG_CODE', how='left').merge(self.time02, on='DONG_CODE', how='left').merge(self.time_total, on='DONG_CODE', how='left')
        self.ppt_summary['20대_유동구성비(%)'] = 100 * self.ppt_summary['20대_유동값'] / self.ppt_summary['제공연령합계_유동값']
        self.ppt_summary['02시_시간구성비(%)'] = 100 * self.ppt_summary['02시_유동값'] / self.ppt_summary['24시간_유동값합계']
        self.ppt_summary['상주인구대비_유동강도(참고)'] = self.ppt_summary['제공연령합계_유동값'] / self.ppt_summary['RESIDENT_POP_TOTAL'].where(self.ppt_summary['RESIDENT_POP_TOTAL'].gt(0))
        self.ppt_summary['20대주민수대비_유동강도(참고)'] = self.ppt_summary['20대_유동값'] / self.ppt_summary['RESIDENT_POP_20G'].where(self.ppt_summary['RESIDENT_POP_20G'].gt(0))
        self.ppt_summary = self.ppt_summary.rename(columns={'NAME': '행정동', 'RESIDENT_POP_TOTAL': '월말_주민등록총인구'})
        self.show_cols = ['행정동', '20대_유동값', '제공연령합계_유동값', '20대_유동구성비(%)', '02시_유동값', '24시간_유동값합계', '02시_시간구성비(%)', '월말_주민등록총인구', '상주인구대비_유동강도(참고)', '20대주민수대비_유동강도(참고)']
        display(self.ppt_summary[self.show_cols].sort_values('20대_유동구성비(%)', ascending=False).round(2).reset_index(drop=True))
        print(f'지역: {self.presentation_city} / 월: {self.presentation_month}')
        print('20대 구성비 = 성·연령 데이터 내부의 구성비')
        print('02시 구성비 = 시간대 데이터 내부의 구성비')
        print("두 값을 곱하거나 결합해 '02시의 20대'로 해석하면 안 됩니다.")
        print('상주인구 대비 유동강도는 확률이나 주민 외출률이 아닙니다.')

    def get_metric(self, table, metric, value_name):
        x = self.change_series.loc[self.change_series['TABLE'].eq(table) & self.change_series['LEVEL'].eq('DONG') & self.change_series['CITY_CODE'].eq(self.selected_city_code) & self.change_series['MONTH'].eq(self.presentation_month) & self.change_series['BASIS'].eq(self.presentation_basis) & self.change_series['VALUE_TYPE'].eq('SUM') & self.change_series['METRIC'].eq(metric), ['DONG_CODE', 'VALUE']].copy()
        return x.rename(columns={'VALUE': value_name})

    def share_map(self, city, month, table, numerator_metric, denominator_metric, title):
        city_code = self.city_codes[city]
        base = self.change_series.loc[self.change_series['TABLE'].eq(table) & self.change_series['LEVEL'].eq('DONG') & self.change_series['CITY_CODE'].eq(city_code) & self.change_series['MONTH'].eq(month) & self.change_series['BASIS'].eq('COMMON_VALID') & self.change_series['VALUE_TYPE'].eq('SUM') & self.change_series['METRIC'].isin([numerator_metric, denominator_metric]), ['DONG_CODE', 'METRIC', 'VALUE']].copy()
        values = base.pivot(index='DONG_CODE', columns='METRIC', values='VALUE').reset_index()
        values['비중'] = 100 * values[numerator_metric] / values[denominator_metric]
        context = self.region_context.loc[self.region_context['CITY_CODE'].eq(city_code) & self.region_context['MONTH'].eq(month), ['DONG_CODE', 'RESIDENT_POP_TOTAL']].copy()
        context['RESIDENT_POP_TOTAL'] = pd.to_numeric(context['RESIDENT_POP_TOTAL'], errors='coerce')
        values = values.merge(context, on='DONG_CODE', how='left', validate='one_to_one')
        geo = self.web_boundaries.loc[self.web_boundaries['CITY_CODE'].eq(city_code)].merge(values, on='DONG_CODE', how='left', validate='one_to_one')
        finite = geo['비중'].dropna()
        vmin = float(finite.min())
        vmax = float(finite.max())
        if vmin == vmax:
            vmax = vmin + 1
        cmap = LinearColormap(['#eff6ff', '#60a5fa', '#1d4ed8'], vmin=vmin, vmax=vmax)
        cmap.caption = title + ' (%)'
        geo['비중표시'] = geo['비중'].map(lambda x: '결측' if pd.isna(x) else f'{x:.2f}%')
        geo['주민등록총인구'] = geo['RESIDENT_POP_TOTAL'].map(lambda x: '결측' if pd.isna(x) else f'{x:,.0f}명')

        def style(feature):
            value = feature['properties'].get('비중')
            if value is None or not np.isfinite(value):
                fill = '#d1d5db'
            else:
                fill = cmap(value)
            return {'fillColor': fill, 'fillOpacity': 0.8, 'color': '#555555', 'weight': 0.8}
        m = folium.Map(tiles='OpenStreetMap', control_scale=True)
        bounds = geo.total_bounds
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
        folium.GeoJson(geo.to_json(), style_function=style, tooltip=folium.GeoJsonTooltip(fields=['NAME', '비중표시', numerator_metric, denominator_metric, '주민등록총인구'], aliases=['행정동', title, f'{numerator_metric} 유동값', f'{denominator_metric} 유동값', '해당 월말 주민등록 총인구'], localize=False)).add_to(m)
        cmap.add_to(m)
        print(f'{city} / {month} / {title}')
        print('색은 행정동별 구성비를 나타냅니다.')
        print('툴팁의 주민등록 총인구는 외부 지역 특성 정보이며 구성비 계산에는 사용하지 않았습니다.')
        return m

    def presentation_maps(self):
        '20대와 02시 구성비 지도를 고정 이름으로 저장한다.'
        self.figure_dir = self.result_directory / 'figures'
        self.figure_dir.mkdir(parents=True, exist_ok=True)
        self.youth_map = self.share_map(self.presentation_city, self.presentation_month, 'flow_age_pop', '20대', self.total_age_label, '20대 유동 구성비')
        self.youth_map.save(str(self.figure_dir / f'{self.presentation_city}_{self.presentation_month}_20대_유동구성비.html'))
        self.night_map = self.share_map(self.presentation_city, self.presentation_month, 'flow_time_pop', '02시', self.total_time_label, '02시 유동 구성비')
        self.night_map.save(str(self.figure_dir / f'{self.presentation_city}_{self.presentation_month}_02시_유동구성비.html'))
        display(self.youth_map)
        display(self.night_map)

    def population_intensities(self, level='DONG', basis='COMMON_VALID'):
        """총인구 기준과 같은 연령 주민 수 기준을 각각 계산한다."""
        from resident_population import AGES
        if level not in ['DONG','CITY'] or basis not in self.basis_labels:
            raise ValueError('지원하지 않는 지역 단위/위치 기준입니다.')
        population = self.region_context.copy()
        population_columns = ['RESIDENT_POP_TOTAL'] + [f'RESIDENT_POP_{a}' for a in AGES]
        keys = ['CITY_CODE','MONTH'] + (['DONG_CODE'] if level=='DONG' else [])
        if level=='CITY':
            population = population.groupby(keys,as_index=False)[population_columns].sum(min_count=1)
        age_population = population[keys+population_columns].melt(id_vars=keys+['RESIDENT_POP_TOTAL'],value_vars=[f'RESIDENT_POP_{a}' for a in AGES],var_name='AGE_CODE',value_name='AGE_RESIDENT_POP')
        age_population['METRIC'] = age_population['AGE_CODE'].map(dict(zip([f'RESIDENT_POP_{a}' for a in AGES],self.age_labels)))
        flow = self.change_series.loc[self.change_series['TABLE'].eq('flow_age_pop') & self.change_series['LEVEL'].eq(level) & self.change_series['BASIS'].eq(basis) & self.change_series['VALUE_TYPE'].eq('SUM') & self.change_series['METRIC'].isin(self.age_labels)].copy()
        result = flow.merge(age_population.drop(columns='AGE_CODE'),on=keys+['METRIC'],how='left',validate='one_to_one',indicator='_population_join')
        if not result['_population_join'].eq('both').all():
            raise ValueError('유동 지표에 연결되지 않은 행정동·월·연령 인구가 있습니다.')
        result = result.drop(columns='_population_join')
        result['FLOW_INTENSITY'] = result['VALUE'] / result['RESIDENT_POP_TOTAL'].where(result['RESIDENT_POP_TOTAL'].gt(0))
        result['AGE_FLOW_INTENSITY'] = result['VALUE'] / result['AGE_RESIDENT_POP'].where(result['AGE_RESIDENT_POP'].gt(0))
        result['POPULATION_RATE_STATUS'] = np.where(result['VALUE'].isna(),'CURRENT_MISSING',np.where(result['AGE_RESIDENT_POP'].gt(0),'CALCULATED','ZERO_RESIDENT_POPULATION'))
        return result

    def presentation_graph(self):
        '모든 제공 연령대의 기존 참고 그래프를 저장한다.'
        setup_korean_font()
        self.age_order = ['10대', '20대', '30대', '40대', '50대', '60대 이상']
        self.month_order = ['202507', '202508', '202509', '202510', '202511', '202512']
        self.month_label = {'202507': '7월', '202508': '8월', '202509': '9월', '202510': '10월', '202511': '11월', '202512': '12월'}
        self.target_cities = ['강남구', '춘천시']
        self.age_city = self.population_intensities(level='CITY')
        self.code_to_city = {v: k for k, v in self.city_codes.items()}
        self.age_city['CITY_NAME'] = self.age_city['CITY_CODE'].map(self.code_to_city)
        self.age_city['MONTH_LABEL'] = self.age_city['MONTH'].map(self.month_label)
        self.fig, self.axes = plt.subplots(2, 1, figsize=(7, 10), dpi=100, constrained_layout=True)
        for self.ax, self.city in zip(self.axes, self.target_cities):
            self.selected_city_code = self.city_codes[self.city]
            self.tmp = self.age_city.loc[self.age_city['CITY_CODE'].eq(self.selected_city_code)].copy()
            for self.age in self.age_order:
                self.s = self.tmp.loc[self.tmp['METRIC'].eq(self.age), ['MONTH', 'FLOW_INTENSITY']].set_index('MONTH').reindex(self.month_order)['FLOW_INTENSITY']
                self.ax.plot([self.month_label[m] for m in self.month_order], self.s.values, marker='o', linewidth=1.8, label=self.age)
            self.ax.set_title(f'{self.city} · 연령별 월별 유동강도', fontsize=12)
            self.ax.set_xlabel('월')
            self.ax.set_ylabel('유동강도\n(연령별 유동값 / 해당 월말 주민등록 총인구)')
            self.ax.grid(alpha=0.3)
            self.ax.legend(fontsize=8, ncol=3)
        self.fig.suptitle('강남구 / 춘천시 연령별 월별 유동 패턴 (데이터 특성 확인용)', fontsize=14)
        self.out_dir = Path(self.result_directory) / 'figures'
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.combined_path = self.out_dir / '강남_춘천_연령별_월별_유동강도.png'
        self.fig.savefig(self.combined_path, bbox_inches='tight', facecolor='white')
        display(self.fig)
        for ax in self.axes:
            ax.clear()
        for ax, city in zip(self.axes, self.target_cities):
            tmp = self.age_city.loc[self.age_city['CITY_NAME'].eq(city)]
            for age in self.age_order:
                values = tmp.loc[tmp['METRIC'].eq(age)].set_index('MONTH').reindex(self.month_order)['AGE_FLOW_INTENSITY']
                ax.plot([self.month_label[m] for m in self.month_order], values, marker='o', label=age)
            ax.set_title(city)
            ax.set_ylabel('연령별 유동값 / 같은 연령의 월말 주민 수')
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8,ncol=3)
        self.fig.suptitle('강남구 / 춘천시 연령별 주민 수 대비 유동강도 (참고)',fontsize=14)
        self.fig.savefig(self.out_dir / '강남_춘천_연령별_주민수대비_유동강도.png',bbox_inches='tight',facecolor='white')
        plt.show()
        print(f'[저장 완료] {self.combined_path}')
        print('※ 현재 그래프는 쉬었음 청년을 의미하는 것이 아닌 데이터 특성 확인용입니다.')
        print("※ '유동강도'는 상주인구 대비 참고 지표이며, 실제 외출률이나 개인 비율을 뜻하지 않습니다.")


    def export_results(self):
        """사람이 읽는 결과 CSV는 한글 헤더, 파일명은 고정한다."""
        from column_labels import korean_table
        output = self.result_directory / "exports"
        output.mkdir(exist_ok=True)
        self.resident_intensities = pd.concat([self.population_intensities(level=level) for level in ['DONG','CITY']],ignore_index=True)
        korean_table(self.resident_intensities).to_csv(output / '월별_주민수대비_유동강도.csv',index=False,encoding='utf-8-sig')
        korean_table(self.monthly_metrics).to_csv(output / "월별_유동수준.csv", index=False, encoding="utf-8-sig")
        korean_table(self.change_series).to_csv(output / "월별_유동변화.csv", index=False, encoding="utf-8-sig")
        print("한글 결과 저장:", output)

