"""STEP 1 ROA 입력 계약·정합성 검증. SK 재처리·신규 유형 판정·파일 쓰기 없음."""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
import csv
import hashlib
import json
import math
import re

WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
CONTRACT = WORK / "roa_contract.json"
NUMERIC_FIELDS = (
    "r_score_std_pct", "r_raw_rate_pct", "r_group_est", "r_threshold_median_21",
    "o_score_std_pct", "o_raw_rate_pct", "o_group_est", "o_threshold_median_21",
    "single_hh_est", "min_stratum_denominator", "a_activity_window_level",
    "a_window_change_pct", "a_p1_nov_vs_jul_pct", "a_p1_below_july_months",
    "a_sk_positions_n",
)
INTEGER_FIELDS = {"a_p1_below_july_months", "a_sk_positions_n"}
FLAG_FIELDS = (
    "r_above_threshold", "o_above_threshold", "r_signal_v1", "o_signal_v1",
    "a_signal_v1", "in_v1_main_comparison",
)
ROA_COLUMNS = {
    "adm_cd8", "adm_nm", "ext_cd7", "ext_nm", "sigungu_cd", "sigungu_nm",
    "ref_month", "rel_calc_period", "rel_calc_period_status", "sk_window",
    "sk_base_window", "p1_compare_months", "r_value_status", "o_value_status",
    "a_value_status", "quality_note", "src_relation", "src_activity", "rule_version_v1",
    *NUMERIC_FIELDS, *FLAG_FIELDS,
}
SUPPLEMENT_COLUMNS = set((
    "외부코드7", "원자료동명", "동", "SK코드8", "기준월", "추정1인가구수", "최소구간분모",
    "연락취약_std", "연락취약_std22", "연락취약_원비율", "평일외출적음_std",
    "평일외출적음_std22", "평일외출적음_원비율", "휴일외출적음_std",
    "휴일외출적음_std22", "휴일외출적음_원비율", "관계망값상태", "활동_전체",
    "활동_주말", "활동_평일", "SK창", "활동_전체_창변화pct", "SK위치수",
    "P1_11월대비7월pct", "P1_7월아래유지개월", "경계면적_km2", "활동밀도_km2", "활동값상태",
))
P1_COLUMNS = set((
    "TABLE", "BASIS", "LEVEL", "CODE", "CITY", "NAME", "MONTH", "METRIC", "N", "VALUE",
    "BASE", "BASE_PCT", "MOM_PCT", "CURRENT_STREAK", "BELOW_BASE_STREAK", "SHARE", "SHARE_BASE_PP",
))
CROSSWALK_COLUMNS = {"외부코드7", "행정동", "동", "SK코드8", "주분석보류", "8자리 앞7자리", "자르기 결과"}


class RoaValidationError(ValueError):
    """입력을 임의 보정하지 않고 오류 위치를 표시하며 중단한다."""


def require(condition, message):
    if not condition:
        raise RoaValidationError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"JSON 중복 키: {key}")
        result[key] = value
    return result


def read_json_strict(path):
    def invalid_constant(value):
        raise RoaValidationError(f"JSON 비유한 값: {value}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                          object_pairs_hook=_unique_json, parse_constant=invalid_constant)
    except (OSError, ValueError) as error:
        raise RoaValidationError(f"JSON 읽기 실패 {path}: {error}") from error


def read_table(path, columns):
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            header = reader.fieldnames or []
            require(len(header) == len(set(header)) and set(header) == columns,
                    f"{Path(path).name}: 스키마 불일치")
            rows = list(reader)
        require(all(None not in row and None not in row.values() for row in rows),
                f"{Path(path).name}: 잘못된 CSV 행 길이")
        return rows
    except (OSError, UnicodeError, csv.Error) as error:
        raise RoaValidationError(f"CSV 읽기 실패 {path}: {error}") from error


def code_text(value, length, label="code"):
    require(isinstance(value, str) and re.fullmatch(r"[0-9]{%d}" % length, value) is not None,
            f"{label}: {length}자리 코드 문자열 필요: {value!r}")
    return value


def decimal_value(value, label):
    require(isinstance(value, str) and value != "" and value == value.strip(),
            f"{label}: 숫자 원문 누락 또는 공백")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise RoaValidationError(f"{label}: 숫자가 아님 {value!r}") from error
    require(number.is_finite() and math.isfinite(float(number)), f"{label}: 비유한 숫자")
    return number


def number(value, label, *, integer=False, nullable=False):
    if value == "" and nullable:
        return None
    parsed = decimal_value(value, label)
    if integer:
        require(parsed == parsed.to_integral_value(), f"{label}: 정수 필요")
        return int(parsed)
    return float(parsed)


def flag(value, label):
    require(value in ("Y", "N", ""), f"{label}: Y/N/빈값 필요: {value!r}")
    return {"Y": True, "N": False, "": None}[value]


def above_median(value, threshold):
    """검증용 구규칙 비교. 동률은 미충족이며 반올림·허용오차를 적용하지 않는다."""
    return decimal_value(value, "score") > decimal_value(threshold, "threshold")


def legacy_a_check(window_pct, p1_pct):
    """구버전 A 재현 검사 전용. 신규 분류나 실질 감소폭 기준이 아니다."""
    return decimal_value(window_pct, "window_pct") < 0 and decimal_value(p1_pct, "p1_pct") < 0


def unique_index(rows, keys, label):
    result = {}
    for row in rows:
        key = tuple(row[k] for k in keys)
        require(key not in result, f"{label}: 중복 키 {key}")
        result[key] = row
    return result


def window_label(months):
    return f"{months[0][:4]}-{months[0][4:]}~{months[-1][:4]}-{months[-1][4:]}"


def verify_periods(row, contract):
    month = row["ref_month"]
    require(month in contract["reference_months"], f"알 수 없는 기준월: {month}")
    expected = window_label(contract["windows"][month])
    require(row["rel_calc_period"] == expected and row["sk_window"] == expected,
            f"{row['adm_cd8']}/{month}: 기준월과 측정기간 불일치")
    require(row["sk_base_window"] == "2025-07~2025-09", "SK 기준 창 불일치")
    require(row["p1_compare_months"] == "2025-11 vs 2025-07", "P1 비교기간 불일치")
    require(row["rel_calc_period_status"] == contract["relation_period_status"],
            "관계망 기간 미확인 상태 누락/변경")


def verify_roa(rows, crosswalk, contract):
    """파일 hash 검사와 독립적인 내용 검증. 오류를 숨기는 조인·형변환을 하지 않는다."""
    require(len(rows) == contract["expected_rows"], "ROA: 66행 필요")
    require(all(set(row) == ROA_COLUMNS for row in rows), "ROA: 스키마 불일치")
    by_key = unique_index(rows, ("adm_cd8", "ref_month"), "ROA")
    cw = unique_index(crosswalk, ("SK코드8",), "crosswalk SK")
    unique_index(crosswalk, ("외부코드7",), "crosswalk 외부")
    require(len(cw) == contract["expected_dongs"], "crosswalk: 22동 필요")
    require(cw.get(("11230510",), {}).get("외부코드7") == "1123051", "신사동 대응 오류")
    require(cw.get(("11230511",), {}).get("외부코드7") == "1123074", "개포3동 대응 오류")
    expected_keys = {(cd[0], month) for cd in cw for month in contract["reference_months"]}
    require(set(by_key) == expected_keys, "ROA: 동×기준월 범위 불일치")
    comparable = 0
    for row in rows:
        cd, month = row["adm_cd8"], row["ref_month"]
        code_text(cd, 8, "adm_cd8")
        code_text(row["ext_cd7"], 7, "ext_cd7")
        code_text(row["sigungu_cd"], 5, "sigungu_cd")
        mapping = cw[(cd,)]
        require((row["adm_nm"], row["ext_cd7"], row["ext_nm"]) ==
                (mapping["동"], mapping["외부코드7"], mapping["행정동"]), f"{cd}: 명시적 코드/명칭 대응 오류")
        hold = cd == contract["hold_adm_cd8"]
        require(mapping["주분석보류"] == ("True" if hold else "False"), f"{cd}: crosswalk 보류 불일치")
        require((row["sigungu_cd"], row["sigungu_nm"]) == ("11230", "강남구"), "강남구 이외 자료")
        verify_periods(row, contract)
        for key in NUMERIC_FIELDS:
            number(row[key], f"{cd}/{month}/{key}", integer=key in INTEGER_FIELDS)
        for key in FLAG_FIELDS:
            flag(row[key], f"{cd}/{month}/{key}")
        require(row["in_v1_main_comparison"] == ("N" if hold else "Y"), f"{cd}: 주 비교 범위 오류")
        require(row["rule_version_v1"] == contract["legacy_csv_rule_version"], "구규칙 버전 불일치")
        require((row["src_relation"], row["src_activity"]) == ("SRC_REL", "SRC_ACT"), "출처 ID 불일치")
        require(row["a_value_status"] == "OBSERVED", f"{cd}: A 관측 상태 오류")
        for key in ("single_hh_est", "min_stratum_denominator", "a_sk_positions_n"):
            require(decimal_value(row[key], key) > 0, f"{cd}: 분모/위치수는 양수 필요")
        require(decimal_value(row["a_activity_window_level"], "A level") >= 0, "음수 활동 수준")
        require(0 <= number(row["a_p1_below_july_months"], "below") <= 4, "11월 유지개월 범위 오류")
        for sig in ("r", "o"):
            require(row[f"{sig}_value_status"] == ("ZERO_CAUSE_UNVERIFIED" if hold else "OBSERVED"),
                    f"{cd}: {sig} 품질 상태 오류")
            for field in ("score_std_pct", "raw_rate_pct", "threshold_median_21"):
                require(0 <= number(row[f"{sig}_{field}"], field) <= 100, f"{cd}: 비율 범위 오류")
            require(decimal_value(row[f"{sig}_group_est"], "group") >= 0, "음수 집단 추정값")
            for field in ("above_threshold", "signal_v1"):
                require((row[f"{sig}_{field}"] == "") == hold, f"{cd}: {sig} 결측/보류 불일치")
            if hold:
                require(all(decimal_value(row[f"{sig}_{key}"], key) == 0
                            for key in ("score_std_pct", "raw_rate_pct", "group_est")), "개포3동 원시 0 변경")
        require((row["a_signal_v1"] == "") == hold, f"{cd}: A 구신호 결측/보류 불일치")
        require(bool(row["quality_note"]) == hold, f"{cd}: 보류 이유 누락/예상 외 품질 메모")
        comparable += not hold
    require(comparable == contract["expected_comparable_dongs"] * 3, "ROA: 정상 비교 63행 필요")
    for month in contract["reference_months"]:
        pop = [r for r in rows if r["ref_month"] == month and r["in_v1_main_comparison"] == "Y"]
        for sig in ("r", "o"):
            threshold = median([decimal_value(r[f"{sig}_score_std_pct"], sig) for r in pop])
            for row in [r for r in rows if r["ref_month"] == month]:
                require(decimal_value(row[f"{sig}_threshold_median_21"], "median") == threshold,
                        f"{month}/{sig}: 21동 중앙값 불일치")
                if row["in_v1_main_comparison"] == "Y":
                    require(flag(row[f"{sig}_above_threshold"], sig) ==
                            above_median(row[f"{sig}_score_std_pct"], row[f"{sig}_threshold_median_21"]),
                            f"{row['adm_cd8']}/{month}/{sig}: 중앙값 초과 불일치")
    return by_key


@dataclass
class RoaInputs:
    root: Path
    contract: dict
    rows: list[dict]
    supplement: list[dict]
    legacy: dict
    activity: list[dict]
    crosswalk: list[dict]
    provenance: dict
    contract_sha256: str


def load_inputs(project_root=None):
    root = Path(project_root or ROOT).resolve()
    contract_path = root / "Code/sk_admin_mapping/roa_contract.json"
    contract = read_json_strict(contract_path)
    require(contract["schema_version"] == "agent_input_integrated.v1" and
            contract["output"] == "Code/sk_admin_mapping/outputs/canonical/agent/agent_input_integrated.v1.json",
            "지원하지 않는 출력 스키마/경로 계약")
    provenance = {}
    for key, spec in contract["inputs"].items():
        path = (root / spec["path"]).resolve()
        require(path.is_relative_to(root), f"{key}: 프로젝트 밖 입력 경로")
        require(path.is_file(), f"기준 입력 없음: {spec['path']}")
        actual = sha256(path)
        require(actual == spec["sha256"], f"{key}: 기준 입력 SHA-256 불일치. 임의 갱신하지 마세요.")
        provenance[key] = {"path": spec["path"], "sha256": actual, "bytes": path.stat().st_size,
                           "encoding": "utf-8-sig", "version_status": "PINNED_BY_SHA256"}
    def path(key):
        return root / contract["inputs"][key]["path"]
    data = RoaInputs(root, contract, read_table(path("roa"), ROA_COLUMNS),
                     read_table(path("supplement"), SUPPLEMENT_COLUMNS), read_json_strict(path("legacy_agent")),
                     read_table(path("activity"), P1_COLUMNS), read_table(path("crosswalk"), CROSSWALK_COLUMNS),
                     provenance, sha256(contract_path))
    return data


def verify_sources_unchanged(data):
    for key, spec in data.provenance.items():
        require(sha256(data.root / spec["path"]) == spec["sha256"], f"{key}: 처리 중 입력 변경")
    require(sha256(data.root / "Code/sk_admin_mapping/roa_contract.json") == data.contract_sha256,
            "처리 중 계약 변경")


def validate_inputs(data):
    """입력 내용·구규칙·P1 계산을 대조한다. 최종 유형은 생성하지 않는다."""
    c = data.contract
    rows = verify_roa(data.rows, data.crosswalk, c)
    supplement = unique_index(data.supplement, ("SK코드8", "기준월"), "v2 통합표")
    require(len(data.supplement) == 66 and set(supplement) == set(rows), "v2 통합표 동×월 불일치")
    meta = data.legacy["meta"]
    require((meta["schema_version"], meta["rule_version"]) ==
            (c["legacy_json_schema_version"], c["legacy_json_rule_version"]), "기존 JSON 버전 오류")
    require(meta["reference_months"] == c["reference_months"] and meta["region"] ==
            {"sigungu_cd": "11230", "sigungu_nm": "강남구", "n_dong": 22}, "기존 JSON 지역/기간 오류")
    legacy = unique_index(data.legacy["dongs"], ("adm_cd8",), "기존 JSON")
    require(set(legacy) == {(cd,) for cd, _ in rows}, "기존 JSON 동 범위 불일치")
    require(any(s["id"] == "SRC_ACT" and s.get("sha256") == data.provenance["activity"]["sha256"]
                for s in meta["sources"]), "기존 JSON의 P1 출처 hash 불일치")
    target = [r for r in data.activity if (r["TABLE"], r["BASIS"], r["LEVEL"], r["METRIC"], r["CITY"]) ==
              ("flow_age_pop", "original", "DONG", "TOTAL", "강남구")]
    activity = unique_index(target, ("CODE", "MONTH"), "P1 대상")
    sk_months = [f"2025{m:02d}" for m in range(7, 13)]
    require(set(activity) == {(cd[0], m) for cd in legacy for m in sk_months}, "P1: 22동×6개월 필요")
    tolerances = c["verification_tolerances"]
    errors = {"supplement_max_abs": 0.0, "p1_window_level_max_abs": 0.0, "p1_window_pct_max_abs": 0.0,
              "p1_nov_pct_max_abs": 0.0, "legacy_rounded_pct_max_abs": 0.0}
    def close(actual, expected, tolerance, label, metric=None):
        delta = abs(actual - expected)
        require(delta <= tolerance, f"{label}: 수치 불일치 {actual} != {expected}")
        if metric:
            errors[metric] = max(errors[metric], delta)
    pairs = {
        "r_score_std_pct": "연락취약_std", "r_raw_rate_pct": "연락취약_원비율",
        "o_score_std_pct": "평일외출적음_std", "o_raw_rate_pct": "평일외출적음_원비율",
        "single_hh_est": "추정1인가구수", "min_stratum_denominator": "최소구간분모",
        "a_activity_window_level": "활동_전체", "a_window_change_pct": "활동_전체_창변화pct",
        "a_p1_nov_vs_jul_pct": "P1_11월대비7월pct", "a_p1_below_july_months": "P1_7월아래유지개월",
        "a_sk_positions_n": "SK위치수",
    }
    for (cd,), old in sorted(legacy.items()):
        group = [rows[(cd, m)] for m in c["reference_months"]]
        hold = cd == c["hold_adm_cd8"]
        require((old["adm_nm"], old["ext_cd7"], old["ext_nm"]) ==
                (group[0]["adm_nm"], group[0]["ext_cd7"], group[0]["ext_nm"]), f"{cd}: JSON 지역 대응 오류")
        p1 = [activity[(cd, m)] for m in sk_months]
        require({p["NAME"] for p in p1} == {old["adm_nm"]}, f"{cd}: P1 동명 오류")
        for p in p1:
            for key in ("VALUE", "BASE", "BASE_PCT", "N", "CURRENT_STREAK", "BELOW_BASE_STREAK"):
                number(p[key], f"P1/{cd}/{p['MONTH']}/{key}", integer=key in {"N", "CURRENT_STREAK", "BELOW_BASE_STREAK"})
            number(p["MOM_PCT"], "P1 MOM", nullable=p["MONTH"] == "202507")
        require(len({p["N"] for p in p1}) == 1, f"{cd}: 고정 위치 수 변경")
        for sig in ("r", "o", "a"):
            states = {r[f"{sig}_signal_v1"] for r in group}
            require(len(states) == 1, f"{cd}: 구기간 신호가 월별로 변경됨")
            state = old["signals"][sig.upper()]["state"]
            if not hold:
                require(state == ("ON" if flag(group[0][f"{sig}_signal_v1"], sig) else "OFF"),
                        f"{cd}/{sig}: 기존 JSON과 구신호 불일치")
            elif sig in ("r", "o"):
                require(state == "UNDETERMINED", "개포3동 JSON R/O 보류 누락")
        for sig in ("r", "o"):
            if not hold:
                require(flag(group[0][f"{sig}_signal_v1"], sig) ==
                        all(flag(r[f"{sig}_above_threshold"], sig) for r in group), f"{cd}: 전체기간 R/O 오류")
        endpoint = group[-1]
        expected_a = legacy_a_check(endpoint["a_window_change_pct"], endpoint["a_p1_nov_vs_jul_pct"])
        require(old["signals"]["A"]["state"] == ("ON" if expected_a else "OFF"), f"{cd}: 기존 JSON A 재현 오류")
        if not hold:
            require(flag(endpoint["a_signal_v1"], "A") == expected_a, f"{cd}: 구 A 조건 불일치")
        base_window = c["windows"][c["reference_months"][0]]
        def window_level(months):
            days = [monthrange(int(m[:4]), int(m[4:]))[1] for m in months]
            return sum(number(activity[(cd, m)]["VALUE"], "P1 VALUE") * d
                       for m, d in zip(months, days)) / sum(days)
        base_level = window_level(base_window)
        for row in group:
            month = row["ref_month"]
            sup = supplement[(cd, month)]
            require((sup["동"], sup["외부코드7"], sup["원자료동명"]) ==
                    (row["adm_nm"], row["ext_cd7"], row["ext_nm"]), "통합표 조인 대응 오류")
            expected_sup_window = "~".join([c["windows"][month][0], c["windows"][month][-1]])
            require(sup["SK창"] == expected_sup_window, "통합표 SK창 불일치")
            require(sup["활동값상태"] == "관측값", "통합표 활동 품질 불일치")
            for key, other in pairs.items():
                close(number(row[key], key), number(sup[other], other),
                      tolerances["level_abs"] if key in {"a_activity_window_level", "single_hh_est"}
                      else tolerances["pct_abs"], key, "supplement_max_abs")
            for sig in ("r", "o"):
                close(100 * number(row[f"{sig}_group_est"], sig) / number(row["single_hh_est"], "hh"),
                      number(row[f"{sig}_raw_rate_pct"], sig), tolerances["pct_abs"], "원비율")
                old_signal = old["signals"][sig.upper()]
                if hold:
                    require(old_signal["value_pct_by_month"][month] is None and
                            old_signal["raw_value_pct_by_month"][month] == 0, "개포3동 JSON 원값/보류 오류")
                else:
                    close(float(old_signal["value_pct_by_month"][month]), number(row[f"{sig}_score_std_pct"], sig),
                          tolerances["legacy_rounded_pct_abs"], "JSON 반올림 대조", "legacy_rounded_pct_max_abs")
                close(float(old_signal["threshold_pct_by_month"][month]), number(row[f"{sig}_threshold_median_21"], sig),
                      tolerances["legacy_rounded_pct_abs"], "JSON 중앙값 반올림 대조")
            level = window_level(c["windows"][month])
            close(level, number(row["a_activity_window_level"], "A"), tolerances["level_abs"],
                  "P1 창 수준", "p1_window_level_max_abs")
            close(100 * (level - base_level) / base_level, number(row["a_window_change_pct"], "A pct"),
                  tolerances["pct_abs"], "P1 창 변화", "p1_window_pct_max_abs")
            nov = activity[(cd, "202511")]
            close(number(nov["BASE_PCT"], "P1 pct"), number(row["a_p1_nov_vs_jul_pct"], "A p1"),
                  tolerances["pct_abs"], "P1 11월/7월", "p1_nov_pct_max_abs")
            require(number(nov["BELOW_BASE_STREAK"], "P1 below", integer=True) ==
                    number(row["a_p1_below_july_months"], "below", integer=True), "P1 유지개월 불일치")
            require(number(row["a_sk_positions_n"], "N", integer=True) == number(nov["N"], "P1 N", integer=True),
                    "P1 관측 위치 수 불일치")
    return {"status": "passed", "roa_rows": len(rows), "dong_count": len(legacy), "reference_months": c["reference_months"],
            "comparable_rows": 63, "comparable_dongs": 21, "hold_dongs": 1, "p1_filtered_rows": len(activity),
            "legacy_signal_matches": 63, "many_to_many_join": False, "errors": errors,
            "scope": "입력 정합성·구규칙 재현. 신규 분류 타당성 및 표준화 원분석 재현 아님"}
