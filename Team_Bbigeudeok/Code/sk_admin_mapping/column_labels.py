"""원본 열은 보존하고 사람이 읽는 표·상태만 한글로 표시한다."""
from pathlib import Path
import pandas as pd
from IPython.display import display

COLUMN_LABELS = {
    "TABLE": "자료 종류", "SOURCE_FILE": "입력 파일", "SOURCE": "입력 파일", "PATH": "파일 경로",
    "STD_YM": "원본 기준년월", "STD_YM_KEY": "정규화 기준년월", "MONTH": "분석 기준년월",
    "BLOCK_CD": "소지역 코드", "X_COORD": "원본 X좌표", "Y_COORD": "원본 Y좌표",
    "BLOCK_CD_KEY": "정규화 소지역 코드", "X_COORD_KEY": "정규화 X좌표", "Y_COORD_KEY": "정규화 Y좌표",
    "CITY_CODE": "분석 시군구 코드", "DONG_CODE": "분석 행정동 코드", "POSITION_KEY": "관측 위치 번호",
    "LOCATION_ID": "월별 관측 위치 번호", "SIGUNGU_CD": "시군구 코드", "SIGUNGU_NM": "시군구",
    "EMD_CD": "행정동 코드", "EMD_NM": "매핑 행정동 이름", "CITY": "시군구 이름", "NAME": "행정동 이름",
    "MATCH_STATUS": "매칭 상태", "REASON": "검토 사유", "REVIEW_REASONS": "검토 사유",
    "CRS_STATUS": "좌표계 확인 상태", "N_LOCATIONS": "관측 위치 수",
    "PERCENT_WITHIN_MONTH_REGION": "월·지역 내 비율(%)", "METRIC_FIELD": "원본 지표 열",
    "METRIC_LABEL": "지표 이름", "METRIC": "지표", "VALUE": "유동 수준", "VALUE_STATUS": "값 상태",
    "SUM_VALUE": "제공 점 일평균 값 합계", "SUM": "제공 점 일평균 값 합계", "MEAN": "유효 위치당 일평균 값",
    "ROWS_BEFORE": "결합 전 행 수", "ROWS_AFTER": "결합 후 행 수", "ATTACHED_FILE": "결합 결과 파일",
    "HOLD_ROWS_PRESERVED": "보류 보존 행 수", "NOT_IN_INDEX_ROWS_PRESERVED": "인덱스 미매칭 보존 행 수",
    "ALL_ORIGINAL_CELLS_PRESERVED": "원래 문자열 보존", "POPULATION_VALUES_PRESERVED": "인구 값 보존",
    "N_SOURCE_ROWS": "입력 행 수", "N_NON_OK_ROWS": "공간 판정 보류 행 수", "N_NOT_IN_INDEX_ROWS": "인덱스 미매칭 행 수",
    "N_DUPLICATE_ROWS_HELD": "중복 위치 보류 행 수", "N_SPATIALLY_ACCEPTED_UNIQUE_ROWS": "정상 비중복 행 수",
    "SOURCE_ROWS": "동일 위치 입력 행 수", "FILE_COUNT": "동일 위치 파일 수", "N_POINT_ROWS": "집계 위치 수",
    "N_VALID": "유효 위치 수", "N_MISSING_OR_MASKED": "결측·비공개 값 수", "N_INVALID_NUMBER": "비수치 값 수",
    "N_NEGATIVE": "음수 값 수", "N_EXCLUDED_VALUES": "제외된 값 수", "N_VALUES": "검사 값 수",
    "N_MISSING": "결측 값 수", "N_INVALID": "무효 값·위치 수", "FIELD": "원본 지표 열",
    "BASIS": "집계 기준", "LEVEL": "집계 지역 단위", "VALUE_TYPE": "수준 계산 방식",
    "N_OBS": "전체 관측 위치 수", "N_COMMON": "기간 내 공통 위치 수", "N_STRICT": "모든 열·월 유효 위치 수",
    "N_POOL": "선택 집계 위치 수", "ROW_COMPLETE": "모든 원본 열 유효", "PREV": "전월 수준", "BASE": "기준월 수준",
    "DELTA": "전월 대비 변화량", "BASE_DELTA": "기준월 대비 변화량", "MOM_PCT": "전월 대비 변화율(%)",
    "BASE_PCT": "기준월 대비 변화율(%)", "BASE_INDEX": "기준월 지수(기준월=100)",
    "RATE_STATUS": "변화율 산출 상태", "COHORT_STATUS": "비교 위치 집단 상태",
    "N_PAIRS": "유효 월간 비교 구간 수", "N_DECREASE": "감소 구간 수", "CURRENT_STREAK": "현재 연속 감소 구간 수",
    "MAX_STREAK": "최장 연속 감소 구간 수", "CITY_MOM_PCT": "시군구 전월 변화율(%)",
    "CITY_BASE_PCT": "시군구 기준월 변화율(%)", "CITY_BASE_INDEX": "시군구 기준월 지수",
    "MOM_MINUS_CITY_PP": "시군구 대비 전월 변화율 차이(%p)", "BASE_MINUS_CITY_PP": "시군구 대비 기준월 변화율 차이(%p)",
    "RESIDENT_POP_TOTAL": "해당 월말 주민등록 총인구(명)", "RESIDENT_POP_10PLUS": "해당 월말 10세 이상 주민 수(명)",
    "EXT_STAT_MONTH": "외부 인구 기준년월", "EXT_REFERENCE_DATE": "외부 인구 기준일",
    "MOIS_ADMIN_CD": "행안부 행정기관 코드", "EXT_POPULATION_TYPE": "외부 인구 집계 대상",
    "AGE_RESIDENT_POP": "같은 연령의 해당 월말 주민 수(명)", "FLOW_INTENSITY": "총인구 대비 유동강도(참고)",
    "AGE_FLOW_INTENSITY": "같은 연령 주민 수 대비 유동강도(참고)", "POPULATION_RATE_STATUS": "주민 수 대비 지표 산출 상태",
    "RESIDENT_VALUE_STATUS": "외부 총인구 값 상태", "EXT_STAT_YEAR": "외부 통계연도", "EXT_BOUNDARY_DATE": "외부 경계 기준일",
    "EXT_JOIN_STATUS": "외부 결합 상태", "EXT_CODE_SYSTEM": "외부 코드 체계", "EXT_SOURCE": "외부 출처",
    "EXT_ARCHIVE_MEMBER": "외부 ZIP 내부 파일", "NO_EXTERNAL_CODE_MATCH": "외부 코드 미매칭 행 수",
    "MATCHED_BUT_POPULATION_UNAVAILABLE": "총인구 결측·비공개 행 수", "OUTPUT": "결과 경로",
    "BOUNDARY_NAME": "경계 행정동 이름", "BASE_DATE": "경계 기준일", "ABS_CITY_GAP": "시군구 변화와의 차이 절댓값(%p)",
    "CASE": "탐색 사례", "SERIES": "비교 집단", "ABS_CHANGE": "기준월 변화율 절댓값(%)",
    "ADM_LOCATION_ID": "매핑된 월별 위치 번호", "ADM_MATCH_STATUS": "행정구역 매칭 상태",
}
for age, label in zip(("10G","20G","30G","40G","50G","60GU"),("10대","20대","30대","40대","50대","60대 이상")):
    COLUMN_LABELS[f"RESIDENT_POP_{age}"] = label+" 월말 주민 수(명)"
for field in ("SIGUNGU_CD","SIGUNGU_NM","EMD_CD","EMD_NM"):
    COLUMN_LABELS["ADM_"+field] = "매핑된 "+COLUMN_LABELS[field]
COLUMN_LABELS.update({
    'BASE_RATE_STATUS': '7월 대비 변화율 산출 상태',
    'MOM_DECREASE': '전월 대비 감소 여부', 'BASE_DECREASE': '7월 대비 감소 여부',
    'BELOW_BASE_STREAK': '현재 7월 아래 연속 유지 월수', 'MAX_BELOW_BASE_STREAK': '최장 7월 아래 연속 유지 월수',
    'N_MOM_COMPARABLE': '전월 비교 가능 위치 수', 'N_BASE_COMPARABLE': '7월 비교 가능 위치 수',
    'N_MOM_DECREASE': '전월 대비 감소 위치 수', 'N_BASE_DECREASE': '7월 대비 감소 위치 수',
    'MOM_DECREASE_PCT': '전월 대비 감소 위치 비율(%)', 'BASE_DECREASE_PCT': '7월 대비 감소 위치 비율(%)',
    'MOM_DECREASE_BASE_FLOW': '전월 감소 위치의 7월 유동 합계', 'BASE_DECREASE_BASE_FLOW': '7월 감소 위치의 7월 유동 합계',
    'MOM_DECREASE_BASE_WEIGHT_PCT': '전월 감소 위치의 7월 유동 비중(%)',
    'BASE_DECREASE_BASE_WEIGHT_PCT': '7월 감소 위치의 7월 유동 비중(%)',
    'TOTAL_FLOW': '동일 위치 전체 연령 유동 합계',
    'SHARE_PCT': '연령별 유동 구성비(%)', 'SHARE_STATUS': '유동 구성비 산출 상태',
    'PREV_SHARE_PCT': '전월 연령별 유동 구성비(%)', 'BASE_SHARE_PCT': '7월 연령별 유동 구성비(%)',
    'SHARE_MOM_PP': '전월 대비 구성비 변화(%p)', 'SHARE_BASE_PP': '7월 대비 구성비 변화(%p)',
    'SHARE_MOM_STATUS': '전월 대비 구성비 변화 산출 상태', 'SHARE_BASE_STATUS': '7월 대비 구성비 변화 산출 상태',
    'TOTAL_MOM_PCT': '전체 연령 전월 변화율(%)', 'TOTAL_BASE_PCT': '전체 연령 7월 변화율(%)',
    'MOM_MINUS_TOTAL_PP': '전체 연령 대비 전월 변화율 차이(%p)', 'BASE_MINUS_TOTAL_PP': '전체 연령 대비 7월 변화율 차이(%p)',
    'MOM_CHANGE_CONTRIB_PP': '전체 전월 변화율의 해당 연령 기여도(%p)',
    'BASE_CHANGE_CONTRIB_PP': '전체 7월 변화율의 해당 연령 기여도(%p)',
    'N_POSITIONS': '연령 계산 고정 위치 수', 'N_POSITION_MONTHS': '연령 계산 위치·월 수',
    'N_ZERO': '연령 유동 수준 0인 위치·월 수', 'N_ZERO_PREVIOUS': '연령 전월 수준 0인 위치·월 수',
    'N_ZERO_BASE': '연령 7월 수준 0인 위치·월 수',
    'TOTAL_24H_FLOW': '동일 위치 24시간 제공 값 합계',
    'PREV_TOTAL_24H_FLOW': '전월 24시간 제공 값 합계', 'BASE_TOTAL_24H_FLOW': '7월 24시간 제공 값 합계',
    'TIME_SHARE_PCT': '시간대별 유동 구성비(%)', 'TIME_SHARE_STATUS': '시간대 구성비 산출 상태',
    'TIME_PREV_SHARE_PCT': '전월 시간대별 구성비(%)', 'TIME_BASE_SHARE_PCT': '7월 시간대별 구성비(%)',
    'TIME_SHARE_MOM_PP': '전월 대비 시간대 구성비 변화(%p)', 'TIME_SHARE_BASE_PP': '7월 대비 시간대 구성비 변화(%p)',
    'TIME_SHARE_MOM_STATUS': '전월 대비 시간대 구성비 변화 산출 상태', 'TIME_SHARE_BASE_STATUS': '7월 대비 시간대 구성비 변화 산출 상태',
    'HOUR_MOM_CHANGE_CONTRIB_PP': '24시간 전체 전월 변화율의 해당 시간 기여도(%p)',
    'HOUR_BASE_CHANGE_CONTRIB_PP': '24시간 전체 7월 변화율의 해당 시간 기여도(%p)',
    'N_HOURS_MOM_COMPARABLE': '전월 비교 가능 시간대 수', 'N_HOURS_BASE_COMPARABLE': '7월 비교 가능 시간대 수',
    'N_HOURS_MOM_DECREASE': '전월 대비 감소 시간대 수', 'N_HOURS_BASE_DECREASE': '7월 대비 감소 시간대 수',
    'MAX_ABS_TIME_SHARE_MOM_PP': '전월 대비 시간대 구성비 변화 절댓값 최댓값(%p)',
    'MAX_ABS_TIME_SHARE_BASE_PP': '7월 대비 시간대 구성비 변화 절댓값 최댓값(%p)',
    'TIME_FIXED_POSITIONS': '시간대 계산 고정 위치 수', 'TIME_POSITION_MONTHS': '시간대 계산 위치·월 수',
    'TIME_ZERO_VALUES': '공통 유효 위치의 시간대 값 0 개수', 'TIME_INVALID_VALUES': '공통 위치의 시간대 무효 값 개수',
    'WKDY_FIXED_POSITIONS': '요일 계산 고정 위치 수', 'WKDY_POSITION_MONTHS': '요일 계산 위치·월 수',
    'WKDY_ZERO_WEEKDAY': '평일 평균 0인 위치·월 수', 'WKDY_ZERO_WEEKEND': '주말 평균 0인 위치·월 수',
    'WKDY_INVALID_VALUES': '공통 위치의 요일 무효 값 개수',
    'WEEKEND_WEEKDAY_RATIO': '주말 평균 / 평일 평균(배)',
    'PREV_WEEKEND_WEEKDAY_RATIO': '전월 주말 / 평일 비율(배)', 'BASE_WEEKEND_WEEKDAY_RATIO': '7월 주말 / 평일 비율(배)',
    'RATIO_MOM_DELTA': '전월 대비 주말 / 평일 비율 차이(배)', 'RATIO_BASE_DELTA': '7월 대비 주말 / 평일 비율 차이(배)',
    'WEEKDAY_RATIO_STATUS': '주말 / 평일 비율 산출 상태', 'RATIO_MOM_STATUS': '전월 대비 비율 차이 산출 상태', 'RATIO_BASE_STATUS': '7월 대비 비율 차이 산출 상태',
})
for duration in range(6):
    for prefix, label in [('CURRENT', f'현재 연속 감소 {duration}구간'), ('BELOW_BASE', f'현재 7월 아래 연속 유지 {duration}개월')]:
        COLUMN_LABELS[f'N_{prefix}_STREAK_{duration}'] = label + ' 위치 수'
        COLUMN_LABELS[f'{prefix}_STREAK_{duration}_PCT'] = label + ' 위치 비율(%)'
for prefix, label in [('WEEKDAY', '평일 평균'), ('WEEKEND', '주말 평균')]:
    for field, description in [('VALUE', '유동 합계'), ('PREV', '전월 유동 합계'), ('BASE', '7월 유동 합계'), ('DELTA', '전월 대비 변화량'), ('BASE_DELTA', '7월 대비 변화량'), ('MOM_PCT', '전월 대비 변화율(%)'), ('BASE_PCT', '7월 대비 변화율(%)')]:
        COLUMN_LABELS[f'{prefix}_{field}'] = label + ' ' + description
    for prefix, label in [('POSITION_CURRENT', f'위치별 현재 연속 감소 {duration}구간'), ('POSITION_BELOW_BASE', f'위치별 현재 7월 아래 유지 {duration}개월')]:
        COLUMN_LABELS[f'N_{prefix}_STREAK_{duration}'] = label + ' 위치 수'
        COLUMN_LABELS[f'{prefix}_STREAK_{duration}_PCT'] = label + ' 위치 비율(%)'
for sex, label in (("MAN", "남성"), ("WMAN", "여성")):
    for age in ("10G", "20G", "30G", "40G", "50G", "60GU"):
        COLUMN_LABELS[f"{sex}_FLOW_POP_CNT_{age}"] = label + " " + ("60대 이상" if age == "60GU" else age[:2]+"대") + " 일평균 유동 값"
for hour in range(24): COLUMN_LABELS[f"TMST_{hour:02d}"] = f"{hour:02d}시 일평균 유동 값"
for day,label in zip(("MON","TUS","WED","THU","FRI","SAT","SUN"),("월","화","수","목","금","토","일")):
    COLUMN_LABELS["FLOW_POP_CNT_"+day] = label+"요일 일평균 유동 값"

VALUE_LABELS = {
    "flow_age_pop":"성·연령별 유동", "flow_time_pop":"시간대별 유동", "flow_wkdy_pop":"요일별 유동",
    "OK":"정상 매칭", "HOLD":"판정 보류", "NOT_IN_INDEX":"인덱스 미매칭", "OUTSIDE_TARGET_REGIONS":"대상 지역 밖",
    "ASSUMED_UNVERIFIED":"좌표계 임시 가정·미확인", "ALL":"월별 전체 유효 위치", "COMMON_LOCATION":"기간 내 공통 위치",
    "COMMON_VALID":"공통 위치·모든 열/월 유효", "DONG":"행정동", "CITY":"시군구", "SUM":"합계", "MEAN":"유효 위치당 평균",
    "CALCULATED":"산출됨", "CURRENT_MISSING":"현재 값 결측", "FIRST_MONTH":"첫 관측 월", "PREVIOUS_MISSING":"전월 값 결측",
    "ZERO_BASE":"7월 수준 0",
    "ZERO_TOTAL":"전체 연령 유동 합계 0·산출 불가",
    "ZERO_24H_TOTAL":"24시간 제공 값 합계 0·산출 불가",
    "ZERO_WEEKDAY":"평일 평균 0·비율 산출 불가", "CURRENT_RATIO_UNAVAILABLE":"현재 비율 산출 불가",
    "PREVIOUS_RATIO_UNAVAILABLE":"전월 비율 산출 불가", "BASE_RATIO_UNAVAILABLE":"7월 비율 산출 불가",
    "CURRENT_SHARE_UNAVAILABLE":"현재 구성비 산출 불가", "PREVIOUS_SHARE_UNAVAILABLE":"전월 구성비 산출 불가", "BASE_SHARE_UNAVAILABLE":"7월 구성비 산출 불가",
    "ZERO_PREVIOUS":"전월 수준 0", "SMALL_PREVIOUS":"작은 전월 분모", "NO_OBSERVATION":"관측 없음",
    "FIXED_VALID_COHORT":"동일 유효 위치 집단", "DESCRIPTIVE_COVERAGE_OR_VALIDITY_CAN_CHANGE":"관측·유효 범위 변동 가능",
    "NUMERIC":"유효 수치", "MISSING_MASKED_OR_INVALID":"결측·비공개·무효", "CODE_MATCHED":"코드 매칭", "CODE_MONTH_MATCHED":"코드·관측월 매칭", "ZERO_RESIDENT_POPULATION":"주민 수 0·산출 불가",
    "NO_EXTERNAL_CODE_MATCH":"외부 코드 미매칭", "NO_VALID_VALUES":"유효 값 없음", "VALID":"유효",
    "COMPLETE_VALUES":"전체 값 유효", "PARTIAL_VALUES":"일부 값 제외",
    "interior":"경계 내부", "near_boundary":"경계 인접",
    "distribution_extreme_east":"관측 범위 동쪽 끝", "distribution_extreme_west":"관측 범위 서쪽 끝",
    "distribution_extreme_north":"관측 범위 북쪽 끝", "distribution_extreme_south":"관측 범위 남쪽 끝",
    "chuncheon_name_suffix_동":"춘천 동 지역 표본", "chuncheon_name_suffix_면":"춘천 면 지역 표본",
    "chuncheon_name_suffix_읍":"춘천 읍 지역 표본",
}


def korean_table(value):
    frame = value.copy()
    frame = frame.rename(columns=COLUMN_LABELS)
    if frame.columns.name: frame.columns = frame.columns.rename(COLUMN_LABELS.get(frame.columns.name, frame.columns.name))
    if frame.index.name: frame.index = frame.index.rename(COLUMN_LABELS.get(frame.index.name, frame.index.name))
    if isinstance(frame.index, pd.MultiIndex): frame.index = frame.index.set_names([COLUMN_LABELS.get(n,n) for n in frame.index.names])
    for position,name in enumerate(frame.columns):
        column = frame.iloc[:,position]
        if pd.api.types.is_bool_dtype(column):
            frame.isetitem(position,column.map({True:"예",False:"아니요"}))
        elif column.dtype == object or pd.api.types.is_string_dtype(column):
            frame.isetitem(position,column.map(lambda item: VALUE_LABELS.get(item,item) if isinstance(item,str) else item))
    if not isinstance(frame.index, pd.MultiIndex): frame.index = frame.index.map(lambda item:VALUE_LABELS.get(item,item))
    return frame


def show_table(value, *args, **kwargs):
    if isinstance(value,pd.Series): value=value.to_frame()
    if isinstance(value,pd.DataFrame): value=korean_table(value)
    elif hasattr(value,"data") and isinstance(value.data,pd.DataFrame): value=korean_table(value.data)
    display(value,*args,**kwargs)


def setup_korean_font():
    import matplotlib as mpl
    from matplotlib import font_manager
    installed={font.name for font in font_manager.fontManager.ttflist}
    for font in ("Malgun Gothic","AppleGothic","NanumGothic","Noto Sans CJK KR"):
        if font in installed:
            mpl.rcParams["font.family"]=font
            mpl.rcParams["axes.unicode_minus"]=False
            return font
    print("한글 폰트를 찾지 못했습니다. README의 한글 폰트 안내를 확인하세요.")
    return None
