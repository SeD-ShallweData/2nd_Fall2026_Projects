"""STEP 2: R/O 네 패턴 × A 유무. 원문 Decimal 비교, 순위·점수·복지사업 추천 없음."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import math

from roa_data import RoaValidationError, decimal_value, require

RULE_VERSION = "roa-eight-combinations.v1"
REFERENCE_MONTHS = ["202510", "202511", "202512"]
WINDOWS = {
    "202510": ["202507", "202508", "202509"],
    "202511": ["202508", "202509", "202510"],
    "202512": ["202509", "202510", "202511"],
}
PATTERNS = {("OFF", "OFF"): "NONE", ("ON", "OFF"): "R", ("OFF", "ON"): "O", ("ON", "ON"): "RO"}
PATTERN_NAMES = {"NONE": "R/O 상대기준 미충족", "R": "연락 신호만", "O": "평일 외출 신호만", "RO": "연락·평일 외출 신호"}
CAVEATS = [
    "R/O는 거주 1인가구의 모형 추정 신호이고 A는 방문자를 포함한 지역 활동 변화다.",
    "R/O 동반은 동일 개인에게 두 특성이 겹친다는 뜻이 아니다.",
    "R/O OFF는 해당 기간 상대기준 미충족이며 관계망 양호·고립 없음의 판정이 아니다.",
    "A OFF는 두 감소조건을 모두 충족하지 못했다는 뜻이며 활동 유지의 판정이 아니다.",
    "A ON은 활동 감소 신호 — 두 비교 기준 충족이며 매월 연속 감소를 뜻하지 않는다.",
    "두 활동 창은 겹치며 두 A 비교는 독립 검증이 아니다. 종합 A에 SK 12월은 포함되지 않는다.",
    "관계망 직전 3개월 산정기간과 SK EPSG:5179는 공급자 미확인 가정이다.",
    "지역 단위 신호이며 개인의 고립·심리 상태·거동 불편·외출 감소를 단정할 수 없다.",
    "지원 검토 방향은 정책 검토용이다. 실제 복지사업 추천·자격 확인·자원 부족 판정이 아니다.",
    "유형은 위험 등급·우선순위·고립 확률을 의미하지 않는다.",
]


def _question(identifier, axis, text):
    return {"id": identifier, "axis": axis, "text": text}


def _direction(identifier, axis, text, precondition):
    return {"id": identifier, "axis": axis, "text": text, "precondition": precondition}


def guidance(pattern, activity_state):
    """관계망 점검과 지역 활동 원인 점검을 병렬로 구성한다."""
    questions, directions, caveats = [], [], list(CAVEATS)
    if pattern in ("R", "RO"):
        questions.append(_question("CONTACT_CONTEXT", "R", "연락 빈도와 대상·연락 수단·원하는 교류 방식에 관해 주민 수요와 현장 정보로 추가 확인할 수 있나요?"))
        directions.append(_direction("CONTACT_SUPPORT_REVIEW", "R", "안부확인·말벗·관계형성 프로그램 검토, 이용 가능한 상담기관의 정보 안내를 검토합니다.",
                                     "주민의 의사·수요 및 실제 기관의 운영·이용 조건 확인"))
        caveats.append("연락이 적다는 추정 신호만으로 심리적 어려움이나 사회적 고립을 단정하지 않는다.")
    if pattern in ("O", "RO"):
        questions.append(_question("WEEKDAY_ACCESS", "O", "평일 외출의 시간대·장소·서비스 접근성·비대면 이용 가능성과 업무지구 등 지역 맥락을 확인했나요?"))
        directions.append(_direction("OUTING_ACCESS_REVIEW", "O", "비대면 접근성과 이용 시간·장소를 점검하고, 주민 수요가 확인되면 방문·동행 지원을 검토합니다.",
                                     "주민 수요·동의와 이동·접근 조건의 현장 확인"))
        caveats.append("평일 외출이 적다는 추정 신호만으로 거동 불편을 단정하지 않는다. 지역 특성이 영향을 줄 수 있다.")
    if pattern == "RO":
        questions.append(_question("CONTACT_AND_ACCESS", "RO", "연락 관련 수요와 평일 외출·접근성 관련 수요를 각각 확인했나요? 두 집단의 개인 수준 중첩은 별도 자료로 확인했나요?"))
        directions.append(_direction("RELATION_AND_ACCESS_REVIEW", "RO", "관계 형성과 서비스 접근성을 함께 검토하되 연락·외출 점검 근거를 따로 유지합니다.",
                                     "개인 수준 중첩을 가정하지 않는 지역 단위 검토"))
    if activity_state == "ON":
        questions.append(_question("ACTIVITY_ALTERNATIVES", "A", "계절·상권·시설 운영 변화·방문 수요·관측 범위 등 지역 활동 감소의 다른 원인을 확인했나요?"))
        directions.append(_direction("ACTIVITY_CAUSE_REVIEW", "A", "지역 활동 감소 원인을 점검합니다. A만으로 주민 개인의 외출 감소나 직접적인 복지 지원 필요성을 판단하지 않습니다.",
                                     "지역·기간·시설·방문 수요의 추가 근거 확인"))
    if pattern == "NONE":
        questions.append(_question("LIMITED_NEGATIVE_FINDING", "RO", "세 기준월 전체 기준은 미충족이지만 일부 월의 초과나 다른 기간의 변화가 있었나요? 자료 품질과 미관측 수요를 확인했나요?"))
        if activity_state == "OFF":
            directions.append(_direction("MONITOR_EVIDENCE", "ROA", "이번 규칙에서 특별한 취약 신호를 확인하지 못한 제한적 결과로 설명하고 추세와 자료 한계를 모니터링합니다.",
                                         "기준 미충족을 관계망 양호·활동 유지로 확대하지 않음"))
    return {"additional_questions": questions, "support_review_directions": directions,
            "caveats": list(dict.fromkeys(caveats))}


def type_catalog():
    """빈 유형도 보존하는 범주 목록. 순서는 직렬화용이며 위험 순서가 아니다."""
    catalog = []
    states = {"NONE": ("OFF", "OFF"), "R": ("ON", "OFF"), "O": ("OFF", "ON"), "RO": ("ON", "ON")}
    for pattern in ("NONE", "R", "O", "RO"):
        for activity_state in ("OFF", "ON"):
            code = f"{pattern}_A{1 if activity_state == 'ON' else 0}"
            catalog.append({"type": code, "display_name": f"{PATTERN_NAMES[pattern]} · A 두 감소조건 {'충족' if activity_state == 'ON' else '미충족'}",
                            "relationship_pattern": pattern, "A_state": activity_state,
                            "signal_composition": dict(zip(("R", "O", "A"), (*states[pattern], activity_state))),
                            "rule_version": RULE_VERSION, **guidance(pattern, activity_state)})
    return catalog


def _numeric(value, label, original_text=None):
    """None은 결측으로 유지. bool·숫자 문자열·NaN을 숫자로 묵시 변환하지 않는다."""
    if value is None:
        return None
    require(type(value) in (int, float) and math.isfinite(value), f"{label}: 유한 JSON 숫자 필요")
    if original_text is not None:
        exact = decimal_value(original_text, label)
        require(float(exact) == value, f"{label}: 원문과 JSON 숫자 불일치")
        return exact
    return Decimal(str(value))


def _months(dong):
    require(dong["period_summary"]["assessment_period_id"] == "REF_202510_202512" and
            dong["period_summary"]["reference_months"] == REFERENCE_MONTHS, "종합 판정 기간 불일치")
    rows = dong["monthly_evidence"]
    require(isinstance(rows, list) and len(rows) == 3, "R/O 월별 근거는 세 기준월 필요")
    by_month = {}
    for row in rows:
        month = row["reference_month"]
        require(month in REFERENCE_MONTHS and month not in by_month, "월별 근거 기준월 중복/범위 오류")
        periods = row["periods"]
        require(periods["relationship_calculation"]["months"] == WINDOWS[month] and
                periods["sk_activity_window"]["months"] == WINDOWS[month] and
                periods["relationship_calculation"]["status"] == "ASSUMED_PREV_3M_UNVERIFIED",
                "기준월과 실제 측정 창/미확인 상태 불일치")
        by_month[month] = row
    return by_month


def evaluate_relationship(monthly, signal):
    require(signal in ("R", "O"), "R 또는 O 신호 필요")
    evidence = []
    for month in REFERENCE_MONTHS:
        row = monthly[month]
        item = row[signal]
        raw = row.get("source_numeric_text", {})
        value = _numeric(item.get("standardized_pct"), f"{signal}/{month}/value", raw.get(f"{signal.lower()}_score_std_pct"))
        threshold = _numeric(item.get("legacy_median_21_pct"), f"{signal}/{month}/median", raw.get(f"{signal.lower()}_threshold_median_21"))
        require(type(item.get("interpretable")) is bool, f"{signal}/{month}: interpretable은 boolean 필요")
        valid = value is not None and threshold is not None and item["interpretable"] and item.get("value_status") == "OBSERVED"
        if value is not None:
            require(0 <= value <= 100, f"{signal}/{month}: 비율 범위 오류")
        if threshold is not None:
            require(0 <= threshold <= 100, f"{signal}/{month}: 중앙값 범위 오류")
        above = value > threshold if valid else None
        evidence.append({"reference_month": month, "standardized_pct": item.get("standardized_pct"),
                         "median_21_pct": item.get("legacy_median_21_pct"), "operator": ">", "above_median": above,
                         "comparison_decimal_text": {"value": str(value) if value is not None else None,
                                                     "median": str(threshold) if threshold is not None else None},
                         "value_status": item.get("value_status"), "interpretable": item["interpretable"],
                         "status": "CALCULATED" if valid else "UNDETERMINED", "source_id": "SRC_REL"})
    state = "UNDETERMINED" if any(e["above_median"] is None for e in evidence) else \
            "ON" if all(e["above_median"] for e in evidence) else "OFF"
    return {"state": state, "scope": "ALL_THREE_REFERENCE_MONTHS", "required_months": REFERENCE_MONTHS,
            "above_months": [e["reference_month"] for e in evidence if e["above_median"] is True],
            "monthly_evidence": evidence, "rule": "세 기준월 모두 동일 월 21동 중앙값 엄격 초과"}


def evaluate_activity(dong, monthly):
    item = dong["period_summary"]["A_comparison_evidence"]
    window, months = item["window_comparison"], item["month_comparison"]
    require(window["base"]["months"] == WINDOWS["202510"] and window["target"]["months"] == WINDOWS["202512"] and
            months["base_month"] == "202507" and months["target_month"] == "202511", "A 비교기간 불일치")
    results, diagnostics = {}, []
    for name, comparison in (("window_comparison", window), ("month_comparison", months)):
        base_text = monthly["202510"].get("source_numeric_text", {}).get("a_activity_window_level") if name == "window_comparison" else None
        target_text = monthly["202512"].get("source_numeric_text", {}).get("a_activity_window_level") if name == "window_comparison" else None
        base = _numeric(comparison.get("base_level"), f"A/{name}/base", base_text)
        target = _numeric(comparison.get("target_level"), f"A/{name}/target", target_text)
        rate_key = "a_window_change_pct" if name == "window_comparison" else "a_p1_nov_vs_jul_pct"
        rate = _numeric(comparison.get("change_pct"), f"A/{name}/rate",
                        monthly["202512"].get("source_numeric_text", {}).get(rate_key))
        if base is not None:
            require(base > 0, f"A/{name}: 양수 비교 분모 필요")
        if target is not None:
            require(target >= 0, f"A/{name}: 비음수 대상 수준 필요")
        valid = base is not None and target is not None and rate is not None
        decreased = target < base if valid else None
        if valid and decreased != (rate < 0):
            diagnostics.append({"code": "LEVEL_VS_REPORTED_RATE_DIRECTION_DIFFERENCE", "comparison": name,
                                "level_decreased": decreased, "reported_rate_negative": rate < 0,
                                "action": "원문 수준 비교를 유지하고 기존 변화율 방향과의 차이를 보고"})
        results[name] = {"periods": {k: deepcopy(v) for k, v in comparison.items() if k in ("base", "target", "base_month", "target_month")},
                         "base_level": comparison.get("base_level"), "target_level": comparison.get("target_level"),
                         "change_pct": comparison.get("change_pct"), "operator": "<", "decreased": decreased,
                         "comparison_decimal_text": {"base": str(base) if base is not None else None,
                                                     "target": str(target) if target is not None else None},
                         "precision_source": "ROA_ORIGINAL_DECIMAL_TEXT" if name == "window_comparison" else "STEP1_JSON_NUMERIC_TEXT",
                         "status": "CALCULATED" if valid else "UNDETERMINED", "source_id": "SRC_ACT"}
    # A 관측 품질은 R/O 보류와 독립이다. 결측을 비감소로 바꾸지 않는다.
    require(all(type(monthly[m]["A"].get("observation_available")) is bool for m in REFERENCE_MONTHS),
            "A 관측 가능 여부는 boolean 필요")
    available = all(monthly[m]["A"].get("observation_available") is True and
                    monthly[m]["A"].get("value_status") == "OBSERVED" for m in REFERENCE_MONTHS)
    state = "UNDETERMINED" if not available or any(e["decreased"] is None for e in results.values()) else \
            "ON" if all(e["decreased"] for e in results.values()) else "OFF"
    return {"state": state, "scope": "WINDOW_AND_JULY_NOVEMBER_COMPARISONS", "comparisons": results,
            "display_label": "활동 감소 신호 — 두 비교 기준 충족" if state == "ON" else
                             "두 활동 감소조건 전체 기준 미충족" if state == "OFF" else "활동 감소 신호 판정 불가",
            "observation_available": available, "independent_validations": False,
            "monthly_continuous_decrease_claimed": False, "minimum_decrease_threshold_added": False,
            "numeric_diagnostics": diagnostics}


def classify_dong(dong):
    """검증된 STEP 1 동 한 건을 판정한다. legacy·상세 탐색 선택값을 참조하지 않는다."""
    try:
        monthly = _months(dong)
        eligibility = dong["classification_eligibility"]
        require(type(eligibility["eligible"]) is bool, "분류 가능 여부는 boolean 필요")
        require(isinstance(eligibility.get("reasons", []), list), "보류 이유는 배열 필요")
        signals = {s: evaluate_relationship(monthly, s) for s in ("R", "O")}
        signals["A"] = evaluate_activity(dong, monthly)
        composition = {s: signals[s]["state"] for s in ("R", "O", "A")}
        reasons = deepcopy(eligibility.get("reasons", [])) if not eligibility["eligible"] else []
        if not eligibility["eligible"] and not reasons:
            reasons.append({"code": "DATA_QUALITY_GATE_HOLD", "fields": ["QUALITY"], "detail": "입력 품질 관문에서 분류 제외"})
        for signal, state in composition.items():
            if state == "UNDETERMINED":
                reasons.append({"code": "SIGNAL_UNDETERMINED", "fields": [signal],
                                "detail": "결측·품질 상태로 신호를 판정할 수 없음. OFF로 대체하지 않음."})
        hold = not eligibility["eligible"] or bool(reasons)
        pattern = PATTERNS.get((composition["R"], composition["O"]))
        code = None if hold else f"{pattern}_A{1 if composition['A'] == 'ON' else 0}"
        template = next((t for t in type_catalog() if t["type"] == code), None)
        if template:
            explanation = {k: deepcopy(template[k]) for k in ("additional_questions", "support_review_directions", "caveats")}
        else:
            explanation = {"additional_questions": [_question("VERIFY_HELD_DATA", "QUALITY", "반복 0·결측의 원인과 사용 가능한 근거를 확인할 수 있나요?")],
                           "support_review_directions": [], "caveats": list(CAVEATS) + ["판정 보류 지역을 8가지 유형에 강제로 배정하지 않는다."]}
            if composition["A"] == "ON":
                # A의 지역 원인 점검만 가능하며 R/O에 기반한 지원 해석은 생성하지 않는다.
                a_guidance = guidance("NONE", "ON")
                explanation["additional_questions"] += [q for q in a_guidance["additional_questions"] if q["axis"] == "A"]
                explanation["support_review_directions"] += a_guidance["support_review_directions"]
        observations = []
        for s in ("R", "O"):
            state = signals[s]["state"]
            valid_months = sum(e["status"] == "CALCULATED" for e in signals[s]["monthly_evidence"])
            observations.append({"axis": s, "text": f"세 기준월 중 판정 가능 {valid_months}개월, 중앙값 초과 {len(signals[s]['above_months'])}개월. 전체기간 신호 {state}.",
                                 "evidence_path": f"classification.signals.{s}.monthly_evidence"})
        observations.append({"axis": "A", "text": signals["A"]["display_label"], "evidence_path": "classification.signals.A.comparisons"})
        return {"status": "HOLD" if hold else "CLASSIFIED", "type": code,
                "display_name": "판정 보류 — 자료 확인 필요" if hold else template["display_name"],
                "relationship_pattern": pattern, "A_state": composition["A"], "signal_composition": composition,
                "rule_version": RULE_VERSION, "assessment_period_id": "REF_202510_202512",
                "reference_months": list(REFERENCE_MONTHS), "signals": signals, "observed_evidence": observations,
                "hold_reasons": reasons, **explanation}
    except (KeyError, TypeError) as error:
        raise RoaValidationError(f"분류 입력 구조 오류: {error}") from error


def summarize(dongs):
    counts = {item["type"]: {"display_name": item["display_name"], "count": 0, "dongs": []} for item in type_catalog()}
    holds = []
    for dong in dongs:
        result = dong["classification"]
        entry = {"adm_cd8": dong["region"]["adm_cd8"], "adm_nm": dong["region"]["adm_nm"]}
        if result["status"] == "HOLD":
            holds.append(entry)
        else:
            counts[result["type"]]["count"] += 1
            counts[result["type"]]["dongs"].append(entry)
    return {"types": counts, "classified_count": sum(t["count"] for t in counts.values()), "hold_count": len(holds), "hold_dongs": holds}


def compare_legacy(dongs):
    """신규 판정 후 별도로 실행하는 대조. 불일치 시 결과를 고치지 않고 기록한다."""
    mismatches, held_differences = [], []
    checked = 0
    for dong in dongs:
        classification = dong["classification"]
        for signal in ("R", "O", "A"):
            new = classification["signal_composition"][signal]
            old = dong["legacy"]["csv_period_signals"][signal]["state"]
            if classification["status"] == "CLASSIFIED":
                checked += 1
                if new != old:
                    mismatches.append({"adm_cd8": dong["region"]["adm_cd8"], "signal": signal, "new": new, "legacy": old})
            elif new != old:
                held_differences.append({"adm_cd8": dong["region"]["adm_cd8"], "signal": signal, "new": new,
                                         "legacy": old, "interpretation": "전체 유형은 HOLD, 관측 가능한 축은 별도 보존"})
    return {"compared_signal_count": checked, "mismatches": mismatches, "held_signal_differences": held_differences,
            "scope": "구규칙과 독립 계산 결과의 대조. 고립 예측 성능 검증이 아님"}
