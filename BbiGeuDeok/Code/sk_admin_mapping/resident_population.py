"""행안부 공식 조회 표를 보존하고 관측월별 주민등록인구를 준비한다.

SGIS 경계 코드와 행안부 코드는 시군구+행정동 이름의 정확한 일대일
대응으로 연결한다. 공식 배포 코드 대응표를 전재한 자료는 아니다.
"""
from pathlib import Path
from html.parser import HTMLParser
from urllib.request import build_opener, HTTPCookieProcessor
from urllib.parse import urlencode
from http.cookiejar import CookieJar
from datetime import datetime, timezone
import csv
import hashlib
import json
import re

import pandas as pd

SOURCE = 'https://jumin.mois.go.kr/ageStatMonth.do'
MONTHS = json.loads(Path(__file__).with_name('config.example.json').read_text(encoding='utf-8'))['resident_population']['months']
AGES = ['10G', '20G', '30G', '40G', '50G', '60GU']
POPULATION_COLUMNS = ['RESIDENT_POP_TOTAL', 'RESIDENT_POP_10PLUS'] + [f'RESIDENT_POP_{a}' for a in AGES]


class OfficialTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = self.cell = False
        self.rows = []
        self.row = []
        self.value = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'table' and dict(attrs).get('id') == 'contextTable':
            self.active = True
        if self.active and tag == 'tr':
            self.row = []
        if self.active and tag in ['td', 'th']:
            self.cell = True
            self.value = ''

    def handle_data(self, text):
        if self.cell:
            self.value += text

    def handle_endtag(self, tag):
        if self.active and tag in ['td', 'th']:
            self.row.append(re.sub(r'\s+', ' ', self.value).strip())
            self.cell = False
        if self.active and tag == 'tr':
            self.rows.append(self.row)
        if self.active and tag == 'table':
            self.active = False


def validate_population(frame, regions):
    """누락 월·중복·분모 불일치를 거부하고 SK 대상 지역과 대조한다."""
    expected = {(code, month) for code in regions['EMD_CD'] for month in MONTHS}
    if frame.duplicated(['EMD_CD', 'STD_YM']).any():
        raise ValueError('외부 인구의 행정동·월이 중복됩니다.')
    if set(zip(frame['EMD_CD'], frame['STD_YM'])) != expected:
        raise ValueError('외부 인구의 대상 행정동·월이 부족하거나 과다합니다.')
    identity = ['SIGUNGU_CD', 'SIGUNGU_NM', 'EMD_CD', 'EMD_NM']
    recorded = frame[identity].drop_duplicates().sort_values('EMD_CD').reset_index(drop=True)
    wanted = regions[identity].drop_duplicates().sort_values('EMD_CD').reset_index(drop=True)
    if not recorded.equals(wanted):
        raise ValueError('외부 인구 대응표의 시군구·행정동 코드/이름이 현재 경계와 다릅니다.')
    if not frame['MOIS_ADMIN_CD'].str.fullmatch(r'\d{10}').all() or frame.groupby('EMD_CD')['MOIS_ADMIN_CD'].nunique().gt(1).any() or frame.groupby('MOIS_ADMIN_CD')['EMD_CD'].nunique().gt(1).any():
        raise ValueError('SGIS·행안부 코드 대응이 일대일이 아닙니다.')
    for column in POPULATION_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors='raise')
        if frame[column].isna().any() or frame[column].lt(0).any():
            raise ValueError('외부 인구에 결측 또는 음수가 있습니다.')
    if not frame[[f'RESIDENT_POP_{a}' for a in AGES]].sum(axis=1).eq(frame['RESIDENT_POP_10PLUS']).all():
        raise ValueError('10세 이상 인구와 제공 연령 구간 합계가 다릅니다.')
    if frame['RESIDENT_POP_10PLUS'].gt(frame['RESIDENT_POP_TOTAL']).any():
        raise ValueError('연령별 주민 수가 총인구보다 큽니다.')
    if not frame['EXT_STAT_MONTH'].eq(frame['STD_YM']).all() or not frame['EXT_STAT_YEAR'].eq(frame['STD_YM'].str[:4]).all():
        raise ValueError('SK 관측월과 주민등록인구 기준월이 다릅니다.')
    if not frame['EXT_SOURCE'].eq(SOURCE).all():
        raise ValueError('외부 인구 출처가 공식 행안부 조회와 다릅니다.')
    return frame


def prepare_population(work, regions):
    work = Path(work)
    folder = work / 'population_cache'
    target = folder / 'resident_population_monthly.csv'
    crosswalk = folder / 'sgis_mois_crosswalk.csv'
    metadata = folder / 'population_metadata.json'
    regions = regions[['SIGUNGU_CD', 'SIGUNGU_NM', 'EMD_CD', 'EMD_NM']].drop_duplicates()
    if regions['EMD_CD'].duplicated().any() or regions.duplicated(['SIGUNGU_NM', 'EMD_NM']).any():
        raise ValueError('행정동 코드·이름이 일대일로 결정되지 않습니다.')
    if target.exists():
        meta = json.loads(metadata.read_text(encoding='utf-8'))
        for name, digest in meta['sha256'].items():
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f'외부 인구 원자료/대응표 해시가 다릅니다: {name}')
        return validate_population(pd.read_csv(target, dtype=str, keep_default_na=False), regions), target, crosswalk, metadata
    folder.mkdir(exist_ok=True)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    opener.open(SOURCE, timeout=60).read()
    locations = json.loads(opener.open('https://jumin.mois.go.kr/selectHangkikcdListAjax.do', b'', timeout=60).read())['locations']
    records, relations, sources = [], [], []
    for city in ['강남구', '춘천시']:
        matches = [r for r in locations if str(r.get('levels')) == '2' and r.get('sikunkunm') == city]
        if len(matches) != 1:
            raise ValueError(f'공식 조회의 시군구가 하나로 결정되지 않습니다: {city}')
        district = matches[0]
        province = next(r for r in locations if str(r.get('levels')) == '1' and r.get('sidonm') == district['sidonm'])
        params = dict(tableChart='T', sltOrgType='2', sltOrgLvl1=province['hangkikcd'], sltOrgLvl2=district['hangkikcd'], sltUndefType='', searchYearStart=MONTHS[0][:4], searchMonthStart=MONTHS[0][4:], searchYearEnd=MONTHS[-1][:4], searchMonthEnd=MONTHS[-1][4:], searchYearMonth='month', gender='gender', sum='sum', sltArgTypes='10', sltArgTypeA='0', sltArgTypeB='100')
        raw = opener.open(SOURCE, urlencode(params).encode(), timeout=60).read()
        table = OfficialTable()
        table.feed(raw.decode('utf-8'))
        if not table.rows or table.rows[0][2:] != [f'{m[:4]}년 {m[4:]}월' for m in MONTHS]:
            raise ValueError('공식 조회의 월별 표 구조가 바뀌었습니다.')
        rows = [r for r in table.rows if r and re.fullmatch(r'\d{10}', r[0])]
        if not rows or rows[0][0] != district['hangkikcd'] or any(len(r) != 2+39*len(MONTHS) for r in rows):
            raise ValueError('공식 조회의 코드/성·연령별 표 구조가 바뀌었습니다.')
        if table.rows[2][:13] != ['총 인구수', '연령구간인구수', '0-9세', '10-19세', '20-29세', '30-39세', '40-49세', '50-59세', '60-69세', '70-79세', '80-89세', '90-99세', '100세 이상']:
            raise ValueError('공식 조회의 연령 구간이 바뀌었습니다.')
        city_rows = regions.loc[regions['SIGUNGU_NM'].eq(city)]
        names = [r[1] for r in rows[1:]]
        if len(set(names)) != len(names) or set(names) != set(city_rows['EMD_NM']):
            raise ValueError(f'공식 조회와 SGIS 행정동 이름이 일치하지 않습니다: {city}')
        lookup = city_rows.set_index('EMD_NM').to_dict('index')
        for r in rows[1:]:
            relations.append(dict(**lookup[r[1]], EMD_NM=r[1], MOIS_ADMIN_CD=r[0], MOIS_ADMIN_NM=r[1], RELATION_METHOD='exact_city_and_admin_name_one_to_one', BOUNDARY_DATE='2025-06-30', VALID_FROM=MONTHS[0], VALID_TO=MONTHS[-1], SOURCE=SOURCE))
        for i, month in enumerate(MONTHS):
            offset = 2 + i * 39
            def values(row):
                return [int(v.replace(',', '')) for v in row[offset:offset+39]]
            city_numbers = values(rows[0])
            if sum(values(r)[0] for r in rows[1:]) != city_numbers[0]:
                raise ValueError('행정동 합계와 공식 시군구 총인구가 다릅니다.')
            for row in rows:
                v = values(row)
                if v[0] != v[13] + v[26] or any(sum(v[k+2:k+13]) != v[k] for k in [0, 13, 26]):
                    raise ValueError('공식 성별/연령별 합계가 다릅니다.')
                if row is rows[0]:
                    continue
                result = dict(**lookup[row[1]], EMD_NM=row[1], MOIS_ADMIN_CD=row[0], STD_YM=month, RESIDENT_POP_TOTAL=v[0], RESIDENT_POP_10PLUS=v[0]-v[2])
                for k, sex in [(0, ''), (13, 'MAN_'), (26, 'WMAN_')]:
                    for age, number in zip(AGES, v[k+3:k+8] + [sum(v[k+8:k+13])]):
                        result[f'RESIDENT_{sex}POP_{age}'] = number
                result.update(RESIDENT_VALUE_STATUS='NUMERIC', EXT_STAT_YEAR=month[:4], EXT_STAT_MONTH=month, EXT_REFERENCE_DATE=f'{month[:4]}-{month[4:]}-'+('31' if month[4:] in ['07','08','10','12'] else '30'), EXT_CODE_SYSTEM='MOIS_administrative_institution_10digit', EXT_SOURCE=SOURCE, EXT_POPULATION_TYPE='registered_all_excluding_foreigners')
                records.append(result)
        source_name = f'mois_{district["hangkikcd"]}_{MONTHS[0]}_{MONTHS[-1]}.csv'
        with (folder / source_name).open('w', encoding='utf-8-sig', newline='') as stream:
            csv.writer(stream).writerows(table.rows)
        sources.append(source_name)
    frame = validate_population(pd.DataFrame(records).astype(str), regions)
    frame.to_csv(target, index=False, encoding='utf-8-sig')
    pd.DataFrame(relations).to_csv(crosswalk, index=False, encoding='utf-8-sig')
    meta = dict(source=SOURCE, retrieved_at_utc=datetime.now(timezone.utc).isoformat(), months=MONTHS, registration='전체: 거주자·거주불명자·재외국민 포함, 외국인 제외', reference='매월 말', boundary_reference='2025-06-30', crosswalk_method='시군구+행정동 이름 정확 일대일 대조; 공식 배포 대응표가 아님', dong_count=len(regions), dong_month_count=len(frame), raw_tables=sources)
    meta['sha256'] = {name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in [target.name, crosswalk.name, *sources]}
    metadata.write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return frame, target, crosswalk, metadata
