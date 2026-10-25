"""STEP 3: 출처를 보존하는 복지자원 정규화·기간/조건 검색. 네트워크와 UI 호출 없음."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from html import unescape
from pathlib import Path
import argparse
import csv
import hashlib
import json
import re

from build_agent_input import canonical_bytes
from roa_data import ROOT, read_json_strict, require, sha256

WORK = Path(__file__).resolve().parent
CONTRACT = WORK / "welfare_contract.json"
CATALOG = WORK / "outputs/canonical/welfare/welfare_catalog.v1.json"
SCHEMA = "welfare-catalog.v1"
HISTORICAL = ("2025-07-01", "2025-11-30")
BUCKETS = {"candidate": "연계 검토 후보", "needs_confirmation": "자격·운영 확인 필요", "excluded": "적용 제외"}
AXIS_TAGS = {"R": {"checkin", "community", "counseling_information"},
             "O": {"remote", "home_visit", "accompaniment", "access_hours", "access_review"}}
CAUTIONS = [
    "지역 단위 R/O 신호는 개인의 고립·심리 상태·거동 불편을 확정하지 않는다.",
    "R/O 동반은 동일 개인의 두 특성 중첩을 뜻하지 않는다. 위험 순위·지원 강도를 부여하지 않는다.",
    "A는 지역 활동 변화다. A만으로 직접 복지지원 필요성을 판단하지 않는다.",
    "2025년 신호로 현재 지원을 검토하며 현재 주민 상태는 별도 확인이 필요하다.",
    "검색 결과 없음은 서비스 부재·복지 공백의 증거가 아니다. 정원·대기·수요·효과는 미확인이다.",
]


def clean(value):
    # <일상이되는 정리> 같은 실제 제목은 보존하고 알려진 HTML 태그만 제거한다.
    text = re.sub(r"<\s*(script|style)\b[^>]*>.*?</\s*\1\s*>", " ", str(value or ""), flags=re.I | re.S)
    text = re.sub(r"</?(?:p|div|span|br|b|strong|i|em|u|a|img|table|tr|td|ul|ol|li|h[1-6]|font|mark)\b[^>]*>", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def iso_date(value):
    """문자열 전체가 날짜인 경우만 해석. 기사 날짜/연도/계획 문구는 운영일로 추정하지 않는다."""
    text = str(value or "").strip()
    if not text or text == "99991231":
        return None
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def period(start=None, end=None, raw=None, open_end=False):
    result = {"start": iso_date(start), "end": iso_date(end), "open_end": bool(open_end),
              "raw": raw if raw is not None else [start or "", end or ""], "issues": []}
    for label, value in (("start", start), ("end", end)):
        if value and not iso_date(value) and not (label == "end" and str(value) == "99991231"):
            result["issues"].append(f"INVALID_{label.upper()}_DATE")
    if result["start"] and result["end"] and result["start"] > result["end"]:
        result["issues"].append("REVERSED_DATES")
    return result


def curated_period(text):
    # 엄격한 일자 쌍 또는 명시적 개시일+열린 종료만. '2025년 시범 계획' 등은 미확인.
    values = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if len(values) == 2:
        return period(*values, raw=text)
    if len(values) == 1 and text.rstrip().endswith("~"):
        return period(values[0], raw=text, open_end=True)
    return period(raw=text)


def urls(text):
    return sorted(set(re.findall(r"https?://[^\s<>\)\],]+", text or "")))


def lineage(source, row, raw, retrieved_at=None):
    return {"source": source, "row": row, "method": "CSV_UTF8_SIG" if row is not None else "OPEN_API",
            "retrieved_at": retrieved_at, "raw": deepcopy(raw)}


def support_tags(text, curated_signal="", curated_reason=""):
    """본문 근거가 있는 검색 태그. 단어만으로 가정방문·개인 고립을 판단하지 않는다."""
    evidence = []
    patterns = {
        "checkin": r"안부확인|안부 확인|말벗|모니터링 콜백|채팅 상담",
        "community": r"관계형성|관계 형성|사회적 관계망|소모임|자조모임|커뮤니티\s*(?:활동|모임|프로그램)|서로.*소통|교류",
        "counseling_information": r"상담|심리지원",
        "remote": r"비대면|온라인(?:으로|\s*(?:강의|참여|진행|모임))|채팅|챗봇|우편|가정으로 발송",
        "home_visit": r"가정\s*방문|가정방문|집수리\s*방문|방문\s*집수리|방문형\s*지원",
        "accompaniment": r"병원\s*(?:안심)?동행|이동\s*동행|외출\s*동행",
        "access_hours": r"야간|주말|퇴근\s*후|저녁\s*\d",
    }
    for tag, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            evidence.append({"tag": tag, "basis": "CONTENT_TEXT_REVIEW", "excerpt": match.group(0)})
    # 선별본의 연결 축은 담당자의 검토 힌트. 옛 A/D 유형·우선순위는 해석하지 않는다.
    if re.match(r"\s*R(?:\b|\s|,|\()", curated_signal) and not any(x["tag"] in AXIS_TAGS["R"] for x in evidence):
        evidence.append({"tag": "community", "basis": "CURATOR_RELEVANCE_REVIEW_REQUIRED", "excerpt": "선별본 R 연결 표시"})
    if re.search(r"(?:^|[,\s])O(?:\b|\()", curated_signal) and not any(x["tag"] in AXIS_TAGS["O"] for x in evidence):
        evidence.append({"tag": "access_review", "basis": "CURATOR_ACCESS_REVIEW_REQUIRED", "excerpt": "선별본 O 연결 표시; 실제 접근 방식 확인 필요"})
    # '집수리 방문'처럼 명시적인 선별 근거만 반영. 단순 '찾아가는'은 매칭하지 않는다.
    match = re.search(patterns["home_visit"], curated_reason)
    if match and not any(x["tag"] == "home_visit" for x in evidence):
        evidence.append({"tag": "home_visit", "basis": "CURATOR_DESCRIPTION_REVIEW_REQUIRED", "excerpt": match.group(0)})
    return sorted({x["tag"] for x in evidence}), evidence


def eligibility(age, gender, target, scope_hint=""):
    age, gender, target, scope_hint = map(clean, (age, gender, target, scope_hint))
    bounds = []
    exact = re.search(r"(?:만\s*)?(\d{1,2})\s*[~∼\-]\s*(\d{1,2})\s*세", age + " " + target)
    if exact:
        bounds = [[int(exact[1]), int(exact[2])]]
    elif age and "프로그램별" not in age:
        # CSV의 연령대 선택지를 범위의 합집합으로 해석한다. 40~50대는 40~59세.
        for token in age.split(","):
            token = token.strip()
            m = re.fullmatch(r"(\d{1,2})\s*[~∼]\s*(\d{1,2})대", token)
            if m:
                bounds.append([int(m[1]), int(m[2]) + 9])
            elif token == "20대 이하":
                bounds.append([0, 29])
            elif token == "60대 이상":
                bounds.append([60, 150])
    scope = "UNKNOWN"
    # 기관 소재지/주최지역은 주민 자격의 근거가 아니다.
    if re.search(r"강남구(?:\s*(?:거주|내|민|주민)|\s*1인가구)", target):
        scope = "GANGNAM_RESIDENT"
    elif "서울" in target and re.search(r"주민등록|거주|시민|주민", target):
        scope = "SEOUL_RESIDENT"
    elif "서울" in target and "생활권" in target:
        scope = "SEOUL_LIVING_AREA"
    elif scope_hint.startswith("서울시 전역"):
        scope = "SEOUL_SERVICE_AREA_REVIEW"
    elif scope_hint == "강남구":
        scope = "GANGNAM_SERVICE_AREA_REVIEW"
    excluded_gangnam = "강남구 대상 아님" in scope_hint
    return {"age_text": age, "age_ranges": bounds, "age_unrestricted": "제한 없음" in age,
            "gender_text": gender, "target_text": target, "resident_scope": scope,
            "scope_evidence": target if scope in ("GANGNAM_RESIDENT", "SEOUL_RESIDENT", "SEOUL_LIVING_AREA") else scope_hint,
            "gangnam_excluded_as_reported": excluded_gangnam,
            "single_household_required": "1인가구" in target and "예비" not in target,
            "other_conditions_review_required": bool(target and target not in ("서울시민", "전체", "누구나", "제한 없음"))}


def base_resource(identifier, original_id, title, provider, kind, content, age="", gender="", target="", scope=""):
    tags, evidence = support_tags(content)
    return {"id": identifier, "original_id": original_id or None, "title": clean(title), "provider": clean(provider),
            "resource_type": kind, "content": clean(content), "eligibility": eligibility(age, gender, target, scope),
            "geography": {"provider_jurisdiction": None, "applicability_note": scope or None, "venue": None},
            "operating_period": period(), "application_period": period(),
            "application": {"method": None, "url": None, "contact": None, "capacity_text": None, "fee_text": None},
            "support_tags": tags, "tag_evidence": evidence, "curated": False,
            "provenance": [], "source_urls": [], "published_or_updated_at": None,
            "source_checked_at": None, "retrieved_at": None,
            "verification": {"operation": "UNVERIFIED", "recruitment": "UNVERIFIED", "capacity": "UNVERIFIED",
                             "freshness": "UNVERIFIED", "notes": [], "missing_fields": []}}


PROGRAM_FIELDS = {
    "PARTCPTN_ID": "참여프로그램 고유키", "PARTCPTN_SJ": "제목", "TY_NM": "구분", "SE_NM": "카테고리",
    "ATDRC_NM": "지역(소속)", "RCEPT_DE1": "시작 접수기간", "RCEPT_DE2": "종료 접수기간",
    "PROGRS_DE1": "시작 진행기간", "PROGRS_DE2": "종료 진행기간", "AGRDE_NM": "참여대상 연령",
    "SEXDSTN_NM": "참여대상 성별", "TRGET_INFO": "참여대상 정보", "TME": "회기수", "PARTCPTN_NMPR": "참여인원",
    "PARTCPT_CT_NM": "참가비 구분", "PARTCPT_AMOUNT": "참가비 금액", "RCEPT_MTH_NM": "접수방법",
    "RCEPT_MTH_LINK": "접수방법 링크", "PROGRS_INQRY": "진행문의", "PLACE_ADRES1": "진행장소 기본주소",
    "PLACE_ADRES2": "진행장보 상세주소", "CN": "내용(원본 복원)", "REG_DATE2": "작성일(yyyy-mm-dd)", "INSTT_NM": "담당 기관",
}


def normalize_program(row, source="enriched", row_number=None, retrieved_at=None):
    if "PARTCPTN_ID" in row:
        mapped = {label: str(row.get(field) or "") for field, label in PROGRAM_FIELDS.items()}
    else:
        mapped = row
    def get(key):
        return mapped.get(key, "")
    identifier = get("참여프로그램 고유키")
    require(bool(identifier and get("제목")), "프로그램 고유키/제목 누락")
    r = base_resource("program:" + identifier, identifier, get("제목"), get("담당 기관"), "TIME_LIMITED",
                      get("내용(원본 복원)"), get("참여대상 연령"), get("참여대상 성별"), get("참여대상 정보"))
    r["geography"].update(provider_jurisdiction=get("지역(소속)"),
                           venue=clean(get("진행장소 기본주소") + " " + get("진행장보 상세주소")))
    r["operating_period"] = period(get("시작 진행기간"), get("종료 진행기간"))
    r["application_period"] = period(get("시작 접수기간"), get("종료 접수기간"))
    r["application"] = {"method": get("접수방법") or None, "url": get("접수방법 링크") or None,
                        "contact": get("진행문의") or None, "capacity_text": get("참여인원") or None,
                        "fee_text": clean(get("참가비 구분") + " " + get("참가비 금액")) or None}
    r["source_urls"] = ["https://data.seoul.go.kr/dataList/OA-21309/S/1/datasetView.do"]
    r["published_or_updated_at"] = iso_date(get("작성일(yyyy-mm-dd)"))
    r["retrieved_at"] = retrieved_at
    r["provenance"] = [lineage(source, row_number, row, retrieved_at)]
    r["verification"]["notes"] = ["일정·참여인원은 게시 기록이며 현재 운영·잔여 정원·접수 가능 확인이 아니다.",
                                      "원문 조건 전체와 조기 마감·멤버십·예산 소진 여부를 기관에 확인해야 한다."]
    return finalize(r)


def curated_kind(row):
    text = row["자원유형"]
    if "계획" in text or "계획" in row["내용"]:
        return "PLAN"
    if "시범" in text:
        return "PILOT"
    if "행정" in text:
        return "ADMINISTRATIVE"
    if "안내" in text or "검색" in text:
        return "INFORMATION"
    if "상시" in text:
        return "ONGOING_REPORTED"
    return "UNKNOWN"


def attach_curated(resource, row, row_number):
    r = deepcopy(resource)
    r["curated"] = True
    r["provenance"].append(lineage("curated", row_number, row))
    r["curation"] = {"source_status": row["확인상태"], "source_checked_at_text": row["확인일"],
                     "signal_hint_raw": row["연결신호"], "legacy_type_or_priority_not_used": True,
                     "excluded_from_adult_linkage_by_curator": "연계 제외" in row["자원유형"],
                     "scope_note": row["강남 적용"], "attribute_conflicts": []}
    # 18건은 같은 ID와 제목·기관·운영일을 대조한 뒤 메타데이터만 덧붙인다.
    for key, value in (("title", clean(row["제목"])), ("provider", clean(row["담당기관"]))):
        if value and r[key] != value:
            r["curation"]["attribute_conflicts"].append(key)
    r["source_checked_at"] = iso_date(row["확인일"].split(" ")[0])
    tags, evidence = support_tags(r["content"] + " " + row["내용"], row["연결신호"], row["선정이유"])
    r["support_tags"] = tags
    r["tag_evidence"] = evidence
    r["verification"]["notes"].append("선별본 확인일은 기사/홈페이지 확인일이며 현재 운영 확인일이 아니다. 옛 유형·우선순위 문구는 검색 판단에 미사용.")
    return finalize(r)


def normalize_curated(row, row_number):
    # 고유키 없는 기관/정책은 제목만으로 합치지 않는다. 기관·출처·링크를 함께 해시한다.
    identity = [row[k] for k in ("제목", "담당기관", "출처", "접수링크")]
    identifier = "curated:" + hashlib.sha256(canonical_bytes(identity)).hexdigest()[:24]
    r = base_resource(identifier, None, row["제목"], row["담당기관"], curated_kind(row), row["내용"],
                      row["참여대상 연령"], "", row["참여대상 정보"], row["강남 적용"])
    r["geography"]["venue"] = row["진행장소"] or None
    r["operating_period"] = curated_period(row["진행기간"])
    r["application_period"] = curated_period(row["접수기간"])
    r["application"].update(method=row["접수기간"] or None, url=row["접수링크"] or None,
                             capacity_text=row["참여인원"] or None, fee_text=row["참가비"] or None)
    r["source_urls"] = urls(row["접수링크"] + " " + row["출처"])
    return attach_curated(r, row, row_number)


def finalize(r):
    missing = [key for key in ("provider", "content") if not r[key]]
    if not r["source_urls"]:
        missing.append("source_url")
    for key in ("contact", "url"):
        if not r["application"][key]:
            missing.append("application." + key)
    for key in ("operating_period", "application_period"):
        if not r[key]["start"] or (not r[key]["end"] and not r[key]["open_end"]):
            missing.append(key)
        missing.extend(key + "." + x for x in r[key]["issues"])
    r["verification"]["missing_fields"] = sorted(set(missing))
    return r


def read_csv(path, expected_columns=None):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        require(columns and len(columns) == len(set(columns)), "복지 CSV 중복/빈 헤더")
        if expected_columns:
            require(set(columns) == set(expected_columns), f"복지 CSV 스키마 변경: {Path(path).name}")
        rows = list(reader)
    require(all(None not in row and None not in row.values() for row in rows), "복지 CSV 행 길이 오류")
    return rows


def build_catalog():
    contract = read_json_strict(CONTRACT)
    inputs, hashes = {}, {}
    for key, item in contract["sources"].items():
        path = ROOT / item["path"]
        require(sha256(path) == item["sha256"], f"복지 입력 변경: {key}. 계약·검증을 갱신하세요.")
        hashes[key] = item["sha256"]
        inputs[key] = read_csv(path, item["columns"])
        require(len(inputs[key]) == item["rows"], f"복지 행 수 변경: {key}")
    records = {}
    for i, row in enumerate(inputs["enriched"], 2):
        r = normalize_program(row, "enriched", i)
        require(r["id"] not in records, "복원본 프로그램 ID 중복")
        records[r["id"]] = r
    # 본문 없는 구 파일과 모든 공유 속성·키 대조. 내용 길이/선별 플래그는 판단에 사용하지 않는다.
    seen = set()
    for i, row in enumerate(inputs["filtered"], 2):
        key = "program:" + row["참여프로그램 고유키"]
        require(key in records and key not in seen, "기존/복원 CSV ID 불일치·중복")
        seen.add(key)
        raw = records[key]["provenance"][0]["raw"]
        require(all(raw[k] == value for k, value in row.items() if k != "내용_길이"), "기존/복원 CSV 공유 속성 불일치")
        records[key]["provenance"].append(lineage("filtered", i, row))
    require(seen == set(records), "기존/복원 CSV ID 집합 불일치")
    merged = 0
    for i, row in enumerate(inputs["curated"], 2):
        if row["고유키"]:
            key = "program:" + row["고유키"]
            require(key in records and not records[key]["curated"], "선별본 ID 누락/중복")
            r = records[key]
            expected = curated_period(row["진행기간"])
            require(r["title"] == clean(row["제목"]) and r["provider"] == clean(row["담당기관"])
                    and all(r["operating_period"][k] == expected[k] for k in ("start", "end")),
                    "선별본 고유키와 실제 속성 충돌; 자동 병합 중단")
            records[key] = attach_curated(r, row, i)
            merged += 1
        else:
            r = normalize_curated(row, i)
            require(r["id"] not in records, "고유키 없는 선별 자원 중복")
            records[r["id"]] = r
    return {"meta": {"schema_version": SCHEMA, "rule_version": "welfare-search.v1",
                     "sources": contract["sources"], "input_sha256": hashes,
                     "implementation_sha256": sha256(Path(__file__)), "historical_period": list(HISTORICAL),
                     "counts": {"programs": len(seen), "curated": len(inputs["curated"]),
                                "merged_programs": merged, "resources": len(records)},
                     "local_download_date": None, "current_operation_verified": False,
                     "transformations": ["UTF8_SIG", "ID_AND_ATTRIBUTES_CHECKED", "STRICT_DATE_PARSING", "TEXT_TAGS_WITH_EVIDENCE"],
                     "caveats": CAUTIONS},
            "resources": sorted(records.values(), key=lambda x: x["id"])}


def write_catalog():
    document = build_catalog()
    payload = canonical_bytes(document)
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    if not CATALOG.is_file() or CATALOG.read_bytes() != payload:
        temporary = CATALOG.with_suffix(".json.tmp")
        try:
            temporary.write_bytes(payload)
            temporary.replace(CATALOG)
        finally:
            if temporary.exists():
                temporary.unlink()
    return document


def load_catalog():
    expected = build_catalog()
    require(CATALOG.is_file(), "복지 공용 JSON 없음. welfare_resources.py build 실행 필요")
    actual = read_json_strict(CATALOG)
    require(canonical_bytes(actual) == canonical_bytes(expected), "복지 공용 JSON이 현재 입력·코드와 다름. 재생성 필요")
    return actual


def period_status(value, mode, as_of):
    if value["issues"]:
        return "UNKNOWN"
    start, end = value["start"], value["end"]
    left, right = HISTORICAL if mode == "historical" else (as_of, as_of)
    if end and end < left:
        return "ENDED_BEFORE_PERIOD"
    if start and start > right:
        return "STARTS_AFTER_PERIOD"
    if start and (end or value["open_end"]):
        return "OVERLAPS_RECORDED_WINDOW" if mode == "historical" else "IN_RECORDED_WINDOW"
    return "UNKNOWN"


def is_seoul(region):
    # 지원 범위는 현재 확인한 강남구/서울시만. 다른 시에 관계망·서울 자격을 이식하지 않는다.
    return region in ("강남구", "서울", "서울시", "서울특별시") or region.startswith(("서울시 ", "서울특별시 "))


def assess_resource(resource, *, mode, as_of=None, region="강남구", profile=None, axes=None):
    require(mode in ("historical", "current"), "mode는 historical/current")
    require(mode != "current" or bool(iso_date(as_of)), "현재 조회는 명시적 as_of YYYY-MM-DD 필수")
    profile = profile or {}
    e = resource["eligibility"]
    excluded, checks = [], []
    selected_axes = AXIS_TAGS if axes is None else axes
    matched = sorted(set(resource["support_tags"]) & set().union(*(AXIS_TAGS[a] for a in selected_axes)))
    operating = period_status(resource["operating_period"], mode, as_of)
    applying = period_status(resource["application_period"], "current", as_of) if mode == "current" else "NOT_EVALUATED_HISTORICAL"
    if mode == "current" and operating == "STARTS_AFTER_PERIOD" and applying == "IN_RECORDED_WINDOW":
        checks.append("FUTURE_PROGRAM_WITH_RECORDED_APPLICATION_WINDOW")
    elif operating in ("ENDED_BEFORE_PERIOD", "STARTS_AFTER_PERIOD"):
        excluded.append(operating)
    elif operating == "UNKNOWN":
        checks.append("OPERATING_DATES_UNKNOWN")
    if resource["resource_type"] == "PLAN":
        excluded.append("PLAN_NOT_OPERATING_RESOURCE")
    if resource["resource_type"] == "ADMINISTRATIVE":
        excluded.append("NOT_A_RESIDENT_APPLICATION_SERVICE")
    if resource.get("curation", {}).get("excluded_from_adult_linkage_by_curator"):
        excluded.append("CURATED_EXCLUSION_ADULT_LINKAGE")
    if e["gangnam_excluded_as_reported"] and region == "강남구":
        excluded.append("GANGNAM_EXCLUDED_AS_REPORTED_RECHECK_CURRENT")
    scope = e["resident_scope"]
    # Only explicit resident requirements count; provider location is not eligibility.
    districts = ('종로구','중구','용산구','성동구','광진구','동대문구','중랑구','성북구','강북구','도봉구','노원구',
                 '은평구','서대문구','마포구','양천구','강서구','구로구','금천구','영등포구','동작구','관악구','서초구','강남구','송파구','강동구')
    explicit = [d for d in districts if re.search(re.escape(d)+r'\s*(?:에\s*)?(?:거주|주민|구민)',e['target_text'])]
    if explicit and region not in e['target_text'] and region not in explicit:
        excluded.append('RESIDENT_REGION_MISMATCH')
    if scope == "GANGNAM_RESIDENT" and region != "강남구":
        excluded.append("RESIDENT_REGION_MISMATCH")
    elif scope.startswith("SEOUL") and not is_seoul(region):
        excluded.append("RESIDENT_REGION_MISMATCH")
    elif scope.endswith("REVIEW") or scope == "UNKNOWN":
        checks.append("RESIDENT_ELIGIBILITY_SCOPE_UNVERIFIED")
    if e["age_ranges"]:
        age = profile.get("age")
        if age is None:
            checks.append("AGE_REQUIRED")
        elif not any(low <= age <= high for low, high in e["age_ranges"]):
            excluded.append("AGE_MISMATCH")
    elif not e["age_unrestricted"]:
        checks.append("AGE_CONDITIONS_UNVERIFIED")
    allowed = set(re.findall(r"여성|남성", e["gender_text"]))
    if len(allowed) == 1:
        if profile.get("gender") is None:
            checks.append("GENDER_REQUIRED")
        elif profile["gender"] not in allowed:
            excluded.append("GENDER_MISMATCH")
    elif not allowed:
        checks.append("GENDER_CONDITIONS_UNVERIFIED")
    if e["single_household_required"]:
        if profile.get("single_household") is False:
            excluded.append("HOUSEHOLD_MISMATCH")
        elif profile.get("single_household") is not True:
            checks.append("SINGLE_HOUSEHOLD_REQUIRED")
    if e["other_conditions_review_required"]:
        checks.append("FULL_ELIGIBILITY_TEXT_REVIEW_REQUIRED")
    if axes is not None and not matched:
        excluded.append("NO_SELECTED_SUPPORT_TAG")
    if resource["resource_type"] in ("PILOT", "UNKNOWN"):
        checks.append("PILOT_OR_DETAILS_UNVERIFIED")
    if mode == "current":
        # 조회 시각·게시 날짜·일정만으로 '운영중/접수중'을 만들지 않는다.
        checks.extend(["CURRENT_OPERATION_UNVERIFIED", "RECRUITMENT_UNVERIFIED", "REMAINING_CAPACITY_UNVERIFIED"])
        if applying != "IN_RECORDED_WINDOW":
            checks.append("APPLICATION_" + applying)
    else:
        checks.append("ACTUAL_HISTORICAL_PARTICIPATION_AND_EFFECT_UNKNOWN")
    checks.extend(resource["verification"]["missing_fields"])
    mandatory = [x for x in checks if x != "ACTUAL_HISTORICAL_PARTICIPATION_AND_EFFECT_UNKNOWN"]
    bucket = "excluded" if excluded else "needs_confirmation" if mandatory else "candidate"
    return {"bucket": bucket, "bucket_label": BUCKETS[bucket], "matched_support_tags": matched,
            "operating_date_status": operating, "application_date_status": applying,
            "operation_status": "UNVERIFIED", "recruitment_status": "UNVERIFIED", "currently_available_confirmed": False,
            "exclusion_reasons": sorted(set(excluded)), "checks_required": sorted(set(checks)),
            "date_status_is_operation_confirmation": False}


def search_resources(*, mode, as_of=None, region="강남구", profile=None, axes=None, query=None,
                     resource_types=None, curated_only=False, catalog=None, limit=20, offset=0):
    require(mode in ("historical", "current"), "잘못된 조회 모드")
    require(mode != "current" or bool(iso_date(as_of)), "현재 조회는 명시적 as_of 필요")
    require(isinstance(limit, int) and 1 <= limit <= 100 and isinstance(offset, int) and offset >= 0, "잘못된 페이지 범위")
    if axes is not None:
        require(set(axes) <= {"R", "O"}, "지원 검색 축은 R/O만 허용")
    profile = profile or {}
    if "age" in profile:
        require(type(profile["age"]) is int and 0 <= profile["age"] <= 150, "age는 0~150 정수")
    if "single_household" in profile:
        require(type(profile["single_household"]) is bool, "single_household는 boolean")
    if "gender" in profile:
        require(profile["gender"] in ("여성", "남성"), "gender는 여성/남성 또는 미입력")
    document = catalog if catalog is not None else load_catalog()
    groups = {key: [] for key in BUCKETS}
    for r in document["resources"]:
        if curated_only and not r["curated"]:
            continue
        if resource_types and r["resource_type"] not in resource_types:
            continue
        if query and clean(query).casefold() not in (r["title"] + " " + r["content"] + " " + r["provider"]).casefold():
            continue
        assessment = assess_resource(r, mode=mode, as_of=as_of, region=region, profile=profile, axes=axes)
        if "NO_SELECTED_SUPPORT_TAG" in assessment["exclusion_reasons"]:
            continue
        # 원문은 데이터이며 UI/LLM 지시가 아니다. 상세 입력은 공용 catalog에서 추적 가능.
        item = {k: deepcopy(v) for k, v in r.items() if k != "provenance"}
        item["provenance"] = [{k: v for k, v in p.items() if k != "raw"} for p in r["provenance"]]
        item["assessment"] = assessment
        groups[assessment["bucket"]].append(item)
    for values in groups.values():
        values.sort(key=lambda r: (not r["curated"], r["title"], r["id"]))
    return {"schema_version": "welfare-search-result.v1", "mode": mode,
            "as_of": as_of if mode == "current" else None,
            "historical_period": list(HISTORICAL) if mode == "historical" else None,
            "region": region, "axes": sorted(axes) if axes is not None else None,
            "counts": {key: len(values) for key, values in groups.items()},
            "results": {key: values[offset:offset + limit] for key, values in groups.items()},
            "pagination": {"offset": offset, "limit_per_bucket": limit, "order": "CURATED_FIRST_THEN_TITLE_ID_NOT_RISK"},
            "data_status": {"source": "LOCAL_CATALOG", "freshness": "UNVERIFIED", "api": "NOT_REQUESTED"},
            "caveats": list(CAUTIONS)}


def search_for_dong(adm_cd8, *, mode, as_of=None, document=None, catalog=None, **search_options):
    if document is None:
        from build_classified_agent_input import load_classified
        document = load_classified()
    matches = [d for d in document["dongs"] if d["region"]["adm_cd8"] == adm_cd8]
    require(len(matches) == 1, "확인된 강남구 분류 동 코드가 아님")
    dong = matches[0]
    require(dong["region"]["sigungu_nm"] == "강남구", "강남구 분류를 다른 시에 적용할 수 없음")
    c = dong["classification"]
    axes = [] if c["status"] == "HOLD" else [axis for axis in ("R", "O") if c["signal_composition"][axis] == "ON"]
    require("axes" not in search_options and "region" not in search_options, "동별 조회 축/지역 덮어쓰기 불가")
    result = search_resources(mode=mode, as_of=as_of, region="강남구", axes=axes, catalog=catalog, **search_options)
    result["roa_context"] = {"region": deepcopy(dong["region"]), "status": c["status"], "type": c["type"],
                             "signal_composition": deepcopy(c["signal_composition"]),
                             "signals": deepcopy(c["signals"]), "additional_questions": deepcopy(c["additional_questions"]),
                             "support_review_directions": deepcopy(c["support_review_directions"]), "caveats": deepcopy(c["caveats"])}
    result["support_search_status"] = "HOLD_NO_RO_INFERENCE" if c["status"] == "HOLD" else "NO_RO_SUPPORT_INFERENCE" if not axes else "RO_CANDIDATE_REVIEW"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "check", "search"))
    parser.add_argument("--mode", choices=("historical", "current"))
    parser.add_argument("--as-of")
    parser.add_argument("--dong")
    parser.add_argument("--query")
    parser.add_argument("--curated-only", action="store_true")
    args = parser.parse_args()
    if args.command in ("build", "check"):
        document = write_catalog() if args.command == "build" else load_catalog()
        print(json.dumps({"status": "passed", "counts": document["meta"]["counts"], "path": str(CATALOG)}, ensure_ascii=False))
    else:
        kwargs = dict(mode=args.mode, as_of=args.as_of, query=args.query, curated_only=args.curated_only)
        result = search_for_dong(args.dong, **kwargs) if args.dong else search_resources(**kwargs)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
