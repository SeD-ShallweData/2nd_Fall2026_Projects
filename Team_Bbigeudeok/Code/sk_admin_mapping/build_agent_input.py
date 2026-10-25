"""STEP 1 공용 JSON 생성/읽기. 신규 분류·UI·복지·LLM과 독립된 로컬 데이터 계층."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

from roa_data import (NUMERIC_FIELDS, ROOT, RoaValidationError, flag, load_inputs, number,
                      read_json_strict, require, sha256, unique_index, validate_inputs,
                      verify_sources_unchanged, window_label)

CLASSIFICATION_STATUS = "NOT_IMPLEMENTED_STEP2"
OUTPUT_RELATIVE = "Code/sk_admin_mapping/outputs/canonical/agent/agent_input_integrated.v1.json"
HOLD_REASON = {
    "code": "RELATION_ZERO_CAUSE_UNVERIFIED",
    "fields": ["R", "O"],
    "detail": "개포3동(구 일원2동)의 관계망 반복 0 원인 미확인. 원값은 보존하되 기준 미충족으로 해석하지 않음.",
}
CAVEATS = [
    "R/O는 거주 1인가구 관련 모형 추정 신호이고 A는 방문자를 포함한 관측 위치 활동이다.",
    "R/O 동반은 동일 개인에게 두 특성이 겹친다는 뜻이 아니다.",
    "기준 미충족은 안전·고립 없음의 판정이 아니며 결측·보류와 구분한다.",
    "세 활동 창은 겹친다. 두 A 비교는 독립 검증이나 매월 연속 감소가 아니다.",
    "관계망 직전 3개월 산정과 SK EPSG:5179는 미확인 가정이다.",
    "현재 종합 A에는 SK 12월이 포함되지 않는다. 월별 상세 선택이 종합 기간을 변경하지 않는다.",
    "구규칙 수치 재현은 신규 유형 타당성·고립 예측 정확도 검증이 아니다.",
    "복지자원 부족·지원 우선순위는 이 데이터만으로 판단하지 않는다.",
]


def canonical_bytes(document):
    """시각·mtime·절대 경로를 넣지 않는 결정적 UTF-8 JSON. 비유한 값은 실패한다."""
    return (json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _period(months):
    return {"months": list(months), "label": window_label(months)}


def _monthly(row, contract):
    month = row["ref_month"]
    hold = row["adm_cd8"] == contract["hold_adm_cd8"]
    record = {
        "reference_month": month,
        "periods": {
            "relationship_calculation": {**_period(contract["windows"][month]),
                                         "status": row["rel_calc_period_status"]},
            "sk_activity_window": _period(contract["windows"][month]),
            "sk_base_window": _period(contract["windows"]["202510"]),
            "p1_comparison": {"from_month": "202507", "to_month": "202511"},
        },
        "estimated_single_households": number(row["single_hh_est"], "hh"),
        "min_stratum_denominator": number(row["min_stratum_denominator"], "min denominator"),
        "A": {
            "activity_window_level": number(row["a_activity_window_level"], "A level"),
            "window_vs_base_change_pct": number(row["a_window_change_pct"], "window pct"),
            "nov_vs_jul_change_pct": number(row["a_p1_nov_vs_jul_pct"], "nov pct"),
            "nov_below_july_streak_months": number(row["a_p1_below_july_months"], "below", integer=True),
            "sk_positions_n": number(row["a_sk_positions_n"], "N", integer=True),
            "value_status": row["a_value_status"],
            "observation_available": True,
            "source_id": "SRC_ACT",
        },
        # JSON numbers preserve float round trips; original decimal text is also retained losslessly.
        "source_numeric_text": {key: row[key] for key in NUMERIC_FIELDS},
    }
    for signal in ("r", "o"):
        record[signal.upper()] = {
            "standardized_pct": number(row[f"{signal}_score_std_pct"], signal),
            "raw_rate_pct": number(row[f"{signal}_raw_rate_pct"], signal),
            "group_estimate": number(row[f"{signal}_group_est"], signal),
            "legacy_median_21_pct": number(row[f"{signal}_threshold_median_21"], signal),
            "legacy_above_median": flag(row[f"{signal}_above_threshold"], signal),
            "value_status": row[f"{signal}_value_status"],
            "interpretable": not hold,
            "source_id": "SRC_REL",
        }
    return record


def build_document(data=None, *, project_root=None):
    """검증을 통과한 입력만 통합한다. legacy 구신호를 최종 신호/유형으로 승격하지 않는다."""
    data = data or load_inputs(project_root)
    validation = validate_inputs(data)
    c = data.contract
    rows = unique_index(data.rows, ("adm_cd8", "ref_month"), "ROA")
    old = unique_index(data.legacy["dongs"], ("adm_cd8",), "JSON")
    p1 = unique_index([r for r in data.activity if
                       (r["TABLE"], r["BASIS"], r["LEVEL"], r["METRIC"], r["CITY"]) ==
                       ("flow_age_pop", "original", "DONG", "TOTAL", "강남구")], ("CODE", "MONTH"), "P1")
    months = c["reference_months"]
    dongs = []
    for (cd,), previous in sorted(old.items()):
        monthly = [_monthly(rows[(cd, m)], c) for m in months]
        first, last = rows[(cd, months[0])], rows[(cd, months[-1])]
        hold = cd == c["hold_adm_cd8"]
        signals = {sig: {"state": None if hold else ("ON" if flag(first[f"{sig.lower()}_signal_v1"], sig) else "OFF"),
                         "raw_csv_token": first[f"{sig.lower()}_signal_v1"]} for sig in ("R", "O", "A")}
        for sig in signals:
            if signals[sig]["state"] is None:
                signals[sig]["state"] = "UNDETERMINED"
        sk_monthly = []
        for month in [f"2025{m:02d}" for m in range(7, 13)]:
            item = p1[(cd, month)]
            sk_monthly.append({
                "observation_month": month,
                "included_in_comprehensive_A": month != "202512",
                "activity_level": number(item["VALUE"], "P1 VALUE"),
                "base_july_level": number(item["BASE"], "P1 BASE"),
                "vs_july_change_pct": number(item["BASE_PCT"], "P1 base pct"),
                "vs_previous_month_change_pct": number(item["MOM_PCT"], "P1 mom", nullable=month == "202507"),
                "current_month_on_month_decrease_streak": number(item["CURRENT_STREAK"], "streak", integer=True),
                "below_july_streak_months": number(item["BELOW_BASE_STREAK"], "below", integer=True),
                "sk_positions_n": number(item["N"], "N", integer=True),
                "source_id": "SRC_ACT",
            })
        dongs.append({
            "region": {"sigungu_cd": first["sigungu_cd"], "sigungu_nm": first["sigungu_nm"],
                       "adm_cd8": cd, "adm_nm": first["adm_nm"]},
            "crosswalk": {"adm_cd8": cd, "ext_cd7": first["ext_cd7"], "ext_nm": first["ext_nm"],
                          "method": "EXPLICIT_SOURCE_CROSSWALK", "source_input": "crosswalk",
                          "boundary_identity_status": "NOT_INDEPENDENTLY_VERIFIED"},
            "monthly_evidence": monthly,
            "sk_monthly_detail": sk_monthly,
            "period_summary": {
                "assessment_period_id": "REF_202510_202512",
                "reference_months": months,
                "R_O_evidence_months": months,
                "A_comparison_evidence": {
                    "display_label": "활동 감소 신호 — 두 비교 기준 충족",
                    "window_comparison": {
                        "base": _period(c["windows"][months[0]]), "target": _period(c["windows"][months[-1]]),
                        "base_level": monthly[0]["A"]["activity_window_level"],
                        "target_level": monthly[-1]["A"]["activity_window_level"],
                        "change_pct": number(last["a_window_change_pct"], "window pct"),
                    },
                    "month_comparison": {
                        "base_month": "202507", "target_month": "202511",
                        "base_level": sk_monthly[0]["activity_level"], "target_level": sk_monthly[4]["activity_level"],
                        "change_pct": number(last["a_p1_nov_vs_jul_pct"], "nov pct"),
                    },
                    "requires_both_decreases_in_provisional_rule": True,
                    "independent_validations": False,
                    "minimum_decrease_threshold_added": False,
                },
            },
            "classification_eligibility": {
                "eligible": not hold, "status": "HOLD" if hold else "ELIGIBLE_WITH_UNVERIFIED_ASSUMPTIONS",
                "reasons": [dict(HOLD_REASON)] if hold else [],
                "scope": "데이터 품질 관문. 신규 유형 판정·모형 타당성 검증이 아님",
            },
            "classification": {"status": CLASSIFICATION_STATUS, "type": None, "rule_version": None},
            "quality": {"R": first["r_value_status"], "O": first["o_value_status"], "A": first["a_value_status"],
                        "notes": [first["quality_note"]] if hold else [],
                        "a_observation_preserved_independently_of_classification": True},
            "legacy": {
                "purpose": "REPRODUCTION_AND_COMPARISON_ONLY",
                "period_scope": "ALL_THREE_REFERENCE_MONTHS_NOT_MONTHLY_SIGNALS",
                "csv_rule_version": c["legacy_csv_rule_version"],
                "json_rule_version": c["legacy_json_rule_version"],
                "csv_in_main_comparison": not hold,
                "csv_period_signals": signals,
                "json_signal_states": {sig: previous["signals"][sig]["state"] for sig in ("R", "O", "A")},
                "comparison_status": "HOLD_A_JSON_ON_CSV_UNDETERMINED" if hold else "ALL_THREE_SIGNALS_MATCH",
            },
        })
    sources = data.legacy["meta"]["sources"]
    source_rel = next(s for s in sources if s["id"] == "SRC_REL")
    doc = {
        "meta": {
            "schema_version": c["schema_version"], "contract_version": c["contract_version"],
            "integration_version": c["integration_version"], "stage": "STEP1_PRE_CLASSIFICATION",
            "classification_status": CLASSIFICATION_STATUS,
            "assessment_period": {"id": "REF_202510_202512", "label": "2025년 10~12월 기준월 종합 점검",
                                  "reference_months": months, "independent_of_sk_detail_selection": True},
            "region": {**c["region"], "n_dong": 22, "classification_eligible_dongs": 21, "hold_dongs": 1},
            "signal_semantics": {
                "R": "거주 추정 1인가구의 연락 관련 신호", "O": "거주 추정 1인가구의 평일 외출 관련 신호",
                "A": "방문자를 포함한 지역 활동 변화 신호",
                "legacy_R_O_rule": "각 기준월 21동 중앙값을 엄격히 초과한 상태가 세 기준월 모두 성립",
                "provisional_A_rule": "9~11월 창 < 7~9월 창 AND 11월 < 7월; 신규 판정은 STEP 2",
            },
            "future_classification_contract": {
                "status": CLASSIFICATION_STATUS, "relationship_pattern_count": 4, "activity_state_count": 2,
                "planned_combination_count": 8, "risk_order_assigned": False,
                "signal_count_ranking_allowed": False, "O_is_secondary": False,
            },
            "assumptions": {"relationship_period": c["relation_period_status"],
                            "sk_crs": "ASSUMED_UNVERIFIED", "sk_crs_assumption": "EPSG:5179"},
            "generation": {"deterministic": True, "contract_sha256": data.contract_sha256,
                           "implementation_sha256": {name: sha256(Path(__file__).with_name(name))
                                                      for name in ("roa_data.py", "build_agent_input.py")}},
            "inputs": data.provenance,
            "sources": {
                "SRC_REL": {"name": source_rel["name"], "owner": source_rel["owner"],
                            "origin_url": source_rel["origin_url"], "available_processed_input": "roa",
                            "upstream_reported_sha256": source_rel["sha256"],
                            "upstream_local_availability": "UNAVAILABLE_IN_PROJECT_CONFIRMED",
                            "standardization_reproduction": "UNVERIFIED_GENERATOR_AND_WEIGHTS_UNAVAILABLE"},
                "SRC_ACT": {**data.provenance["activity"],
                            "grain_filter": {"TABLE": "flow_age_pop", "BASIS": "original", "LEVEL": "DONG",
                                             "METRIC": "TOTAL", "CITY": "강남구"}},
                "SRC_QA": {"available_processed_input": "roa", "source_note_status": "PRESERVED_FROM_ROA",
                           "cause_of_repeated_zero": "UNVERIFIED"},
            },
            "numeric_policy": {
                "values": "FLOAT64_WITH_ORIGINAL_DECIMAL_TEXT", "threshold_comparison": "STRICT_GREATER_THAN",
                "legacy_A_reproduction": "STRICT_LESS_THAN_ZERO_NO_NEW_MINIMUM",
                "verification_tolerances": c["verification_tolerances"],
                "tolerance_scope": "수치 대조 전용. 분류의 최소 감소폭이나 판단 임계값이 아님.",
            },
            "excluded_legacy_fields": ["level", "level_map", "possible_range", "signal_count"],
            "excluded_unverified_context": ["activity_density_high_all_months", "activity_per_km2_sep_nov", "contact_rank_dec"],
            "caveats": list(dict.fromkeys(data.legacy["meta"]["caveats"] + CAVEATS)),
            "validation": validation,
        },
        "dongs": dongs,
    }
    verify_sources_unchanged(data)
    return doc


def output_path(project_root=None):
    return Path(project_root or ROOT).resolve() / OUTPUT_RELATIVE


def write_integrated(project_root=None):
    """완전 검증 후 신규 전용 경로에만 원자적으로 기록. 기존 입력을 덮어쓸 수 없다."""
    doc = build_document(project_root=project_root)
    path = output_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(doc)
    if path.is_file() and path.read_bytes() == payload:
        return path, doc
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path, doc


def load_integrated(project_root=None):
    """Dashboard/Agent용 공통 읽기 진입점. 오래되거나 임의 변경된 산출물은 거부한다."""
    path = output_path(project_root)
    require(path.is_file(), "통합 JSON 없음. build_agent_input.py를 먼저 실행하세요.")
    actual = read_json_strict(path)
    expected = build_document(project_root=project_root)
    require(canonical_bytes(actual) == canonical_bytes(expected),
            "통합 JSON이 현재 입력/계약/코드와 다릅니다. build_agent_input.py로 재생성하세요.")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="기존 JSON을 읽기 전용 검증")
    args = parser.parse_args()
    try:
        if args.check:
            doc = load_integrated(args.project_root)
            path = output_path(args.project_root)
        else:
            path, doc = write_integrated(args.project_root)
        print(json.dumps({"status": "passed", "path": str(path), "schema": doc["meta"]["schema_version"],
                          "dong_count": len(doc["dongs"]), "classification": CLASSIFICATION_STATUS}, ensure_ascii=False))
    except (RoaValidationError, OSError, KeyError, TypeError) as error:
        print(f"ROA 통합 실패: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
