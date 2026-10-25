"""P0: 완료된 공간 캐시를 재사용하는 로컬 지도·그래프 탐색 환경.

prepare는 새 공간 캐시만 작성한다. serve는 읽기 전용이며 준비/실험을 재실행하지 않는다.
"""
from pathlib import Path
import argparse
import contextlib
from functools import lru_cache
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import sys
import threading
from urllib.parse import parse_qs, urlparse

# 기존 plotting 코드가 사용자 홈에 캐시를 쓰지 않도록 로컬 런타임 경로 사용.
_runtime = Path(__file__).resolve().parent / 'outputs' / 'validation' / 'p0_runtime'
_runtime.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('MPLCONFIGDIR', str(_runtime / 'matplotlib'))

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from IPython.utils.capture import capture_output
from plotly.offline import get_plotlyjs
import plotly.graph_objects as go

from activity_analysis import ActivityExplorer
from workflow_io import read_json, read_csv, file_state, fingerprint, cache_valid, complete_state, write_json

VERSION = 'p0-1'
WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
OUTPUT = WORK / 'outputs' / 'canonical' / 'spatial_explorer'
TABLES = ['flow_age_pop', 'flow_time_pop', 'flow_wkdy_pop']
LABELS = {
    TABLES[0]: ['10대', '20대', '30대', '40대', '50대', '60대 이상'],
    TABLES[1]: [f'{h:02d}시' for h in range(24)],
    TABLES[2]: ['평일 평균', '주말 평균'],
}
TOTAL = {TABLES[0]: '제공 연령 합계', TABLES[1]: '24시간 값 합계'}
SIZES = [250, 500]
BASIS_LABELS = {'original': '자료별 기존 COMMON_VALID', 'joint': '세 자료 공통 유효 위치 (별도 비교용)'}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def finite(value):
    return float(value) if np.isfinite(value) else None


def changes(values):
    """6개월 축: 분모 0과 첫 달은 결측, 원래 값과 절대 변화는 보존."""
    values = np.asarray(values, dtype=float)
    prev = np.full_like(values, np.nan)
    prev[:, 1:] = values[:, :-1]
    base = np.broadcast_to(values[:, :1], values.shape)
    delta, base_delta = values - prev, values - base
    with np.errstate(divide='ignore', invalid='ignore'):
        mom = np.where(prev > 0, 100 * delta / prev, np.nan)
        rate = np.where(base > 0, 100 * base_delta / base, np.nan)
    run = np.zeros(len(values), dtype=int)
    below = run.copy()
    streak, below_streak = np.zeros_like(values), np.zeros_like(values)
    for i in range(values.shape[1]):
        run = np.where(values[:, i] < prev[:, i], run + 1, 0)
        below = np.where(values[:, i] < base[:, i], below + 1, 0)
        streak[:, i], below_streak[:, i] = run, below
    streak[~np.isfinite(values)] = np.nan
    below_streak[~np.isfinite(values)] = np.nan
    return dict(VALUE=values, PREV=prev, BASE=base, DELTA=delta, BASE_DELTA=base_delta,
                MOM_PCT=mom, BASE_PCT=rate, CURRENT_STREAK=streak, BELOW_BASE_STREAK=below_streak)


def input_states(explorer):
    canonical = explorer.result_directory
    paths = [WORK / 'activity_analysis.py',
             WORK / 'config.example.json', explorer.mapping_directory / 'manifest.json',
             explorer.mapping_directory / 'normalized_location_mapping.csv',
             canonical / 'duplicate_location_groups.csv', canonical / 'source_audit.json',
             canonical / 'attachment_cache.json']
    paths += [Path(p) for p in explorer.attachments['PATH']]
    paths += list((canonical / 'exports').glob('실험*.csv'))
    for level in ['sigungu', 'emd']:
        p = Path(explorer.config['boundaries'][level]['path'])
        paths += list(p.parent.glob(p.stem + '.*'))
    return [file_state(p) for p in sorted(set(paths)) if p.is_file()]


def load_position_geometry(explorer):
    """재매핑하지 않고 완료된 매핑의 정규화 키와 검토용 좌표를 연결한다."""
    keys = ['BLOCK_CD_KEY', 'X_COORD_KEY', 'Y_COORD_KEY']
    columns = keys + ['SIGUNGU_CD', 'EMD_CD', 'MATCH_STATUS', 'REVIEW_X_M', 'REVIEW_Y_M', 'LONGITUDE', 'LATITUDE']
    parts = []
    for chunk in read_csv(explorer.mapping_directory / 'normalized_location_mapping.csv', usecols=columns,
                          dtype={k: str for k in keys + ['SIGUNGU_CD', 'EMD_CD', 'MATCH_STATUS']}, chunksize=50000):
        parts.append(chunk.loc[chunk.MATCH_STATUS.eq('OK')].drop_duplicates(keys))
    geo = pd.concat(parts, ignore_index=True).drop_duplicates(keys)
    positions = explorer.position_identity.merge(geo, on=keys, validate='one_to_one')
    assignments = explorer.lookup[['POSITION_KEY', 'CITY_CODE', 'DONG_CODE']].drop_duplicates()
    assignments = assignments.loc[assignments.POSITION_KEY.isin(explorer.stable_positions)]
    positions = positions.merge(assignments, on='POSITION_KEY', validate='one_to_one')
    if not np.isfinite(positions[['REVIEW_X_M', 'REVIEW_Y_M', 'LONGITUDE', 'LATITUDE']]).all().all():
        raise ValueError('공간 캐시의 검토용 좌표에 결측/비정상 값이 있습니다.')
    return positions.sort_values('POSITION_KEY').reset_index(drop=True)


def build_grid(boundaries, size):
    """5179 임시 가정의 (0,0) 고정 원점 정사각형과 행정동의 교차 구획."""
    features = []
    for row in boundaries.itertuples():
        xmin, ymin, xmax, ymax = row.geometry.bounds
        xs = np.arange(int(np.floor(xmin / size)), int(np.floor(xmax / size)) + 1)
        ys = np.arange(int(np.floor(ymin / size)), int(np.floor(ymax / size)) + 1)
        ix, iy = np.meshgrid(xs, ys)
        ix, iy = ix.ravel(), iy.ravel()
        boxes = shapely.box(ix * size, iy * size, (ix + 1) * size, (iy + 1) * size)
        clipped = shapely.intersection(boxes, row.geometry)
        keep = shapely.area(clipped) > 0
        for x, y, geometry in zip(ix[keep], iy[keep], clipped[keep]):
            features.append(dict(UNIT_ID=f'{size}:{row.DONG_CODE}:{x}:{y}', CITY_CODE=row.CITY_CODE,
                                 DONG_CODE=row.DONG_CODE, NAME=f'{row.NAME} · {size}m [{x},{y}]', geometry=geometry))
    return gpd.GeoDataFrame(features, crs=5179)


def save_geo(frame, path):
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        stream.write(frame.to_crs(4326).to_json(drop_id=True))


def prepare(project_root=None):
    explorer = ActivityExplorer(project_root, load_population_context=False)
    if explorer.project_root != ROOT:
        raise ValueError('현재 코드가 속한 프로젝트 루트에서 실행하세요.')
    states = input_states(explorer)
    # VERSION은 공간 캐시 스키마/집계 규칙 버전. UI 편집은 수치 캐시를 재생성하지 않는다.
    key = fingerprint(dict(version=VERSION, inputs=states))
    manifest = OUTPUT / 'manifest.json'
    if cache_valid(read_json(manifest), key):
        print('P0 완료 캐시 재사용:', OUTPUT)
        return read_json(OUTPUT / 'coverage.json')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json(manifest, dict(status='building', fingerprint=key))
    # 이미 검증된 요일 정의·필드 및 기존 위치 읽기/결측·중복 정책을 재사용한다.
    definition = read_json(WORK / 'outputs' / 'validation' / 'weekday_experiment_validation.json')
    fields = [f'FLOW_POP_CNT_{d}' for d in ['MON', 'TUS', 'WED', 'THU', 'FRI', 'SAT', 'SUN']]
    if definition.get('status') != 'passed' or definition.get('definition', {}).get('fields') != fields:
        raise ValueError('실험 ④의 검증된 요일 정의 기록이 필요합니다.')
    if explorer.table_cfg[TABLES[2]]['population_columns'] != fields:
        raise ValueError('요일 필드가 기존 검증 기록과 다릅니다.')
    explorer.weekday_fields, explorer.weekday_labels = fields, ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
    explorer.raw_fields[TABLES[2]] = fields
    explorer.read_locations()
    regions = explorer.existing[['SIGUNGU_CD', 'SIGUNGU_NM', 'EMD_CD', 'EMD_NM']].drop_duplicates()
    explorer.regions = regions.rename(columns={'SIGUNGU_CD': 'CITY_CODE', 'SIGUNGU_NM': 'CITY', 'EMD_CD': 'DONG_CODE', 'EMD_NM': 'NAME'})
    explorer.region_name = explorer.regions.set_index('DONG_CODE').NAME.to_dict()
    explorer.load_boundaries(include_population_context=False)
    positions = load_position_geometry(explorer)
    strict = {}
    for table, data in explorer.location_data.items():
        counts = data.groupby('POSITION_KEY').MONTH.nunique()
        common = set(counts.index[counts.eq(len(explorer.months))]) & explorer.stable_positions
        valid_counts = data.loc[data.POSITION_KEY.isin(common) & data.ROW_COMPLETE].groupby('POSITION_KEY').MONTH.nunique()
        strict[table] = set(valid_counts.index[valid_counts.eq(len(explorer.months))])
    joint = set.intersection(*strict.values())
    positions['JOINT'] = positions.POSITION_KEY.isin(joint)
    for table in TABLES:
        positions[table] = positions.POSITION_KEY.isin(strict[table])
    files, grid_diagnostics = [], []
    dong_geo = explorer.boundaries[['CITY_CODE', 'DONG_CODE', 'NAME', 'geometry']].copy()
    dong_geo['UNIT_ID'] = dong_geo.DONG_CODE
    p = OUTPUT / 'dongs.geojson.gz'
    save_geo(dong_geo, p)
    files.append(p)
    for size in SIZES:
        print('새 분석 격자 구성:', size, flush=True)
        grids = build_grid(dong_geo, size)
        x = np.floor(positions.REVIEW_X_M / size).astype(int).astype(str)
        y = np.floor(positions.REVIEW_Y_M / size).astype(int).astype(str)
        column = f'GRID_{size}'
        positions[column] = str(size) + ':' + positions.DONG_CODE + ':' + x + ':' + y
        if not positions[column].isin(grids.UNIT_ID).all():
            raise ValueError('매핑 위치의 격자가 행정동 교차 구획에 없습니다.')
        # 새 구획이 실제로 기존 점을 포함하는지 검사. 경계 접촉 허용 오차 1cm.
        ordered_geo = grids.set_index('UNIT_ID').geometry.reindex(positions[column]).to_numpy()
        points = shapely.points(positions.REVIEW_X_M, positions.REVIEW_Y_M)
        if not shapely.dwithin(ordered_geo, points, .01).all():
            raise ValueError('기존 점 배정과 새 구획의 기하가 일치하지 않습니다.')
        counts = positions.groupby(column).size()
        grids['N_OBS_UNION'] = grids.UNIT_ID.map(counts).fillna(0).astype(int)
        for city, selected in grids.groupby('CITY_CODE'):
            occupied = selected.N_OBS_UNION[selected.N_OBS_UNION.gt(0)]
            row = dict(CITY_CODE=city, CITY=explorer.city_names[city], SIZE_M=size,
                       N_CELLS=len(selected), N_EMPTY=int(selected.N_OBS_UNION.eq(0).sum()),
                       EMPTY_PCT=100 * selected.N_OBS_UNION.eq(0).mean(),
                       SPARSE_LE2_PCT=100 * selected.N_OBS_UNION.le(2).mean(),
                       OCCUPIED_MEDIAN=float(occupied.median()), OCCUPIED_P10=float(occupied.quantile(.1)),
                       OCCUPIED_P90=float(occupied.quantile(.9)), MAX_POSITIONS=int(occupied.max()))
            for table in TABLES:
                cohort_counts = positions.loc[positions[table] & positions.CITY_CODE.eq(city)].groupby(column).size()
                row[table + '_EMPTY_PCT'] = 100 * (1 - selected.UNIT_ID.isin(cohort_counts.index).mean())
            grid_diagnostics.append(row)
        p = OUTPUT / f'grid_{size}.geojson.gz'
        save_geo(grids, p)
        files.append(p)
    coverage = []
    for level, group in [('CITY', 'CITY_CODE'), ('DONG', 'DONG_CODE')]:
        for code, subset in positions.groupby(group):
            row = dict(LEVEL=level, CITY_CODE=subset.CITY_CODE.iloc[0], DONG_CODE=code if level == 'DONG' else '__CITY__',
                       NAME=explorer.region_name.get(code, explorer.city_names.get(code)), N_OBS_UNION=len(subset),
                       N_JOINT=int(subset.JOINT.sum()))
            for table in TABLES:
                n = int(subset[table].sum())
                row[table] = n
                row[table + '_JOINT_PCT'] = 100 * row['N_JOINT'] / n if n else None
                row[table + '_EXCLUDED'] = n - row['N_JOINT']
            coverage.append(row)
    recommended = {}
    for city in explorer.city_names:
        a, b = [next(r for r in grid_diagnostics if r['CITY_CODE'] == city and r['SIZE_M'] == size) for size in SIZES]
        # 탐색 가독성 기준이며 위축/위험 임계값이나 자료 제외 기준이 아니다.
        recommended[city] = 250 if a['OCCUPIED_MEDIAN'] >= 5 and a['SPARSE_LE2_PCT'] <= 50 else 500
    checks = []
    references = {TABLES[0]: '실험2_연령대별_월간유동변화.csv', TABLES[1]: '실험3_시간대별_월간유동변화.csv', TABLES[2]: '실험4_평일주말_월간유동변화.csv'}
    for table in TABLES:
        labels = LABELS[table]
        data = explorer.location_data[table]
        data = data.loc[data.POSITION_KEY.isin(strict[table])].sort_values(['POSITION_KEY', 'MONTH'])
        ids = data.POSITION_KEY.drop_duplicates().to_numpy(dtype=np.int32)
        cube = data[labels].to_numpy(dtype=float).reshape(len(ids), len(explorer.months), len(labels))
        if not np.isfinite(cube).all() or not data.groupby('POSITION_KEY').MONTH.nunique().eq(6).all():
            raise ValueError('기존 COMMON_VALID 위치의 6개월 값이 불완전합니다.')
        p = OUTPUT / f'{table}.npz'
        np.savez_compressed(p, ids=ids, values=cube)
        files.append(p)
        # 새 격자 합계를 상위로 다시 합산해 기존 실험 CSV와 대조한다.
        reference = read_csv(explorer.result_directory / 'exports' / references[table], dtype={'분석 기준년월': str, '분석 행정동 코드': str})
        for size in SIZES:
            connected = data.merge(positions[['POSITION_KEY', f'GRID_{size}']], on='POSITION_KEY', validate='many_to_one')
            grouped = connected.groupby(['DONG_CODE', f'GRID_{size}', 'MONTH'])[labels].sum(min_count=1)
            parent = grouped.groupby(level=['DONG_CODE', 'MONTH']).sum(min_count=1)
            max_diff = 0.0
            for label in labels:
                ref = reference.loc[reference['집계 지역 단위'].eq('행정동') & reference['지표'].eq(label)]
                old = ref.set_index(['분석 행정동 코드', '분석 기준년월'])['유동 수준']
                current = parent[label].reindex(old.index)
                if not current.isna().eq(old.isna()).all() or not np.allclose(current, old, atol=1e-7, rtol=1e-10, equal_nan=True):
                    raise ValueError(f'{table}/{size}/{label}: 새 격자→행정동 합계가 기존 CSV와 다릅니다.')
                max_diff = max(max_diff, float((current - old).abs().max()))
            n = data.groupby('DONG_CODE').POSITION_KEY.nunique()
            old_n = reference.loc[reference['집계 지역 단위'].eq('행정동')].groupby('분석 행정동 코드')['선택 집계 위치 수'].first()
            if not n.reindex(old_n.index, fill_value=0).eq(old_n).all():
                raise ValueError('기존 위치 수와 다릅니다.')
            checks.append(dict(TABLE=table, GRID_SIZE=size, MAX_PARENT_DIFFERENCE=max_diff, N_FIXED=len(ids)))
    positions = positions.drop(columns=['SIGUNGU_CD', 'EMD_CD', 'MATCH_STATUS'])
    p = OUTPUT / 'positions.csv.gz'
    positions.to_csv(p, index=False, encoding='utf-8', compression='gzip')
    files.append(p)
    for name, rows in [('coverage.csv', coverage), ('grid_diagnostics.csv', grid_diagnostics)]:
        p = OUTPUT / name
        pd.DataFrame(rows).to_csv(p, index=False, encoding='utf-8-sig')
        files.append(p)
    summary = dict(months=explorer.months, city_names=explorer.city_names,
                   dongs=explorer.regions.to_dict('records'), crs_status='ASSUMED_UNVERIFIED',
                   boundary_date='2025-06-30', grid_origin=[0, 0], sizes=SIZES, recommended=recommended,
                   grid_definition='정사각형 격자와 행정동의 교차 구획. 통신사 격자/생활권 경계 아님.',
                   n_joint=len(joint), n_union=len(positions), coverage=coverage,
                   grid_diagnostics=grid_diagnostics, parent_checks=checks,
                   same_xy_extra_keys=int(positions.duplicated(['X_COORD_KEY', 'Y_COORD_KEY']).sum()),
                   sources=[str(explorer.result_directory / 'exports' / references[t]) for t in TABLES])
    p = OUTPUT / 'coverage.json'
    write_json(p, summary)
    files.append(p)
    if states != input_states(explorer):
        raise ValueError('준비 중 기존 입력/실험 결과가 바뀌었습니다.')
    complete_state(manifest, key, files, inputs=states, version=VERSION, crs_status='ASSUMED_UNVERIFIED')
    print('P0 새 공간 캐시 완료:', OUTPUT, '/ 세 자료 공통:', len(joint), flush=True)
    return summary


class SpatialExplorer:
    def __init__(self, output=OUTPUT):
        self.output = Path(output)
        state = read_json(self.output / 'manifest.json')
        if not cache_valid(state, state.get('fingerprint')):
            raise ValueError('완료된 P0 공간 캐시가 필요합니다. --prepare를 먼저 실행하세요.')
        if state.get('version') != VERSION or any(file_state(r['path']) != r for r in state['inputs']):
            raise ValueError('기존 입력/코드/결과가 바뀌었습니다. --prepare로 새 공간 캐시를 갱신하세요.')
        self.meta = read_json(self.output / 'coverage.json')
        self.positions = pd.read_csv(self.output / 'positions.csv.gz', dtype={k: str for k in ['CITY_CODE', 'DONG_CODE', 'BLOCK_CD_KEY', 'X_COORD_KEY', 'Y_COORD_KEY', 'GRID_250', 'GRID_500']})
        self.positions = self.positions.set_index('POSITION_KEY', drop=False)
        self.tables = {}
        for table in TABLES:
            with np.load(self.output / f'{table}.npz', allow_pickle=False) as data:
                self.tables[table] = dict(ids=data['ids'], values=data['values'])
        self.geometry = {}
        for level in ['dongs', 'grid_250', 'grid_500']:
            with gzip.open(self.output / f'{level}.geojson.gz', 'rt', encoding='utf-8') as stream:
                self.geometry[level] = json.load(stream)['features']

    @lru_cache(maxsize=12)
    def grouped(self, table, basis, level, size):
        data = self.tables[table]
        p = self.positions.loc[data['ids']]
        keep = p.JOINT.to_numpy() if basis == 'joint' else np.ones(len(p), dtype=bool)
        p, cube = p.loc[keep], data['values'][keep]
        column = {'CITY': 'CITY_CODE', 'DONG': 'DONG_CODE', 'GRID': f'GRID_{size}', 'POINT': 'POSITION_KEY'}[level]
        units, inverse = np.unique(p[column].astype(str), return_inverse=True)
        n = np.bincount(inverse, minlength=len(units))
        sums = np.empty((len(units), 6, cube.shape[2]))
        for m in range(6):
            for k in range(cube.shape[2]):
                sums[:, m, k] = np.bincount(inverse, weights=cube[:, m, k], minlength=len(units))
        representatives = p.drop_duplicates(column).set_index(p.drop_duplicates(column)[column].astype(str)).reindex(units)
        return dict(units=units, n=n, values=sums, city=representatives.CITY_CODE.to_numpy(), dong=representatives.DONG_CODE.to_numpy())

    def derived(self, grouped, table, metric, view, compare, scale='full'):
        cube = grouped['values']
        values = cube.sum(axis=2) if metric == TOTAL.get(table) else cube[:, :, LABELS[table].index(metric)]
        result = changes(values)
        delta = result['BASE_DELTA' if compare == 'base' else 'DELTA']
        result['DISPLAY_DELTA'] = delta
        rate = result['BASE_PCT' if compare == 'base' else 'MOM_PCT']
        unit = '제공 위치 값 합계'
        shown = values
        if view == 'PCT':
            shown, unit = rate, '%'
        elif view == 'DELTA':
            shown = delta
        elif view in ['CURRENT_STREAK', 'BELOW_BASE_STREAK']:
            shown, unit = result[view], '구간 수' if view == 'CURRENT_STREAK' else '개월 수'
        elif view in ['SHARE', 'SHARE_DELTA']:
            if table not in TOTAL or metric == TOTAL[table]:
                raise ValueError('구성비는 연령 또는 개별 시간대를 선택하세요.')
            total = cube.sum(axis=2)
            with np.errstate(divide='ignore', invalid='ignore'):
                share = np.where(total > 0, 100 * values / total, np.nan)
            shown, unit = share, '%'
            s = changes(share)
            result['DISPLAY_DELTA'] = s['BASE_DELTA' if compare == 'base' else 'DELTA']
            if view == 'SHARE_DELTA':
                shown, unit = s['BASE_DELTA' if compare == 'base' else 'DELTA'], '%p'
        elif view in ['RATIO', 'RATIO_DELTA']:
            if table != TABLES[2]:
                raise ValueError('주말/평일 비율은 실험 ④에서만 제공합니다.')
            with np.errstate(divide='ignore', invalid='ignore'):
                ratio = np.where(cube[:, :, 0] > 0, cube[:, :, 1] / cube[:, :, 0], np.nan)
            shown, unit = ratio, '배'
            r = changes(ratio)
            result['DISPLAY_DELTA'] = r['BASE_DELTA' if compare == 'base' else 'DELTA']
            if view == 'RATIO_DELTA':
                shown = r['BASE_DELTA' if compare == 'base' else 'DELTA']
        elif view != 'VALUE':
            raise ValueError('지원하지 않는 지도 표시 지표입니다.')
        # 색 범위는 동일 자료/지표/단위의 두 도시 전체 6개월에 고정한다.
        valid = shown[np.isfinite(shown)]
        bound = max(float(np.abs(valid).max()), 1e-12) if len(valid) else 1
        positive = view in ['VALUE', 'SHARE', 'RATIO', 'CURRENT_STREAK', 'BELOW_BASE_STREAK']
        domain = [0, bound] if positive else [-bound, bound]
        if view == 'SHARE':
            domain = [0, 100]
        if view == 'SHARE_DELTA':
            domain = [-100, 100]
        if view in ['CURRENT_STREAK', 'BELOW_BASE_STREAK']:
            domain = [0, 5]
        result['COLOR_FULL_DOMAIN'] = domain.copy()
        if scale == 'robust' and view in ['VALUE', 'PCT', 'DELTA', 'RATIO', 'RATIO_DELTA'] and len(valid):
            visual_bound = max(float(np.quantile(np.abs(valid), .98)), 1e-12)
            domain = [0, visual_bound] if positive else [-visual_bound, visual_bound]
        return result, shown, unit, domain

    def validate_query(self, q):
        allowed = dict(table=TABLES, basis=list(BASIS_LABELS), level=['DONG', 'GRID', 'POINT'],
                       size=['250', '500'], city=list(self.meta['city_names']), month=self.meta['months'],
                       compare=['base', 'mom'], scale=['full', 'robust'], view=['VALUE', 'PCT', 'DELTA', 'CURRENT_STREAK', 'BELOW_BASE_STREAK', 'SHARE', 'SHARE_DELTA', 'RATIO', 'RATIO_DELTA'])
        defaults = dict(table=TABLES[0], basis='original', level='DONG', size='250', city='11230', month=self.meta['months'][-1], compare='base', view='PCT', dong='', grid='', metric=TOTAL[TABLES[0]], selected='', scale='full')
        q = {**defaults, **q}
        for key, values in allowed.items():
            if q[key] not in values:
                raise ValueError(f'잘못된 선택: {key}')
        if q['metric'] not in LABELS[q['table']] + ([TOTAL[q['table']]] if q['table'] in TOTAL else []):
            raise ValueError('해당 자료에 없는 지표입니다.')
        if q['dong'] and not any(r['DONG_CODE'] == q['dong'] and r['CITY_CODE'] == q['city'] for r in self.meta['dongs']):
            raise ValueError('선택한 도시의 행정동이 아닙니다.')
        if q['level'] == 'POINT' and not (q['grid'] or q['dong']):
            raise ValueError('개별 위치는 행정동 또는 격자를 먼저 선택하세요.')
        return q

    def catalog(self):
        return {**self.meta, 'basis_labels': BASIS_LABELS, 'metrics': {t: ([TOTAL[t]] if t in TOTAL else []) + LABELS[t] for t in TABLES}}

    def map_data(self, query):
        q = self.validate_query(query)
        table, level, size = q['table'], q['level'], int(q['size'])
        g = self.grouped(table, q['basis'], level, size)
        values, shown, unit, domain = self.derived(g, table, q['metric'], q['view'], q['compare'], q['scale'])
        mi = self.meta['months'].index(q['month'])
        index = {key: i for i, key in enumerate(g['units'])}
        if level == 'POINT':
            selected = self.positions.loc[self.positions.CITY_CODE.eq(q['city'])]
            selected = selected.loc[selected[f'GRID_{size}'].eq(q['grid'])] if q['grid'] else selected.loc[selected.DONG_CODE.eq(q['dong'])]
            if len(selected) > 2000:
                raise ValueError('관측 위치가 2,000개를 넘습니다. 격자를 선택해 범위를 줄이세요.')
            features = [dict(type='Feature', geometry=dict(type='Point', coordinates=[r.LONGITUDE, r.LATITUDE]),
                             properties=dict(UNIT_ID=str(r.POSITION_KEY), DONG_CODE=r.DONG_CODE, NAME=f'관측 위치 {r.POSITION_KEY}',
                                             CITY_CODE=r.CITY_CODE, BLOCK_CD_KEY=r.BLOCK_CD_KEY)) for r in selected.itertuples()]
        else:
            features = self.geometry['dongs' if level == 'DONG' else f'grid_{size}']
            features = [f for f in features if f['properties']['CITY_CODE'] == q['city'] and (not q['dong'] or f['properties']['DONG_CODE'] == q['dong'])]
        out = []
        for feature in features:
            props = dict(feature['properties'])
            i = index.get(props['UNIT_ID'])
            n = int(g['n'][i]) if i is not None else 0
            value = finite(shown[i, mi]) if i is not None else None
            delta = finite(values['BASE_DELTA' if q['compare'] == 'base' else 'DELTA'][i, mi]) if i is not None else None
            display_delta = finite(values['DISPLAY_DELTA'][i, mi]) if i is not None else None
            kind = '구성비' if q['view'].startswith('SHARE') else '비율' if q['view'].startswith('RATIO') else '유동'
            state = '자료 부족' if n == 0 else '비교 불가' if value is None or display_delta is None else '변화 없음' if display_delta == 0 else kind + ' 증가' if display_delta > 0 else kind + ' 감소'
            observed = props.get('N_OBS_UNION', 1 if level == 'POINT' else next(r['N_OBS_UNION'] for r in self.meta['coverage'] if r['DONG_CODE'] == props['DONG_CODE'] and r['LEVEL'] == 'DONG'))
            props.update(n=n, n_observed=int(observed), state=state, value=value, delta=delta,
                         direction_unit='%p' if kind == '구성비' else '배' if kind == '비율' else '제공 위치 값 합계',
                         display_delta=display_delta,
                         raw=finite(values['VALUE'][i, mi]) if i is not None else None,
                         previous=finite(values['PREV'][i, mi]) if i is not None else None,
                         base=finite(values['BASE'][i, mi]) if i is not None else None,
                         trend=[finite(v) for v in values['VALUE'][i]] if i is not None else [None] * 6)
            out.append(dict(type='Feature', geometry=feature['geometry'], properties=props))
        return dict(type='FeatureCollection', features=out, unit=unit, domain=domain, full_domain=values['COLOR_FULL_DOMAIN'],
                    n_saturated=sum(abs(f['properties']['value']) > domain[1] for f in out if f['properties']['value'] is not None),
                    positive=q['view'] in ['VALUE', 'SHARE', 'RATIO', 'CURRENT_STREAK', 'BELOW_BASE_STREAK'],
                    scope='동일 자료·선택 지표·공간 단위·위치 기준 / 강남·춘천 전체 6개월 공통 범례', q=q)

    def series(self, table, basis, level, size, selected):
        g = self.grouped(table, basis, level, size)
        matching = np.flatnonzero(g['units'] == selected)
        if not len(matching):
            return None
        i = matching[0]
        return dict(n=int(g['n'][i]), values=g['values'][i], city=g['city'][i], dong=g['dong'][i])

    def detail(self, query):
        q = self.validate_query(query)
        selected, level, table, size = q['selected'], q['level'], q['table'], int(q['size'])
        # 동일 요청 범위 밖의 선택은 받아들이지 않는다.
        allowed = {f['properties']['UNIT_ID'] for f in self.map_data(q)['features']}
        if selected not in allowed:
            raise ValueError('현재 지도 범위에 없는 공간입니다.')
        s = self.series(table, q['basis'], level, size, selected)
        if s is None:
            return dict(selected=selected, n=0, figures=[], message='선택 위치 기준의 유효 위치가 없습니다. 결측을 0으로 표시하지 않습니다.')
        groups = [('선택 공간', s)]
        if level != 'DONG':
            groups.append(('상위 행정동', self.series(table, q['basis'], 'DONG', size, s['dong'])))
        groups.append(('시군구', self.series(table, q['basis'], 'CITY', size, s['city'])))
        rows = []
        figures = []
        for label, series in groups:
            if series is None:
                continue
            values = series['values'].sum(axis=1) if q['metric'] == TOTAL.get(table) else series['values'][:, LABELS[table].index(q['metric'])]
            c = changes(values[None])
            for m, month in enumerate(self.meta['months']):
                rows.append(dict(MONTH=month, GROUP=f"{label} ({series['n']:,}위치)", VALUE=values[m], BASE_PCT=c['BASE_PCT'][0, m], MOM_PCT=c['MOM_PCT'][0, m]))
        plotter = ActivityExplorer.__new__(ActivityExplorer)
        frame = pd.DataFrame(rows)
        for field, title, unit in [('VALUE', '선택 공간·상위 공간의 원래 수준', '제공 위치 값 합계'),
                                   ('BASE_PCT', '7월 대비 변화율 · 상위 공간 비교', '%'), ('MOM_PCT', '전월 대비 변화율 · 상위 공간 비교', '%')]:
            with capture_output():
                fig = plotter.line_plot(frame, field, 'GROUP', title, unit)
            figures.append(json.loads(fig.to_json()))
        cube = s['values']
        profile = pd.DataFrame([dict(MONTH=month, METRIC=metric, VALUE=cube[m, i]) for m, month in enumerate(self.meta['months']) for i, metric in enumerate(LABELS[table])])
        with capture_output():
            fig = plotter.line_plot(profile, 'VALUE', 'METRIC', '선택 공간 · 연령/시간대/요일 수준', '제공 위치 값 합계')
        figures.append(json.loads(fig.to_json()))
        if table in TOTAL:
            total = cube.sum(axis=1)
            with np.errstate(divide='ignore', invalid='ignore'):
                share = np.where(total[:, None] > 0, 100 * cube / total[:, None], np.nan)
            profile['SHARE'] = share.ravel()
            with capture_output():
                fig = plotter.line_plot(profile, 'SHARE', 'METRIC', '선택 공간 · 구성비 추이', '%')
            figures.append(json.loads(fig.to_json()))
            if table == TABLES[1]:
                # 기존 실험 ③의 시간대 프로필·구성비 히트맵·대표 시간대 추이를 재사용.
                plotter.months = self.meta['months']
                plotter.city_codes = self.meta['city_names'] | {v: k for k, v in self.meta['city_names'].items()}
                plotter.region_name = {s['dong']: '선택 공간'}
                source = profile.assign(TABLE=table, CITY_CODE=s['city'], DONG_CODE=s['dong'], LEVEL='DONG', BASIS='COMMON_VALID', VALUE_TYPE='SUM')
                existing = plotter.time_profile(self.meta['city_names'][s['city']], s['dong'], source=source, show=False)
                existing['shares'].update_traces(zmin=0, zmax=100)
                for name in ['profile', 'shares', 'trends']:
                    figures.append(json.loads(existing[name].to_json()))
        else:
            with np.errstate(divide='ignore', invalid='ignore'):
                ratio = np.where(cube[:, 0] > 0, cube[:, 1] / cube[:, 0], np.nan)
            with capture_output():
                fig = plotter.line_plot(pd.DataFrame(dict(MONTH=self.meta['months'], VALUE=ratio, GROUP='주말/평일')), 'VALUE', 'GROUP', '선택 공간 · 주말/평일 비율', '배')
            figures.append(json.loads(fig.to_json()))
        comparison = self.map_data(q)
        p = next(f['properties'] for f in comparison['features'] if f['properties']['UNIT_ID'] == selected)
        identity = dict(selected=selected, dong=s['dong'], city=s['city'], basis=BASIS_LABELS[q['basis']], n=s['n'])
        if level == 'POINT':
            identity.update(self.positions.loc[int(selected), ['BLOCK_CD_KEY', 'X_COORD_KEY', 'Y_COORD_KEY']].to_dict())
        return dict(**identity, figures=figures, properties=p, months=self.meta['months'],
                    message='집계 값은 고유 방문자 수가 아닙니다. 같은 공간의 지표 중첩은 동일 개인·연령×시간×요일 행동을 뜻하지 않습니다.')


def page_html():
    m = folium.Map(location=[37.51, 127.05], zoom_start=12, tiles='OpenStreetMap', prefer_canvas=True)
    rendered = m.get_root().render()
    # Folium이 생성한 지도·기존 Leaflet 자원을 그대로 사용한다.
    header = rendered.split('<head>')[1].split('</head>')[0]
    body = rendered.split('<body>')[1].split('</body>')[0]
    script = rendered.rsplit('<script>', 1)[1].split('</script>')[0]
    template = (WORK / 'spatial_explorer.html').read_text(encoding='utf-8')
    return template.replace('__FOLIUM_HEAD__', header).replace('__FOLIUM_BODY__', body).replace('__FOLIUM_SCRIPT__', script).replace('__MAP_NAME__', m.get_name()).encode('utf-8')


class BoundedLocalServer(ThreadingHTTPServer):
    """Bound concurrent sockets and stalled local request bodies."""
    daemon_threads = True
    max_workers = 8
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(self.max_workers)
        super().__init__(*args,**kwargs)
    def get_request(self):
        request,address=super().get_request();request.settimeout(10)
        return request,address
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            body=b'{"error":{"code":"SERVER_BUSY","message":"Server busy; retry later"}}'
            # Drain a bounded header chunk so Windows does not reset a normal GET
            # connection before the client can read our 503 response.
            try:
                request.settimeout(.25)
                request.recv(16384)
            except OSError:pass
            try:request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            except OSError:pass
            self.shutdown_request(request);return
        try:super().process_request(request,address)
        except Exception:
            self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()
    def handle_error(self,request,address):
        # Never print tracebacks or request bodies from untrusted clients.
        pass


def make_server(port=8766, app=None, service=None, agent=None):
    from dashboard_service import DashboardService
    from roa_agent import RoaAgent
    app = app or SpatialExplorer()
    service = service or DashboardService(app.geometry['dongs'])
    agent = agent or RoaAgent(service)
    plotly = get_plotlyjs().encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # 담당자 질문·검색어·환경변수는 로그에 기록하지 않는다.
            pass

        def respond(self, body, mime='application/json; charset=utf-8', status=200):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'private, max-age=3600' if self.path == '/plotly.js' else 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
            try:
                if parsed.path in ['/', '/index.html']:
                    body, mime = page_html(), 'text/html; charset=utf-8'
                elif parsed.path == '/plotly.js':
                    body, mime = plotly, 'text/javascript; charset=utf-8'
                elif parsed.path in {'/dashboard.css', '/dashboard.js', '/roa_dashboard.js',
                                     '/welfare_explorer.js', '/activity_explorer.js'}:
                    # Explicit UI allowlist; never expose arbitrary workspace files.
                    body = (WORK / parsed.path[1:]).read_bytes()
                    mime = ('text/css' if parsed.path.endswith('.css') else 'text/javascript') + '; charset=utf-8'
                elif parsed.path.startswith('/api/roa/'):
                    if any(len(v) != 1 for v in parse_qs(parsed.query, keep_blank_values=True).values()):
                        raise ValueError('중복 조회 인자')
                    body, mime = json_bytes(service.get(parsed.path, q)), 'application/json; charset=utf-8'
                elif parsed.path == '/api/agent/status':
                    body, mime = json_bytes(agent.provider.status()), 'application/json; charset=utf-8'
                elif parsed.path == '/api/service/status':
                    body, mime = json_bytes(service.external_status()), 'application/json; charset=utf-8'
                elif parsed.path == '/api/catalog':
                    body, mime = json_bytes(app.catalog()), 'application/json; charset=utf-8'
                elif parsed.path == '/api/map':
                    body, mime = json_bytes(app.map_data(q)), 'application/json; charset=utf-8'
                elif parsed.path == '/api/detail':
                    body, mime = json_bytes(app.detail(q)), 'application/json; charset=utf-8'
                else:
                    self.send_error(404)
                    return
                status = 200
            except (ValueError, KeyError) as error:
                detail = dict(code='INVALID_REQUEST', message=str(error)) if parsed.path.startswith('/api/roa/') else str(error)
                body, mime, status = json_bytes(dict(error=detail)), 'application/json; charset=utf-8', 400
            except Exception:
                body, mime, status = json_bytes(dict(error=dict(code='DATA_UNAVAILABLE', message='데이터 조회 실패. 검증 상태와 서버 실행 환경을 확인하세요.'))), 'application/json; charset=utf-8', 503
            self.respond(body, mime, status)

        def do_POST(self):
            if self.path != '/api/agent':
                self.respond(json_bytes(dict(error=dict(code='NOT_FOUND', message='없는 경로'))), status=404)
                return
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if host not in allowed or self.headers.get('Origin', 'http://' + host) not in {'http://' + h for h in allowed}:
                self.respond(json_bytes(dict(error=dict(code='ORIGIN_REJECTED', message='로컬 화면에서 요청하세요.'))), status=403)
                return
            try:
                if self.headers.get('Transfer-Encoding') or any(len(self.headers.get_all(k,[]))!=1 for k in ('Host','Content-Length','Content-Type')):
                    raise ValueError('잘못된 요청 프레이밍')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 12000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('JSON 본문은 1~12000바이트')
                def pairs(items):
                    obj = {}
                    for k, v in items:
                        if k in obj:
                            raise ValueError('중복 JSON 키')
                        obj[k] = v
                    return obj
                payload = json.loads(self.rfile.read(length).decode('utf-8'), object_pairs_hook=pairs,
                                     parse_constant=lambda value: (_ for _ in ()).throw(ValueError('비유한 JSON')))
                self.respond(json_bytes(agent.ask(payload)))
            except (ValueError, KeyError, TypeError, UnicodeError):
                self.respond(json_bytes(dict(error=dict(code='INVALID_AGENT_REQUEST', message='동·질문·기간·기준일을 확인하세요. 질문과 선택 지역/기간이 같아야 합니다.'))), status=400)
            except Exception:
                self.respond(json_bytes(dict(error=dict(code='AGENT_DATA_UNAVAILABLE', message='조회 도구 오류. 입력 데이터와 서버 검증 상태를 확인하세요.'))), status=503)

    return BoundedLocalServer(('127.0.0.1', port), Handler)


def serve(port=8766):
    server = make_server(port)
    print(f'P0 탐색 화면: http://127.0.0.1:{port} / 종료: Ctrl+C', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', help='기존 결과를 보존하고 새 공간 캐시만 준비')
    parser.add_argument('--serve', action='store_true', help='완료 공간 캐시로 탐색 화면 실행')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if args.prepare:
        prepare()
    if args.serve:
        serve(args.port)
    if not (args.prepare or args.serve):
        parser.print_help()


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    main()
