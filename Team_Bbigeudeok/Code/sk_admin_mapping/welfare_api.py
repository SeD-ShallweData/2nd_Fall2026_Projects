"""서울 공식 Open API 어댑터. 명시적 갱신/일반 검색 분리, 오류 시 검증된 로컬 검색."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache
import argparse
import json
import math
import os
import socket
import ssl
import hashlib
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from build_agent_input import canonical_bytes
from roa_data import read_json_strict, require
from welfare_resources import (WORK, PROGRAM_FIELDS, base_resource, finalize, lineage, normalize_program,
                               period, search_resources, search_for_dong, support_tags)

SERVICES = {"programs": ("OA-21309", "tbPartcptn", "PARTCPTN_ID", "PARTCPTN_SJ"),
            "list": ("OA-23013", "welfareServiceList", "SERV_ID", "SERV_NM"),
            "detail": ("OA-23014", "welfareServiceDe", "SERV_ID", "SERV_NM")}
BASE_URL = "http://openapi.seoul.go.kr:8088"
_REFRESH_LOCK = threading.Lock()
CACHE = WORK / "outputs/canonical/welfare/welfare_api_cache.v1.json"
SOURCE_REVIEWS = WORK / 'outputs/validation/step1b/source_checks.json'
MAX_BYTES = 16 * 1024 * 1024
REQUIRED_FIELDS = {
    "programs": {"PARTCPTN_ID", "PARTCPTN_SJ", "INSTT_NM", "PROGRS_DE1", "PROGRS_DE2", "RCEPT_DE1", "RCEPT_DE2", "TRGET_INFO", "CN"},
    "list": {"SERV_ID", "SERV_NM", "SERV_DGST", "SERV_DTL_LINK", "BIZ_CHR_DEPT_NM", "CTPV_NM", "SGG_NM", "LAST_MOD_YMD"},
    "detail": {"SERV_ID", "SERV_NM", "ENFC_BGNG_YMD", "ENFC_END_YMD", "SPRT_TRGT_CN", "SLCT_CRIT_CN", "ALW_SERV_CN", "APLY_MTD_CN", "LAST_MOD_YMD"},
}


class ApiFailure(ValueError):
    """오류에는 인증키·요청 URL·서버 원문 메시지를 포함하지 않는다."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ApiFailure("REDIRECT_REFUSED")


def default_transport(url, timeout):
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(url, timeout=timeout) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ApiFailure("RESPONSE_TOO_LARGE")
    return payload


def parse_response(payload, service):
    if not isinstance(payload, (bytes, str)) or len(payload) > MAX_BYTES:
        raise ApiFailure("INVALID_RESPONSE")
    try:
        text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload.lstrip("\ufeff")
        if text.lstrip().startswith("<"):
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise ApiFailure("XML_DECLARATION_REFUSED")
            root = ET.fromstring(text)
            node = root
            result = node.find("RESULT") if root.tag == service else root
            code = result.findtext("CODE") if result is not None else None
            if code == "INFO-200":
                return {"rows": [], "total": 0, "code": code}
            if code != "INFO-000":
                raise ApiFailure(code if code and code.replace("-", "").isalnum() and len(code) < 32 else "MALFORMED_RESPONSE")
            if root.tag != service:
                raise ApiFailure("SERVICE_SCHEMA_CHANGED")
            total = int(root.findtext("list_total_count"))
            rows = [{child.tag: child.text or "" for child in row} for row in root.findall("row")]
        else:
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise ApiFailure("DUPLICATE_JSON_KEY")
                    obj[key] = value
                return obj
            obj = json.loads(text, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ApiFailure("NONFINITE_JSON")))
            node = obj.get(service, obj)
            result = node.get("RESULT", {})
            code = result.get("CODE")
            if code == "INFO-200":
                return {"rows": [], "total": 0, "code": code}
            if code != "INFO-000":
                raise ApiFailure(code if isinstance(code, str) and code.replace("-", "").isalnum() and len(code) < 32 else "MALFORMED_RESPONSE")
            if service not in obj:
                raise ApiFailure("SERVICE_SCHEMA_CHANGED")
            if type(node.get('list_total_count')) is not int:
                raise ApiFailure('INVALID_TOTAL_COUNT')
            total = node["list_total_count"]
            rows = node["row"]
        if total < 0 or not isinstance(rows, list) or len(rows) > total or not all(isinstance(r, dict) for r in rows):
            raise ApiFailure("MALFORMED_RESPONSE")
        return {"rows": rows, "total": total, "code": "INFO-000"}
    except ApiFailure:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ET.ParseError, UnicodeError):
        raise ApiFailure("MALFORMED_RESPONSE") from None


class SeoulWelfareClient:
    """키는 프로세스 환경변수에서만 읽는다. 실제 키 요청은 서버 측에서만 실행한다."""
    def __init__(self, *, transport=None, page_size=500, max_calls=40, timeout=8, sample=False, base_url=None, allow_http_authenticated=False):
        require(type(page_size) is int and 1 <= page_size <= 1000, "page_size는 1~1000")
        require(type(max_calls) is int and 1 <= max_calls <= 100, "max_calls는 1~100")
        require(isinstance(timeout, (float, int)) and 0 < timeout <= 30, "timeout은 0~30초")
        self.transport = transport or default_transport
        self.page_size = page_size
        self.max_calls = max_calls
        self.timeout = timeout
        self.sample = sample
        self.calls = 0
        require(type(allow_http_authenticated) is bool,'HTTP opt-in must be boolean')
        self.allow_http_authenticated = allow_http_authenticated
        self.base_url = (base_url or os.environ.get("SEOUL_API_BASE_URL", BASE_URL)).rstrip("/")

    def fetch(self, source):
        if source not in SERVICES:
            raise ApiFailure("UNKNOWN_SOURCE")
        from seoul_config import load_seoul_config
        config=load_seoul_config()
        if not self.sample and config.error:raise ApiFailure(config.error)
        key = "sample" if self.sample else config.api_key
        if not key:
            raise ApiFailure("MISSING_API_KEY")
        if key == "sample" and not self.sample:
            raise ApiFailure("SAMPLE_KEY_REQUIRES_SAMPLE_MODE")
        target = urllib.parse.urlsplit(self.base_url)
        if target.hostname != "openapi.seoul.go.kr" or target.username or target.password or target.query or target.fragment or target.path or target.scheme not in ("http", "https") or target.port not in (None, 443, 8088):
            raise ApiFailure("API_ENDPOINT_NOT_ALLOWED")
        # Default is HTTPS-only; official HTTP:8088 requires per-client explicit opt-in.
        if not self.sample and target.scheme != "https" and self.transport is default_transport and not (self.allow_http_authenticated and target.port==8088):
            raise ApiFailure("INSECURE_CREDENTIAL_TRANSPORT_BLOCKED")
        dataset, service, id_field, title_field = SERVICES[source]
        rows, total, start, ids, pages = [], None, 1, set(), []
        width=self.page_size
        while total is None or len(rows) < total:
            if self.calls >= self.max_calls:
                raise ApiFailure("CALL_BUDGET_EXCEEDED_INCOMPLETE")
            end = 5 if self.sample else start + width - 1
            url = f"{self.base_url}/{urllib.parse.quote(key, safe='')}/json/{service}/{start}/{end}/"
            self.calls += 1
            try:
                data = parse_response(self.transport(url, self.timeout), service)
            except ApiFailure as error:
                if str(error)=='RESPONSE_TOO_LARGE' and not self.sample and width>1:
                    width=max(1,width//2)
                    continue
                raise
            except urllib.error.HTTPError as error:
                raise ApiFailure("API_HTTP_AUTH_REJECTED" if error.code in (401,403) else "API_HTTP_FAILURE") from None
            except urllib.error.URLError as error:
                cause=error.reason
                code="API_DNS_FAILURE" if isinstance(cause,socket.gaierror) else "API_TLS_FAILURE" if isinstance(cause,ssl.SSLError) else "API_TIMEOUT" if isinstance(cause,TimeoutError) else "API_NETWORK_ACCESS_DENIED" if isinstance(cause,PermissionError) or getattr(cause,'winerror',None)==10013 else "NETWORK_OR_HTTP_ERROR"
                raise ApiFailure(code) from None
            except TimeoutError:
                raise ApiFailure("API_TIMEOUT") from None
            except OSError:
                raise ApiFailure("NETWORK_OR_HTTP_ERROR") from None
            if total is not None and total != data["total"]:
                raise ApiFailure("DATA_CHANGED_DURING_PAGINATION")
            total = data["total"]
            pages.append({'start':start,'end':end,'returned':len(data['rows']),'reported_total':total})
            if not data["rows"] and len(rows) < total:
                raise ApiFailure("EMPTY_PAGE_INCOMPLETE")
            if len(data["rows"]) > end - start + 1:
                raise ApiFailure("PAGE_SIZE_SCHEMA_CHANGED")
            for row in data["rows"]:
                if not REQUIRED_FIELDS[source] <= set(row) or any(not isinstance(v, (str, int, float, type(None))) or isinstance(v, float) and not math.isfinite(v) for v in row.values()):
                    raise ApiFailure("REQUIRED_FIELD_SCHEMA_CHANGED")
                if not isinstance(row.get(id_field), str) or not row[id_field] or not isinstance(row.get(title_field), str) or not row[title_field]:
                    raise ApiFailure("REQUIRED_FIELD_SCHEMA_CHANGED")
                if row[id_field] in ids:
                    raise ApiFailure("DUPLICATE_SOURCE_ID")
                ids.add(row[id_field])
                rows.append(row)
            if len(rows) > total:
                raise ApiFailure("INVALID_TOTAL_COUNT")
            if self.sample:
                break
            start = end + 1
            width=self.page_size
        return {"source": source, "dataset": dataset, "service": service, "rows": rows, "total": total,
                "complete": len(rows) == total, "sample": self.sample,
                "pages": pages,
                "source_url": f"https://data.seoul.go.kr/dataList/{dataset}/S/1/datasetView.do"}


def normalize_services(list_rows, detail_rows, retrieved_at):
    """목록/상세는 SERV_ID 조인. 이름·행 순서로 병합하지 않으며 상세 누락을 보존한다."""
    details, listed, result = {}, set(), []
    for row in detail_rows:
        key = row.get("SERV_ID")
        if not key or key in details:
            raise ApiFailure("DUPLICATE_OR_MISSING_DETAIL_ID")
        details[key] = row
    for row in list_rows:
        key = row.get("SERV_ID")
        if not key or key in listed:
            raise ApiFailure("DUPLICATE_OR_MISSING_LIST_ID")
        listed.add(key)
        detail = details.get(key, {})
        if detail and row.get("SERV_NM") != detail.get("SERV_NM"):
            raise ApiFailure("LIST_DETAIL_ATTRIBUTE_CONFLICT")
        target = detail.get("SPRT_TRGT_CN", "")
        content = " ".join(str(detail.get(k, row.get(k, "")) or "") for k in ("SERV_DGST", "ALW_SERV_CN", "SPRT_TRGT_CN", "SLCT_CRIT_CN"))
        r = base_resource("service:" + key, key, row["SERV_NM"], detail.get("BIZ_CHR_DEPT_NM", row.get("BIZ_CHR_DEPT_NM", "")),
                          "SERVICE_RECORDED", content, target=target)
        r["operating_period"] = period(detail.get("ENFC_BGNG_YMD"), detail.get("ENFC_END_YMD"), open_end=detail.get("ENFC_END_YMD") == "99991231")
        r["geography"]["provider_jurisdiction"] = " ".join(str(row.get(k, "") or "") for k in ("CTPV_NM", "SGG_NM"))
        r["application"].update(method=detail.get("APLY_MTD_CN", row.get("APLY_MTD_NM")) or None,
                                  url=row.get("SERV_DTL_LINK") or None, contact=detail.get("INQ_NUM", row.get("INQ_NUM")) or None)
        r["published_or_updated_at"] = detail.get("LAST_MOD_YMD", row.get("LAST_MOD_YMD")) or None
        r["retrieved_at"] = retrieved_at
        r["source_urls"] = [f"https://data.seoul.go.kr/dataList/{dataset}/S/1/datasetView.do" for dataset in ("OA-23013", "OA-23014") if detail or dataset == "OA-23013"]
        r["provenance"] = [lineage("OA-23013", None, row, retrieved_at)]
        if detail:
            r["provenance"].append(lineage("OA-23014", None, detail, retrieved_at))
        r["verification"]["notes"] = ["API 조회 성공은 운영·접수 가능 확인이 아니다. 대상·선정 기준 원문을 확인해야 한다."]
        if not detail:
            r["verification"]["notes"].append("DETAIL_NOT_FOUND_BY_SERV_ID")
        result.append(finalize(r))
    return result, {"listed": len(listed), "matched": len(listed & set(details)),
                    "list_without_detail_ids": sorted(listed - set(details)), "detail_without_list_ids": sorted(set(details) - listed)}


def collect_api(*, client=None, retrieved_at=None):
    """외부 요청은 이 명시적 갱신 함수에서만. 한 소스 실패가 다른 소스/로컬을 막지 않는다."""
    client = client or SeoulWelfareClient()
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    collected, states, resources = {}, {}, []
    for source in SERVICES:
        try:
            response = client.fetch(source)
            collected[source] = response
            states[source] = {k: response[k] for k in ("dataset", "service", "total", "complete", "sample", "source_url")}
            states[source].update(status="SAMPLE_ONLY" if response["sample"] else "SUCCESS", retrieved_at=retrieved_at, returned=len(response["rows"]))
            states[source]['pages']=response['pages']
        except ApiFailure as error:
            states[source] = {"status": "FAILED", "reason": str(error), "retrieved_at": retrieved_at}
    # 샘플은 API 명세 진단 전용이며 전체 검색 카탈로그·캐시로 승격하지 않는다.
    if not client.sample:
        if "programs" in collected:
            try:
                resources.extend(normalize_program(row, "OA-21309", None, retrieved_at) for row in collected["programs"]["rows"])
            except (ValueError, TypeError, KeyError):
                states["programs"] = {"status": "FAILED", "reason": "NORMALIZATION_SCHEMA_CHANGED", "retrieved_at": retrieved_at}
                resources = []
        if "list" in collected:
            try:
                normalized, join = normalize_services(collected["list"]["rows"], collected.get("detail", {}).get("rows", []), retrieved_at)
                resources.extend(normalized)
                states["service_join"] = join
            except (ValueError, TypeError, KeyError):
                states["service_join"] = {"status": "FAILED", "reason": "NORMALIZATION_OR_JOIN_SCHEMA_CHANGED"}
    return {"meta": {"schema_version": "welfare-api-cache.v1", "retrieved_at": retrieved_at, "sample_only": client.sample,
                     "api_status": states, "calls_attempted": client.calls, "operation_verified": False}, "resources": resources}


def refresh_api(*, client=None, retrieved_at=None):
    if not _REFRESH_LOCK.acquire(blocking=False):raise ApiFailure('API_REFRESH_BUSY')
    try:return _refresh_api(client=client,retrieved_at=retrieved_at)
    finally:_REFRESH_LOCK.release()


def _refresh_api(*, client=None, retrieved_at=None):
    document = collect_api(client=client, retrieved_at=retrieved_at)
    # 실패/샘플/부분 다운로드로 직전 성공 캐시를 덮어쓰지 않는다.
    complete = all(document["meta"]["api_status"].get(k, {}).get("status") == "SUCCESS" for k in SERVICES)
    join_ok = document["meta"]["api_status"].get("service_join", {}).get("status") != "FAILED"
    document["meta"]["cache_written"] = bool(complete and join_ok and not document["meta"]["sample_only"])
    if document["meta"]["cache_written"]:
        # 신뢰 가능한 수집 경로의 완성본과 나중에 편집된 캐시를 구별한다.
        document["meta"]["integrity_sha256"] = hashlib.sha256(canonical_bytes(document["resources"])).hexdigest()
        validate_api_document(document, require_complete=True)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        temporary = CACHE.with_suffix(".json.tmp")
        try:
            temporary.write_bytes(canonical_bytes(document))
            temporary.replace(CACHE)
        finally:
            if temporary.exists():
                temporary.unlink()
    return document


def combine_catalogs(local, api):
    """같은 원본 ID+제목+기관일 때만 합치고 로컬/실시간 원문을 각각 보존한다."""
    combined = {r["id"]: deepcopy(r) for r in local["resources"]}
    conflicts = []
    for r in api["resources"]:
        key = r["id"]
        if key in combined:
            old = combined[key]
            if old["original_id"] != r["original_id"] or old["title"] != r["title"] or old["provider"] != r["provider"]:
                conflicts.append(key)
                # 충돌 API 원문은 API 문서/캐시에 남긴다. 검색은 로컬을 보존하고 충돌 ID를 보고한다.
                continue
            new = deepcopy(r)
            new["provenance"] = deepcopy(old["provenance"]) + new["provenance"]
            new["curated"] = old["curated"]
            if "curation" in old:
                new["curation"] = deepcopy(old["curation"])
            # 선별 태그는 추가 확인용 근거로 함께 보존한다.
            new["support_tags"] = sorted(set(new["support_tags"] + old["support_tags"]))
            new["tag_evidence"] += deepcopy(old["tag_evidence"])
            new["verification"]["notes"].append("동일 ID·제목·기관 확인 후 API 게시 속성 적용. 운영 확인은 별도.")
            combined[key] = new
        else:
            combined[key] = deepcopy(r)
    return {"meta": deepcopy(local["meta"]), "resources": sorted(combined.values(), key=lambda r: r["id"])}, conflicts


def validate_api_document(api, require_complete=False):
    require(isinstance(api, dict) and isinstance(api.get("meta"), dict), "잘못된 API 문서")
    meta = api["meta"]
    require(meta.get("schema_version") == "welfare-api-cache.v1" and meta.get("sample_only") is False, "샘플/잘못된 API 문서")
    require(isinstance(api.get("resources"), list) and isinstance(meta.get("api_status", {}), dict), "잘못된 API 문서 스키마")
    if require_complete:
        states=meta['api_status']
        require('integrity_sha256' in meta and meta.get('cache_written') is True, '완료된 인증 캐시 필요')
        require(all(states.get(k,{}).get('status')=='SUCCESS' and states[k].get('complete') is True
                    and states[k].get('sample') is False and type(states[k].get('returned')) is int
                    and states[k]['returned']==states[k].get('total') for k in SERVICES),'불완전한 API 캐시')
        require(states.get('service_join',{}).get('status')!='FAILED' and 'matched' in states.get('service_join',{}),'API 조인 검증 누락')
        require(len(api['resources'])==states['programs']['returned']+states['list']['returned'],'API 정규화 행 수 불일치')
    if "integrity_sha256" in meta:
        require(meta["integrity_sha256"] == hashlib.sha256(canonical_bytes(api["resources"])).hexdigest(), "API 캐시 무결성 불일치")
    require(len(api["resources"]) <= 20000, "API 캐시 자원 수 한도 초과")
    # 검색에 필요한 구조를 실제로 순회해 검증한다. 원문 스키마 변경은 로컬 대체로 이어져야 한다.
    seen = set()
    for resource in api["resources"]:
        require(isinstance(resource, dict) and isinstance(resource.get("id"), str) and resource["id"] not in seen, "API 자원 ID 누락/중복")
        seen.add(resource["id"])
        require(isinstance(resource.get("provenance"), list) and all(isinstance(p, dict) for p in resource["provenance"]), "API 출처 스키마 오류")
        for key in ("title", "provider", "content", "resource_type"):
            require(isinstance(resource.get(key), str), "API 자원 문자열 스키마 오류")
        require(isinstance(resource.get("curated"), bool) and isinstance(resource.get("support_tags"), list), "API 태그 스키마 오류")
        search_resources(mode="historical", catalog={"resources": [resource]}, limit=1)
    return api


def attach_source_reviews(resource):
    """Attach scoped review notes without rewriting frozen catalog values."""
    try:
        if not SOURCE_REVIEWS.is_file() or SOURCE_REVIEWS.stat().st_size>65536:return resource
        review=read_json_strict(SOURCE_REVIEWS).get('resources',{}).get(resource['id'])
        if not isinstance(review,dict):return resource
        from welfare_resources import iso_date
        require(iso_date(review.get('checked_on')) is not None,'Invalid review date')
        url=urllib.parse.urlsplit(review.get('url',''))
        require(url.scheme=='https' and url.hostname in ('www.gangnam1.org','bokji.gangnam.go.kr','www.seochoyc.org') and not url.username and not url.password,'Invalid source review URL')
        require(review.get('status') in ('PARTIAL_MATCH','CONFLICT_RECONFIRM','SEARCH_SNAPSHOT_ONLY') and isinstance(review.get('note'),str) and len(review['note'])<=1000,'Invalid source review')
        resource['source_review']=deepcopy(review)
        if review['status']!='SEARCH_SNAPSHOT_ONLY':resource['source_checked_at']=review['checked_on']
    except (ValueError,OSError,TypeError,KeyError,AttributeError):pass
    return resource


@lru_cache(maxsize=1)
def _verified_projection(path,size,mtime):
    api=read_json_strict(Path(path));validate_api_document(api,require_complete=True)
    # Raw source bodies remain intact on disk; ordinary searches never need embedded images.
    for r in api['resources']:
        r['provenance']=[{k:v for k,v in p.items() if k!='raw'} for p in r['provenance']]
    return api


def load_verified_api_cache():
    state=CACHE.stat()
    return _verified_projection(str(CACHE.resolve()),state.st_size,state.st_mtime_ns)


def query_welfare(*, adm_cd8=None, api_document=None, use_api_cache=False, catalog=None, **kwargs):
    """STEP 4 진입점. 일반 조회는 네트워크/갱신 없음. 오류·캐시 부재는 로컬로 대체."""
    from welfare_resources import load_catalog
    local = catalog if catalog is not None else load_catalog()
    api, status, conflicts = api_document, "NOT_REQUESTED", []
    disk_verified=False
    if use_api_cache and api is None:
        try:
            api = load_verified_api_cache()
            disk_verified=True
        except (ValueError, OSError):
            status = "CACHE_MISSING_OR_INVALID_LOCAL_FALLBACK"
            api = None
    source = local
    api_meta = {}
    if api is not None:
        try:
            if not disk_verified:validate_api_document(api)
            api_meta = api["meta"]
            if api["resources"]:
                source, conflicts = combine_catalogs(local, api)
                status = "API_RECORDS_PLUS_LOCAL_RECHECK_FRESHNESS"
            else:
                status = "API_UNAVAILABLE_LOCAL_FALLBACK"
        except (ValueError, TypeError, KeyError, AttributeError):
            source, status = local, "INVALID_API_LOCAL_FALLBACK"
            # 실패 갱신 문서는 자원 없이 원인만 전달할 수 있다.
            if isinstance(api, dict) and isinstance(api.get("meta"), dict) and isinstance(api["meta"].get("api_status", {}), dict):
                api_meta = api["meta"]
    result = search_for_dong(adm_cd8, catalog=source, **kwargs) if adm_cd8 else search_resources(catalog=source, **kwargs)
    for bucket in result['results'].values():
        for resource in bucket:attach_source_reviews(resource)
    result["data_status"] = {"source": "API_PLUS_LOCAL" if source is not local else "LOCAL_CATALOG", "api": status,
                             "freshness": "UNVERIFIED", "api_retrieved_at": api_meta.get("retrieved_at"),
                             "api_status": api_meta.get("api_status", {}),
                             "id_attribute_conflicts_local_kept": conflicts}
    states=api_meta.get('api_status',{})
    result['data_status'].update(local_catalog_records=len(local['resources']),
                                api_collection_complete=bool(states) and all(states.get(k,{}).get('status')=='SUCCESS' and states[k].get('complete') is True for k in SERVICES),
                                currently_available_confirmed_count=0)
    retrieved=api_meta.get("retrieved_at")
    age_days=None
    if isinstance(retrieved,str):
        try:
            when=datetime.fromisoformat(retrieved.replace('Z','+00:00'))
            require(when.tzinfo is not None, "API 조회일 timezone 필요")
            age_days=max(0,(datetime.now(timezone.utc)-when).total_seconds()/86400)
        except (ValueError,TypeError):
            age_days=None
    result["data_status"].update(cache_age_days=round(age_days,2) if age_days is not None else None,
                                cache_stale=age_days is None or age_days>7,
                                timestamp_proves_current_operation=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("diagnose-sample", "refresh"))
    parser.add_argument('--allow-http-authenticated',action='store_true',help='Explicitly allow official HTTP:8088 credential transport')
    parser.add_argument('--page-size',type=int,default=100)
    parser.add_argument('--max-calls',type=int,default=100)
    args = parser.parse_args()
    client = SeoulWelfareClient(sample=args.command == "diagnose-sample",page_size=args.page_size,max_calls=args.max_calls,allow_http_authenticated=args.allow_http_authenticated)
    result = collect_api(client=client) if client.sample else refresh_api(client=client)
    print(json.dumps(result["meta"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
