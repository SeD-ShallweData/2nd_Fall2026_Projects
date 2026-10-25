"""STEP 4 웹 연결 계층. 분류·적합성은 STEP 2/3 모듈에 위임한다."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import re

from build_classified_agent_input import load_classified
from welfare_resources import load_catalog, iso_date, WORK
from welfare_api import query_welfare
from roa_data import require, read_json_strict, ROOT

PALETTE = {"NONE": "#778396", "R": "#7461ad", "O": "#258a88", "RO": "#b47b37", "HOLD": "#687280"}


class DashboardService:
    def __init__(self, geometry):
        self.document = load_classified()
        self.resources = load_catalog()
        self.dongs = {d["region"]["adm_cd8"]: d for d in self.document["dongs"]}
        self.geometry = [deepcopy(f) for f in geometry if f["properties"]["CITY_CODE"] == "11230"]
        require({f["properties"]["DONG_CODE"] for f in self.geometry} == set(self.dongs), "ROA/지도 동 코드 불일치")
        paths = list(WORK.glob("*roa*.py")) + list(WORK.glob("*agent_input.py"))
        paths += [WORK / n for n in ("build_classified_agent_input.py", "welfare_resources.py", "welfare_api.py", "roa_contract.json", "welfare_contract.json")]
        paths += [WORK / n for n in ("llm_config.py", "llm_provider.py", "roa_agent.py", "seoul_config.py", "request_limits.py")]
        paths += list((WORK / "outputs/canonical/agent").glob("*.json"))
        paths += [WORK / "outputs/canonical/welfare/welfare_catalog.v1.json", WORK / 'outputs/validation/step1b/source_checks.json']
        for name in ("roa_contract.json", "welfare_contract.json"):
            contract = read_json_strict(WORK / name)
            source = contract.get("sources", contract.get("inputs", {}))
            for item in source.values():
                if isinstance(item, dict) and "path" in item:
                    paths.append(ROOT / item["path"])
        self.files = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in set(paths) if p.is_file()}

    def check(self):
        require(all(p.is_file() and (p.stat().st_size, p.stat().st_mtime_ns) == state for p, state in self.files.items()),
                "검증된 데이터/코드가 변경됐습니다. 서버를 재시작해 검증하세요.")

    def dong(self, code):
        require(isinstance(code, str) and code in self.dongs, "확인된 강남구 8자리 동 코드가 아닙니다.")
        return self.dongs[code]

    def catalog(self):
        self.check()
        summary = self.document["meta"]["classification_summary"]
        legend = []
        for item in self.document["meta"]["classification_catalog"]:
            code = item["type"]
            legend.append({"type": code, "display_name": item["display_name"], "count": summary["types"][code]["count"],
                           "color": PALETTE[code.split("_")[0]], "a_on": code.endswith("A1")})
        legend.append({"type": "HOLD", "display_name": "판정 보류", "count": summary["hold_count"], "color": PALETTE["HOLD"], "a_on": None})
        return {"period": "2025년 10~12월 기준월 종합 ROA 점검", "rule_version": self.dongs[next(iter(self.dongs))]["classification"]["rule_version"],
                "classified_count": summary["classified_count"], "hold_count": summary["hold_count"], "legend": legend,
                "dongs": [{**d["region"], "type": d["classification"]["type"], "status": d["classification"]["status"]} for d in self.dongs.values()],
                "as_of_default": date.today().isoformat(), "resource_count": len(self.resources["resources"]),
                "period_alignment": [{"reference_month": m["reference_month"], "periods": m["periods"]} for m in next(iter(self.dongs.values()))["monthly_evidence"]]}

    def map_data(self):
        self.check()
        features = deepcopy(self.geometry)
        for f in features:
            c = self.dong(f["properties"]["DONG_CODE"])["classification"]
            f["properties"].update(type=c["type"], status=c["status"], display_name=c["display_name"],
                                     color=PALETTE[c["relationship_pattern"] or "HOLD"], a_state=c["A_state"])
        return {"type": "FeatureCollection", "features": features}

    def detail(self, code):
        self.check()
        d = self.dong(code)
        return {key: deepcopy(d[key]) for key in ("region", "classification", "monthly_evidence", "sk_monthly_detail", "quality", "classification_eligibility")}

    def welfare(self, code, *, mode, as_of=None, query=None, limit=10, offset=0, curated_only=False, use_api_cache=None):
        self.check()
        self.dong(code)
        if use_api_cache is None:
            import os
            use_api_cache=os.environ.get('ROA_USE_WELFARE_API_CACHE')=='1'
        return query_welfare(adm_cd8=code, mode=mode, as_of=as_of, query=query, limit=limit, offset=offset,
                             curated_only=curated_only, use_api_cache=use_api_cache, catalog=self.resources, document=self.document)

    def resource(self, identifier, *, use_api_cache=False):
        self.check()
        require(type(use_api_cache) is bool,'잘못된 캐시 선택')
        catalog=self.resources
        if use_api_cache:
            from welfare_api import load_verified_api_cache,combine_catalogs
            try:
                api=load_verified_api_cache()
                catalog,_=combine_catalogs(self.resources,api)
            except (ValueError,OSError,TypeError,KeyError):pass
        matches = [r for r in catalog["resources"] if r["id"] == identifier]
        require(len(matches) == 1, "확인된 자원 ID가 아닙니다.")
        result = deepcopy(matches[0])
        result["provenance"] = [{k: v for k, v in p.items() if k != "raw"} for p in result["provenance"]]
        from welfare_api import attach_source_reviews
        return attach_source_reviews(result)

    def external_status(self):
        import os
        from welfare_api import BASE_URL, CACHE, load_verified_api_cache
        from seoul_config import load_seoul_config
        config=load_seoul_config()
        cache={'status':'MISSING','authenticated_refresh_verified':False}
        if CACHE.is_file():
            try:
                document=load_verified_api_cache()
                cache={'status':'VALIDATED_COMPLETE','authenticated_refresh_verified':True,
                       'retrieved_at':document['meta'].get('retrieved_at'),'resources':len(document['resources'])}
            except (ValueError,OSError,TypeError,KeyError):cache['status']='INVALID_LOCAL_FALLBACK'
        diagnostic={}
        path=WORK/'outputs/validation/step1b/seoul_network.json'
        if path.is_file():
            try:
                r=read_json_strict(path);diagnostic={k:r.get(k) for k in ('status','checked_at','authenticated_requests')}
            except (ValueError,OSError):diagnostic={'status':'DIAGNOSTIC_UNAVAILABLE'}
        sample_path = WORK / "outputs/validation/seoul_live_sample_validation.json"
        sample = {}
        if sample_path.is_file():
            try:
                checked = read_json_strict(sample_path)
                sample = {"checked_at": checked.get("checked_at"), "services": {
                    k: {"status": v.get("status"), "returned": v.get("returned"), "sample": v.get("sample")}
                    for k,v in checked.get("meta", {}).get("api_status", {}).items()}}
            except (ValueError,TypeError,KeyError):
                sample = {"status": "DIAGNOSTIC_UNAVAILABLE"}
        if cache['authenticated_refresh_verified']:
            diagnostic={'status':'AUTHENTICATED_COMPLETE','checked_at':cache.get('retrieved_at')}
        return {"welfare": {"default_source": "API_PLUS_LOCAL_IF_ENABLED" if cache['authenticated_refresh_verified'] else "LOCAL_629", "authenticated_key_present": bool(config.api_key),
                            "configuration_source":config.source,'configuration_error':config.error,
                            "authenticated_refresh_verified": cache['authenticated_refresh_verified'], "public_sample": sample,
                            'cache':cache,'connection_diagnostic':diagnostic,
                            "plain_http_credentials_blocked": True,
                            "freshness": "UNVERIFIED"}}

    def get(self, path, q):
        allowed = {"/api/roa/catalog": set(), "/api/roa/map": set(), "/api/roa/detail": {"dong"},
                   "/api/roa/resource": {"id", "use_api_cache"}, "/api/roa/welfare": {"dong", "mode", "as_of", "query", "limit", "offset", "curated_only", "use_api_cache"}}
        require(path in allowed and set(q) <= allowed[path], "지원하지 않는 조회 인자입니다.")
        if path == "/api/roa/catalog":
            return self.catalog()
        if path == "/api/roa/map":
            return self.map_data()
        if path == "/api/roa/detail":
            return self.detail(q.get("dong"))
        if path == "/api/roa/resource":
            require(isinstance(q.get("id"), str) and len(q["id"]) <= 100, "잘못된 자원 ID")
            require(q.get('use_api_cache','false') in ('true','false'),'잘못된 캐시 선택')
            return self.resource(q["id"],use_api_cache=q.get('use_api_cache')=='true')
        require(q.get("mode") in ("historical", "current"), "조회 모드가 필요합니다.")
        require(q.get("mode") != "current" or bool(iso_date(q.get("as_of"))), "현재 조회는 YYYY-MM-DD 기준일이 필요합니다.")
        require(len(q.get("query", "")) <= 100 and not any(ord(c) < 32 for c in q.get("query", "")), "검색어는 제어문자 없이 100자 이하")
        for key in ("curated_only", "use_api_cache"):
            require(q.get(key, "false") in ("true", "false"), "boolean 인자는 true/false")
        require(re.fullmatch(r"\d{1,3}", q.get("limit", "10")) and re.fullmatch(r"\d{1,6}", q.get("offset", "0")), "잘못된 페이지 범위")
        return self.welfare(q.get("dong"), mode=q["mode"], as_of=q.get("as_of"), query=q.get("query") or None,
                            limit=int(q.get("limit", 10)), offset=int(q.get("offset", 0)),
                            curated_only=q.get("curated_only") == "true", use_api_cache=(q['use_api_cache']=='true') if 'use_api_cache' in q else None)
