"""실제 데이터 도구를 호출하는 담당자용 Agent. 기본 로컬 정형, Upstage 공급자 설정은 별도 모듈."""
from copy import deepcopy
import json
import os
import re
import threading
import urllib.request
import urllib.error

from roa_data import require
from welfare_resources import iso_date


from llm_provider import ProviderFailure, create_provider


class GeminiProvider:
    """STEP 4 회귀 호환용 모의 어댑터. 실제 Gemini 네트워크 호출은 금지한다."""
    def __init__(self, transport=None):
        self.transport=transport
    def status(self):
        return {"provider":"legacy_gemini_mock", "ready":bool(self.transport and os.environ.get("ROA_ENABLE_LLM")=="1"),
                "message":"모의 전용", "live_tested":False}
    def generate(self, contents, declarations):
        if not self.status()["ready"]:
            raise ProviderFailure("LEGACY_GEMINI_NETWORK_DISABLED")
        try:
            return self.transport({"contents":contents,"tools":[{"functionDeclarations":declarations}]})["candidates"][0]["content"]
        except (OSError,ValueError,KeyError,TypeError,IndexError):
            raise ProviderFailure("MOCK_PROVIDER_FAILURE") from None


def number(value):
    return "미확인" if value is None else format(value, ".6f").rstrip("0").rstrip(".")


def evidence_registry(dong):
    c = dong["classification"]
    facts = {"classification": f"{dong['region']['adm_nm']}: {c['type'] or 'HOLD'} · {c['display_name']}. R {c['signal_composition']['R']}, O {c['signal_composition']['O']}, A {c['signal_composition']['A']}."}
    for axis in ("R", "O"):
        for row in c["signals"][axis]["monthly_evidence"]:
            key = axis + "." + row["reference_month"]
            if row["status"] != "CALCULATED":
                facts[key] = f"{axis} {row['reference_month']}: 판정 불가/보류. 원시 반복 0을 OFF 또는 실제 0으로 해석하지 않습니다."
            else:
                facts[key] = f"{axis} {row['reference_month']}: 표준화 {number(row['standardized_pct'])}%, 21동 중앙값 {number(row['median_21_pct'])}%, 초과 {'예' if row['above_median'] else '아니오'}."
        facts[axis + ".period"] = (f"{axis}: 품질/결측으로 전체기간 판정 불가. 초과 개월 수를 해석하지 않습니다." if c["signals"][axis]["state"] == "UNDETERMINED" else
                                    f"{axis}: 세 기준월 중 중앙값 초과 {len(c['signals'][axis]['above_months'])}/3개월, 전체기간 {c['signals'][axis]['state']}. OFF는 세 월 전체 상대기준 미충족입니다.")
    for key, row in c["signals"]["A"]["comparisons"].items():
        label = "9~11월 창 대 7~9월 창" if key == "window_comparison" else "11월 대 7월"
        facts["A." + key] = f"A {label}: 기준 수준 {number(row['base_level'])}, 비교 수준 {number(row['target_level'])}, 변화율 {number(row['change_pct'])}%, 감소조건 {'충족' if row['decreased'] is True else '미충족' if row['decreased'] is False else '미확인'}."
    facts["A.meaning"] = "A ON은 두 감소조건 충족이며 매월 연속 감소를 뜻하지 않습니다. A OFF는 두 조건 전체 미충족이며 활동 유지 판정이 아닙니다."
    if c["status"] == "HOLD":
        facts["hold"] = "판정 보류 사유: " + "; ".join(str(r.get("detail", r.get("code", "자료 확인 필요"))) for r in c["hold_reasons"])
    return facts


class RoaAgent:
    def __init__(self, service, provider=None):
        self.service = service
        self.provider = provider or create_provider()
        self._finish_local = threading.local()
        self.llm_lock = threading.BoundedSemaphore(1)

    def ask(self, payload):
        require(isinstance(payload, dict) and set(payload) <= {"dong", "question", "mode", "as_of", "engine", "query", "curated_only"}, "잘못된 Agent 요청 인자")
        code = payload.get("dong")
        d = self.service.detail(code)
        question = payload.get("question")
        require(isinstance(question, str) and 1 <= len(question.strip()) <= 1000 and not any(ord(c) < 32 and c not in "\n\t" for c in question), "질문은 1~1000자")
        mode = payload.get("mode")
        require(mode in ("historical", "current"), "조회 모드를 선택하세요.")
        as_of = payload.get("as_of")
        require(mode != "current" or bool(iso_date(as_of)), "현재 조회 기준일이 필요합니다.")
        engine = payload.get("engine", "structured")
        require(engine in ("structured", "llm"), "잘못된 Agent 모드")
        for other in self.service.dongs.values():
            name = other["region"]["adm_nm"]
            require(name not in question or other["region"]["adm_cd8"] == code, "질문 지역과 선택 동이 다릅니다. 지도에서 질문할 동을 선택하세요.")
        require("춘천" not in question, "춘천에는 강남 R/O 분류를 적용하지 않습니다. SK 상세 탐색을 사용하세요.")
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", question)
        require(not dates or mode == "current" and all(x == as_of for x in dates), "질문 날짜와 선택한 조회기간이 다릅니다. 기간/기준일을 변경하세요.")
        years = re.findall(r"(20\d{2})\s*년", question)
        require(all(y in ({"2025", as_of[:4]} if mode == "current" else {"2025"}) for y in years), "선택한 점검/지원 기간에서 확인할 수 없는 연도입니다.")
        query = payload.get("query")
        if query is None:
            quoted = re.search(r'["“「]([^"”」]{1,100})["”」]', question)
            query = quoted[1] if quoted else None
        require(query is None or isinstance(query, str) and len(query) <= 100 and not any(ord(c) < 32 for c in query), "잘못된 자원 검색어")
        curated_only = payload.get('curated_only', False)
        require(type(curated_only) is bool, '잘못된 선별본 조회 조건')
        c = d["classification"]
        facts = evidence_registry(d)
        questions = {x["id"]: x["text"] for x in c["additional_questions"]}
        trace, tool_count = [], 0
        result = None
        def tool(name, args):
            nonlocal tool_count, result
            tool_count += 1
            require(tool_count <= 12 and isinstance(args, dict), "도구 호출 한도/형식 오류")
            if name in ("get_region_classification", "get_region_evidence", "get_additional_questions"):
                require(not args, "지역은 서버 선택 맥락에 고정됩니다.")
                data = ({"region": d["region"], "classification": c} if name == "get_region_classification" else
                        {"facts": facts} if name == "get_region_evidence" else {"questions": questions})
            elif name == "search_welfare_resources":
                require(set(args) <= {"query"}, "허용되지 않은 자원 검색 인자")
                value = args.get("query", query)
                require(value is None or isinstance(value, str) and len(value) <= 100 and not any(ord(x) < 32 for x in value), "검색어 오류")
                require(value == query or query is None and value == '', '사용자 검색 조건을 변경할 수 없습니다.')
                result = self.service.welfare(code, mode=mode, as_of=as_of, query=query, limit=5, curated_only=curated_only)
                data = {"counts": result["counts"], "data_status": result["data_status"],
                        "resources": [{"id": r["id"], "title": r["title"], "assessment": r["assessment"], "source_urls": r["source_urls"]}
                                      for bucket in ("candidate", "needs_confirmation") for r in result["results"][bucket]][:8]}
            elif name == "get_resource_details":
                require(set(args) == {"id"} and isinstance(args["id"], str), "잘못된 자원 상세 인자")
                require(result is not None and any(r["id"] == args["id"] for rows in result["results"].values() for r in rows), "현재 동/기간 검색 결과에 없는 자원")
                r = next(r for rows in result['results'].values() for r in rows if r['id']==args['id'])
                data = {k: r[k] for k in ("id", "title", "eligibility", "operating_period", "application_period", "source_urls", "verification")}
            else:
                raise ValueError("허용되지 않은 도구")
            trace.append({"name": name, "arguments": deepcopy(args), "status": "success"})
            return data
        # 언제나 실제 도구로 숫자/질문을 읽는다. 질문 의미의 범위는 아래 intent에 표시한다.
        tool("get_region_evidence", {})
        tool("get_additional_questions", {})
        tool("search_welfare_resources", {"query": query} if query else {})
        base_result = deepcopy(result)
        policy = {"EXISTING": "기존 사업 활용 검토: 조회된 자원의 대상·운영·접수·주민 의사를 확인한 뒤 안내 경로를 검토합니다.",
                  "EXPAND": "확대 검토 후보: 실제 이용수요·대기·기관 정원·담당자 의견을 추가 조사한 뒤 기존 사업의 접근 방식·시간 조정 가능성을 검토합니다.",
                  "NEW": "신규 아이디어(운영 사업 아님): 주민 수요와 기존 사업의 대응 범위를 조사한 뒤 관계 형성/접근성 보완 시범안을 별도로 검토합니다."}
        if c["status"] == "HOLD" or not base_result["axes"]:
            policy = {"DATA_REVIEW": "추가 조사 제안(신규 운영 사업 아님): 데이터 품질과 계절·시설·상권·방문 수요 등 지역 활동 맥락을 확인합니다. R/O 기반 개인 지원은 자동 추론하지 않습니다."}
        else:
            actual = [r["title"] for bucket in ("candidate", "needs_confirmation") for r in base_result["results"][bucket]][:2]
            focus = " / ".join(x["text"] for x in c["support_review_directions"] if x["axis"] in base_result["axes"])
            policy["EXISTING"] = ("기존 사업 활용 검토: " + (", ".join(actual) if actual else "현재 검색 조건에서 확인된 사업 없음")
                                  + ". 자격·운영·접수와 주민 의사를 확인한 뒤 안내 여부를 검토합니다.")
            policy["EXPAND"] += " 동별 검토 방향: " + focus
            ideas = (["주민이 원하는 안부·교류 방식 조사"] if "R" in base_result["axes"] else []) + (["비대면·이용 시간·방문/동행 접근 수요 조사"] if "O" in base_result["axes"] else [])
            policy["NEW"] = "신규 아이디어(현재 운영 사업 아님): " + " / ".join(ideas) + ". 기존 사업 대응 범위·수요·정원 확인 이후에 시범안을 검토합니다."
        selected_facts, selected_questions, selected_policy = list(facts), list(questions), list(policy)
        agent_mode, failure = "STRUCTURED_DATA_TOOLS", None
        if engine == "llm":
            self._finish_local.value = None
            acquired = self.llm_lock.acquire(blocking=False)
            try:
                if not acquired:raise ProviderFailure('LLM_BUSY')
                selected_facts, selected_questions, selected_policy = self.run_llm(question, d["region"], mode, as_of, facts, questions, policy, tool,
                    search_context={'query': query, 'curated_only': curated_only})
                agent_mode = "LLM_TOOL_SELECTION_GROUNDED_RENDERING"
                if self._finish_local.value:
                    trace.append(self._finish_local.value)
            except (ProviderFailure, ValueError, KeyError, TypeError) as error:
                # 생 텍스트/허구 수치를 표시하지 않고 동일 동의 실제 도구 결과로 대체한다.
                safe_reasons = {"근거·자원 도구 조회를 먼저 해야 합니다.": "MISSING_REQUIRED_TOOL_EVIDENCE", "근거 없는 LLM 출력": "UNSUPPORTED_EVIDENCE_ID",
                                "LLM 결과 스키마 오류": "INVALID_FINAL_SCHEMA", "빈 LLM 검토 결과": "EMPTY_FINAL_REVIEW",
                                "허용되지 않은 LLM 도구": "DISALLOWED_TOOL", "LLM 도구 한도 초과": "TOOL_BUDGET_EXCEEDED"}
                failure = safe_reasons.get(str(error), str(error) if isinstance(error, ProviderFailure) and re.fullmatch(r"[A-Z_]+", str(error)) else "LLM_UNAVAILABLE_OR_INVALID_TOOL_OUTPUT")
                agent_mode = "STRUCTURED_FALLBACK"
                result = base_result
            finally:
                if acquired:self.llm_lock.release()
        # All policy names come from the final validated lookup, including zero
        # results or the restored fallback result.
        if 'EXISTING' in policy:
            actual = [r['title'] for bucket in ('candidate', 'needs_confirmation') for r in result['results'][bucket]][:2]
            policy['EXISTING'] = ('기존 사업 활용 검토: ' + (', '.join(actual) if actual else '현재 검색 조건에서 확인된 사업 없음')
                                  + '. 자격·운영·접수와 주민 의사를 확인한 뒤 안내 여부를 검토합니다.')
        intent = "evidence" if any(x in question for x in ("왜", "유형", "근거", "비교", "OFF")) else "resources" if any(x in question for x in ("자원", "프로그램", "찾아", "사업", "신청")) else "review"
        resource_lines = []
        for bucket in ("candidate", "needs_confirmation"):
            for r in result["results"][bucket][:3]:
                resource_lines.append(f"{r['title']} [{r['assessment']['bucket_label']}] · 대상 {r['eligibility']['target_text'] or r['eligibility']['age_text'] or '미확인'} · 운영 {r['operating_period']['start'] or '미확인'}~{r['operating_period']['end'] or '종료일 미확인'} · 신청 {r['application_period']['start'] or '미확인'}~{r['application_period']['end'] or '미확인'} · 현재 이용 가능 확정 아님.")
                if r.get('source_review'):
                    resource_lines.append('공식 자료 대조 ('+r['source_review']['checked_on']+'): '+r['source_review']['note'])
        if not resource_lines:
            resource_lines = ["이 조건에서 직접 연결할 확인 가능한 자원을 찾지 못했습니다. 서비스 부재·복지 공백을 뜻하지 않습니다."]
        gaps = [questions[k] for k in selected_questions] + ["운영·모집·자격·멤버십·정원·대기·주민 수요를 확인해야 합니다. 현재 자료만으로 부족/공백과 정책 효과를 확정할 수 없습니다."]
        sections = [
            {"title": "지역 점검 결과", "lines": [facts["classification"]]},
            {"title": "데이터 근거", "lines": [facts[k] for k in selected_facts if k != "classification"]},
            {"title": "해석", "lines": [c["signals"]["A"]["display_label"], "R/O는 거주 1인가구 모형 추정, A는 방문자를 포함한 지역 활동입니다. 지역 활동을 주민 개인의 외출로 해석하지 않습니다.",
                                           "관계망은 세 기준월 전체 기준이며 일부 월만 초과해도 전체 신호는 OFF입니다. 직전 3개월 산정은 미확인 가정입니다."]},
            {"title": "관련 복지자원", "lines": resource_lines},
            {"title": "확인 필요한 공백", "lines": gaps},
            {"title": "정책 검토안", "lines": [policy[k] for k in selected_policy]},
            {"title": "주의사항", "lines": list(c["caveats"]) + ["담당자 확인과 주민 의사·수요에 따른 최종 판단이 필요합니다."]},
        ]
        return {"region": d["region"], "type": c["type"], "status": c["status"], "mode": mode, "as_of": as_of if mode == "current" else None,
                "agent_mode": agent_mode, "llm_failure": failure, "intent": intent, "question": question,
                "sections": sections, "evidence": facts, "resources": result, "tool_trace": trace,
                "provider_status": self.provider.status(), "response_contract": "수치·사업명·출처는 코드 측 검증값만 표시"}

    def run_llm(self, question, region, mode, as_of, facts, questions, policy, tool, search_context=None):
        names = ("get_region_classification", "get_region_evidence", "get_additional_questions", "search_welfare_resources", "get_resource_details", "finish_review")
        declarations = []
        for name in names:
            props = {"query": {"type": "string"}} if name == "search_welfare_resources" else {"id": {"type": "string"}} if name == "get_resource_details" else {
                k: {"type": "array", "items": {"type": "string"}} for k in ("evidence_ids", "question_ids", "policy_codes")} if name == "finish_review" else {}
            declarations.append({"name": name, "description": "선택 동 고정. " + name,
                                 "parameters": {"type": "object", "properties": props, "additionalProperties": False, "required": list(props) if name in ("finish_review", "get_resource_details") else []}})
        finish = declarations[-1]["parameters"]["properties"]
        if search_context is not None:
            declarations[3]['parameters']['properties']['query']['enum'] = [search_context['query'] or '']
            declarations[3]['description'] = '선택 동·시점·선별본·검색 조건 고정. query 미지정 또는 빈 문자열은 전체 조회. 동 이름이나 여러 키워드를 새로 만들어 검색하지 마세요.'
        for key, allowed in (("evidence_ids", facts), ("question_ids", questions), ("policy_codes", policy)):
            finish[key]["items"]["enum"] = list(allowed)
        contents = [{"role": "user", "parts": [{"text": json.dumps({"question": question, "selected_region": region, "mode": mode, "as_of": as_of,
                       "search_context": search_context, "available_evidence_ids": list(facts), "available_question_ids": list(questions), "policy_options": policy}, ensure_ascii=False)}]}]
        calls = 0
        observed = set()
        rounds = self.provider.status().get("max_provider_calls_per_question", 3)
        for step in range(rounds):
            required_missing = {"get_region_evidence", "search_welfare_resources"} - observed
            # 실제 조회를 하기 전 finish를 노출하지 않는다. 잘못된 조기 완료를 모델이 선택할 수 없게 한다.
            available = [d for d in declarations if d["name"] in required_missing] if required_missing else declarations
            if not required_missing and (step == rounds-1 or calls >= self.provider.status().get('max_tool_calls_per_question',6)-1):
                available = [d for d in declarations if d["name"] == "finish_review"]
            content = self.provider.generate(contents, available)
            contents.append(content)  # thoughtSignature 등 공급자 원본 part를 보존한다.
            responses = []
            batch = [part['functionCall'] for part in content['parts'] if 'functionCall' in part]
            require(all(fc.get('name') in names and isinstance(fc.get('args', {}), dict) for fc in batch), '허용되지 않은 LLM 도구')
            require(not any(fc['name'] == 'finish_review' for fc in batch) or len(batch) == 1, 'LLM 결과 스키마 오류')
            for part in content["parts"]:
                fc = part.get("functionCall")
                if not fc:
                    continue
                calls += 1
                require(calls <= self.provider.status().get("max_tool_calls_per_question", 6), "LLM 도구 한도 초과")
                name, args = fc["name"], fc.get("args", {})
                require(name in names and isinstance(args, dict), "허용되지 않은 LLM 도구")
                if name == "finish_review":
                    require("get_region_evidence" in observed and "search_welfare_resources" in observed, "근거·자원 도구 조회를 먼저 해야 합니다.")
                    require(set(args) == {"evidence_ids", "question_ids", "policy_codes"}, "LLM 결과 스키마 오류")
                    for key, allowed in (("evidence_ids", facts), ("question_ids", questions), ("policy_codes", policy)):
                        require(isinstance(args[key], list) and len(args[key]) <= 20 and all(isinstance(v, str) and v in allowed for v in args[key]), "근거 없는 LLM 출력")
                    require(args["evidence_ids"] and args["policy_codes"], "빈 LLM 검토 결과")
                    tool_trace_finish = {"name": "finish_review", "arguments": {"evidence_ids": args["evidence_ids"], "question_ids": args["question_ids"], "policy_codes": args["policy_codes"]}, "status": "validated"}
                    # finish는 데이터 조회/쓰기 도구가 아니며 실제 최종 선택을 기록한다.
                    self._finish_local.value = tool_trace_finish
                    return args["evidence_ids"], args["question_ids"], args["policy_codes"]
                output = tool(name, args)
                observed.add(name)
                function_response = {"name": name, "response": output}
                if "id" in fc:
                    function_response["id"] = fc["id"]
                responses.append({"functionResponse": function_response})
            require(responses, "LLM 도구 호출이 없음")
            contents.append({"role": "user", "parts": responses})
        raise ProviderFailure("LLM_ROUND_LIMIT")
