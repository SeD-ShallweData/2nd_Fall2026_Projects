"""주피터에서 공식 경계를 자동 준비하고 기존 위치 인덱스를 재사용하는 함수.

공식 경계 자료만 준비한다. SK 입력 데이터 분석/공간 결합은 별도 함수 호출 시 실행된다.
"""
from __future__ import annotations

import copy
import csv
import http.cookiejar
import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, is_zipfile

PORTAL_PAGE = "https://www.data.go.kr/data/15129688/fileData.do"
SGIS_INFO = "https://sgis.mods.go.kr/view/pss/openDataIntrcn"
RULE_URL = "https://sgis.mods.go.kr/html/attachFiles/%ED%95%9C%EA%B5%AD%ED%96%89%EC%A0%95%EA%B5%AC%EC%97%AD%EB%B6%84%EB%A5%98%20%EC%8B%A0%C2%B7%EA%B5%AC%20%EB%8C%80%EB%B9%84%ED%91%9C.pdf"
REFERENCE = "2025-06-30"
CODE_SYSTEM = "SGIS_Korean_administrative_classification_8digit"
STEMS = {"sigungu": "bnd_sigungu_00_2025_2Q", "emd": "bnd_dong_00_2025_2Q"}
FIELDS = {"sigungu": ("SIGUNGU_CD", "SIGUNGU_NM"), "emd": ("ADM_CD", "ADM_NM")}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def portal_request(opener, path, data=None):
    url = urllib.parse.urljoin(PORTAL_PAGE, path)
    if urllib.parse.urlparse(url).hostname != "www.data.go.kr":
        raise ValueError("공식 포털 외 주소로 요청하지 않습니다.")
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=body, headers={"Referer": PORTAL_PAGE})
    return opener.open(request, timeout=180)


def download_official_zip(work):
    """포털의 공개 다운로드 절차를 따름. CAPTCHA 요구 시 우회하지 않고 중단."""
    cache = Path(work) / "boundary_cache"
    cache.mkdir(exist_ok=True)
    archive = cache / "sgis_2025_official.zip"
    metadata_path = cache / "official_download_metadata.json"
    if archive.exists():
        if not metadata_path.exists() or not is_zipfile(archive):
            raise ValueError("기존 경계 캐시의 출처 기록이 없거나 ZIP이 아닙니다. 기존 파일을 덮어쓰지 않습니다.")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("reference_date") != REFERENCE or metadata.get("portal_page") != PORTAL_PAGE:
            raise ValueError("기존 경계 캐시의 기준일 또는 출처가 다릅니다.")
        if metadata.get("bytes") and archive.stat().st_size != metadata["bytes"]:
            raise ValueError("경계 ZIP의 크기가 출처 기록과 다릅니다. 부분 다운로드일 수 있습니다.")
        print("공식 2025 경계 ZIP 재사용:", archive)
        return archive
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with portal_request(opener, PORTAL_PAGE) as response:
        html = response.read().decode("utf-8")
    calls = re.findall(r"fileDetailObj\.fn_fileDataDown\((.*?)\)", html, flags=re.S)
    arguments = None
    for call in calls:
        values = [value.strip() for value in re.findall(r"'([^']*)'", call)]
        if len(values) >= 4 and values[0] == "15129688":
            arguments = values
            break
    if arguments is None:
        raise RuntimeError(f"공식 포털의 다운로드 양식이 변경되었습니다: {PORTAL_PAGE}")
    with portal_request(opener, "/tcs/dss/selectFileDataDownload.do", {
        "publicDataPk": arguments[0], "publicDataDetailPk": arguments[1],
        "atchFileId": arguments[2], "fileDetailSn": arguments[3], "publicDataTyCode": "PR0051"
    }) as response:
        metadata = json.load(response)
    if metadata.get("status") is not True:
        raise RuntimeError("포털이 공개 파일 다운로드를 허용하지 않았습니다. 로그인/보안문자 화면이 필요하면 사용자가 포털에서 처리해야 합니다.")
    info = metadata["dataSetFileDetailInfo"]
    standard_date = str(info.get("stdrDe", "")).replace("/", "").replace("-", "")
    if standard_date not in ("06302025", "20250630") or "2025" not in str(info.get("publicDataDc", "")):
        raise RuntimeError("포털의 최신 자료가 요청한 2025-06-30 경계와 다릅니다. 다른 연도로 자동 대체하지 않습니다.")
    attachment = {"atchFileId": metadata["atchFileId"], "fileDetailSn": metadata["fileDetailSn"]}
    with portal_request(opener, "/cmm/cmm/check-limit.json", attachment) as response:
        guard = json.load(response)
    if guard.get("needCaptcha") is not False:
        raise RuntimeError(f"포털에서 보안문자 확인이 필요합니다. 자동 요청을 중단했습니다: {PORTAL_PAGE}")
    params = {**attachment, "dataNm": info["dataNm"]}
    download_path = "/cmm/cmm/fileDownload.do?" + urllib.parse.urlencode(params)
    partial = cache / f"download_{uuid4().hex}.part"
    total = 0
    try:
        with portal_request(opener, download_path) as response, partial.open("xb") as output:
            advertised = int(response.headers.get("Content-Length", "0"))
            if advertised > 600 * 1024 * 1024:
                raise RuntimeError("공식 패키지가 600MiB보다 큽니다. 예상 자료가 맞는지 확인 후 진행해주세요.")
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > 600 * 1024 * 1024:
                    raise RuntimeError("예상 다운로드 크기를 넘었습니다.")
                output.write(block)
                if total // (16 * 1024 * 1024) != (total - len(block)) // (16 * 1024 * 1024):
                    print(f"다운로드: {total / 1024**2:.0f} MiB", flush=True)
        if advertised and total != advertised:
            raise RuntimeError("다운로드가 끝나지 않았습니다.")
        if not is_zipfile(partial):
            raise RuntimeError("서버가 ZIP 대신 오류/로그인 페이지를 반환했습니다.")
        if archive.exists():
            raise FileExistsError("기존 경계 ZIP을 덮어쓰지 않습니다.")
        partial.rename(archive)
        write_json(metadata_path, {
            "portal_page": PORTAL_PAGE, "public_data_pk": "15129688", "attachment_id": metadata["atchFileId"],
            "file_detail_sn": metadata["fileDetailSn"], "data_name": info["dataNm"], "reference_date": REFERENCE,
            "bytes": total, "downloaded_at_utc": datetime.now(timezone.utc).isoformat()
        })
    finally:
        if partial.exists():
            partial.unlink()  # 이번 호출에서 생성한 부분 파일만 정리
    return archive


def extract_official_boundaries(archive, work):
    raw = Path(work) / "boundary_cache" / "prepared_raw"
    raw.mkdir(exist_ok=True)
    sidecars = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix"}
    with ZipFile(archive) as package:
        for info in package.infolist():
            name = Path(info.filename).name
            selected_shape = any(name.startswith(stem + ".") for stem in STEMS.values()) and Path(name).suffix.lower() in sidecars
            if not selected_shape:
                continue
            # 이름을 평탄화하여 archive의 경로를 실행 호스트 경로로 사용하지 않는다.
            target = raw / name
            if target.exists():
                if target.stat().st_size != info.file_size:
                    raise ValueError(f"기존 준비 파일의 크기가 다릅니다: {target}")
                continue
            temporary = raw / (uuid4().hex + ".part")
            try:
                with package.open(info) as source, temporary.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                temporary.rename(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
    for stem in STEMS.values():
        for extension in (".shp", ".shx", ".dbf", ".prj"):
            if not (raw / (stem + extension)).is_file():
                raise FileNotFoundError(f"공식 ZIP에 필요한 경계 구성 파일이 없습니다: {stem}{extension}")
    return raw


def prepare_notebook_config(work, base_config=None):
    """실제 공식 경계 속성 + 공식 코드 구성 규칙으로 경계 설정을 자동 작성."""
    import pandas as pd
    import pyogrio
    from pyproj import CRS
    work = Path(work).resolve()
    config = base_config
    if config is None:
        config = json.loads((work / "config.example.json").read_text(encoding="utf-8-sig"))
        root = work.parents[1]
        for field in ("input_dir", "output_dir", "layout_file"):
            config[field] = str(root / config[field])
    config = copy.deepcopy(config)
    raw = work / "boundary_cache" / "prepared_raw"
    ready = all((raw / (stem + extension)).is_file() for stem in STEMS.values() for extension in (".shp", ".shx", ".dbf", ".prj"))
    if not ready:
        archive = download_official_zip(work)
        raw = extract_official_boundaries(archive, work)
        # 공식 묶음에서 필요한 경계만 준비한다. 통계 원자료 ZIP은 보관하지 않는다.
        resolved_archive = archive.resolve()
        if resolved_archive.parent != (work / "boundary_cache").resolve() or resolved_archive.name != "sgis_2025_official.zip":
            raise ValueError("경계 준비 후 임시 ZIP 정리 경로가 다릅니다.")
        resolved_archive.unlink()
    attributes = {}
    provenance = {"portal": PORTAL_PAGE, "sgis_info": SGIS_INFO, "classification_rule": RULE_URL, "reference_date": REFERENCE, "layers": {}}
    for level, stem in STEMS.items():
        path = raw / (stem + ".shp")
        info = pyogrio.read_info(path)
        code, name = FIELDS[level]
        if not {"BASE_DATE", code, name}.issubset(info["fields"]):
            raise ValueError(f"공식 파일의 열이 해당 패키지의 확인된 구조와 다릅니다: {path}; {info['fields']}")
        if info.get("crs") is None or not CRS.from_user_input(info["crs"]).equals(CRS.from_epsg(5179)):
            raise ValueError(f"공식 PRJ의 CRS가 해당 패키지의 EPSG:5179와 다릅니다: {path}")
        frame = pyogrio.read_dataframe(path, columns=["BASE_DATE", code, name], read_geometry=False)
        if set(frame["BASE_DATE"]) != {"20250630"}:
            raise ValueError(f"실제 경계 기준일이 20250630으로 일치하지 않습니다: {path}")
        length = 5 if level == "sigungu" else 8
        if not frame[code].map(lambda value: isinstance(value, str) and re.fullmatch(rf"\d{{{length}}}", value) is not None).all():
            raise ValueError(f"공식 코드가 문자열 {length}자리 규칙과 다릅니다: {path}")
        if frame[code].duplicated().any() or frame[name].isna().any():
            raise ValueError(f"공식 경계의 코드가 중복되거나 이름이 비었습니다: {path}")
        attributes[level] = frame
        config["boundaries"][level].update(
            path=str(path), code_field=code, name_field=name, code_system=CODE_SYSTEM,
            reference_date=REFERENCE, evidence=f"{PORTAL_PAGE}; ZIP {stem}; 실제 BASE_DATE=20250630, PRJ={info['crs']}",
            layer=None, encoding=None, crs_if_missing=None, crs_evidence=None
        )
        provenance["layers"][level] = {"path": str(path), "crs": info["crs"], "fields": list(info["fields"]), "feature_count": int(info["features"]), "reference_date_field": "BASE_DATE", "reference_date_value": "20250630"}
    config["boundaries"]["emd"]["kind"] = "administrative"
    sigma, emd = attributes["sigungu"], attributes["emd"]
    # SGIS 공식 '한국행정구역분류 신·구 대비표'의 8자리 구성을 적용.
    # 대분류=1,2번째, 중분류=3,4,5번째, 소분류=6,7,8번째 자리.
    # BLOCK_CD에는 적용하지 않는다. 경계 ADM_CD에만 공식 규칙을 적용한다.
    parent = pd.DataFrame({"EMD_CD": emd["ADM_CD"], "EMD_NM": emd["ADM_NM"], "SIGUNGU_CD": emd["ADM_CD"].str[:5], "BASE_DATE": "20250630"})
    parent = parent.merge(sigma[["SIGUNGU_CD", "SIGUNGU_NM"]], on="SIGUNGU_CD", how="left", validate="many_to_one")
    if parent["SIGUNGU_NM"].isna().any():
        parent[parent["SIGUNGU_NM"].isna()].to_csv(raw.parent / "unresolved_official_parents.csv", index=False, encoding="utf-8-sig")
        raise ValueError("공식 코드 구성으로 얻은 상위 코드가 같은 기준일 시군구 목록에 없습니다. 임의 대응하지 않습니다.")
    parent["RELATION_METHOD"] = "derived_from_official_classification_rule_and_same_date_boundary_codes"
    parent_path = raw.parent / "parent_crosswalk_20250630.csv"
    if not parent_path.exists() or not pd.read_csv(parent_path, dtype=str, encoding="utf-8-sig").equals(parent.astype(str)):
        parent.to_csv(parent_path, index=False, encoding="utf-8-sig")
    config["parent_crosswalk"].update(
        path=str(parent_path), encoding="utf-8-sig", separator=",", emd_column="EMD_CD", sigungu_column="SIGUNGU_CD",
        emd_code_system=CODE_SYSTEM, sigungu_code_system=CODE_SYSTEM, reference_date=REFERENCE,
        evidence=f"공식 코드 구성 규칙 {RULE_URL}; 원본 ADM_CD와 SIGUNGU_CD 목록(동일 BASE_DATE=20250630)으로 생성. 배포 대응표를 그대로 전재한 것은 아님."
    )
    for target in config["targets"]:
        selected = sigma.loc[sigma["SIGUNGU_NM"].eq(target["name"]), "SIGUNGU_CD"]
        if len(selected) != 1:
            raise ValueError(f"대상 지역 이름을 하나의 공식 코드로 결정할 수 없습니다: {target['name']}")
        target["sigungu_code"] = selected.iloc[0]
    config["review"]["metric_crs"] = "EPSG:5179"  # 확인된 SGIS 경계 CRS. SK 원본 CRS를 지정하는 값이 아님.
    config["boundary_mode"] = "fixed_snapshot"
    # 사용자가 첨부한 두 레이아웃 사진에서 확인한 구성만 반영.
    for table in config["tables"]:
        if table["name"] == "flow_age_pop":
            table.update(primary_key=["STD_YM", "BLOCK_CD", "X_COORD", "Y_COORD"],
                row_meaning="월·소지역·제공 좌표별 성연령(10세 단위) 일평균 유동인구",
                population_columns=[f"{gender}_FLOW_POP_CNT_{age}" for gender in ("MAN", "WMAN") for age in ("10G", "20G", "30G", "40G", "50G", "60GU")])
        elif table["name"] == "flow_time_pop":
            table.update(primary_key=["STD_YM", "BLOCK_CD", "X_COORD", "Y_COORD"],
                row_meaning="월·소지역·제공 좌표별 1시간 단위 시간대 일평균 유동인구",
                population_columns=[f"TMST_{hour:02d}" for hour in range(24)])
    config["schema_evidence"] = "사용자 첨부 성연령/시간대 테이블 정의서 사진. flow_wkdy_pop은 이 두 사진으로 확인하지 않음. SK 원본 EPSG·단위·축·배율은 미확정으로 유지."
    config_path = raw.parent / "config_prepared.local.json"
    write_json(config_path, config)
    write_json(raw.parent / "boundary_preparation.json", {**provenance, "parent_relation_method": "derived_using_official_classification_rule", "parent_rows": len(parent), "targets": config["targets"], "config_path": str(config_path), "scope": "official boundary preparation only; no SK point transformation or spatial join"})
    print("경계·공식 코드 규칙 기반 상하위 대응·대상 지역 코드 설정 완료")
    print("경계 기준일:", REFERENCE)
    print("시군구:", len(sigma), "행정 읍면동:", len(emd))
    print("대상 지역:", config["targets"])
    print("주피터용 설정:", config_path)
    return config


def run_mapping_from_notebook(config, source_crs=None, evidence=None, unit=None, xy_order=None, scale_x=None, scale_y=None):
    """준비된 경계와 기존 index 사용. 확인되지 않은 SK CRS는 자동 추정하지 않음."""
    from sk_admin_mapping import map_command
    config = copy.deepcopy(config)
    replacements = {"crs": source_crs, "evidence": evidence, "unit": unit, "xy_order": xy_order, "scale_x": scale_x, "scale_y": scale_y}
    for field, value in replacements.items():
        if value is not None:
            config["source_coordinates"][field] = value
    settings = config["source_coordinates"]
    missing = [field for field in replacements if settings.get(field) is None or settings.get(field) == ""]
    if missing:
        raise ValueError("경계 준비는 완료되었습니다. SK 제공 좌표는 사진에 UTM-K만 있어 아직 확정되지 않았습니다. 공급자 근거로 다음 값을 채운 뒤 이 셀만 재실행하세요: " + ", ".join(missing))
    return map_command(config, "latest")


def show_review_map(config, mapping_dir):
    """주피터에 지역 경계와 고정 seed 검토 표본을 표시. 전체 점은 결과 GeoJSON에 있음."""
    import folium
    import geopandas as gpd
    import pandas as pd
    mapping_dir = Path(mapping_dir)
    samples = pd.read_csv(mapping_dir / "review_samples.csv", dtype=str, keep_default_na=False)
    sigma_settings = config["boundaries"]["sigungu"]
    sigma = gpd.read_file(sigma_settings["path"], engine="pyogrio")
    target_codes = {target["sigungu_code"] for target in config["targets"]}
    sigma = sigma[sigma[sigma_settings["code_field"]].isin(target_codes)].copy()
    if sigma.empty:
        raise ValueError("대상 지역 경계가 없습니다.")
    emd_settings = config["boundaries"]["emd"]
    parent = pd.read_csv(config["parent_crosswalk"]["path"], dtype=str)
    emd_codes = set(parent.loc[parent["SIGUNGU_CD"].isin(target_codes), "EMD_CD"])
    emd = gpd.read_file(emd_settings["path"], engine="pyogrio")
    emd = emd[emd[emd_settings["code_field"]].isin(emd_codes)].copy()
    # 브라우저 표시만 가볍게 한다. 실제 매핑은 원본 상세 도형을 사용한다.
    sigma.geometry = sigma.geometry.simplify(30, preserve_topology=True)
    emd.geometry = emd.geometry.simplify(30, preserve_topology=True)
    sigma = sigma.to_crs(4326)
    emd = emd.to_crs(4326)
    bounds = sigma.total_bounds
    result = folium.Map(tiles="OpenStreetMap")
    result.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])
    folium.GeoJson(sigma.to_json(), name="시군구 경계: 2025-06-30", tooltip=folium.GeoJsonTooltip(fields=[sigma_settings["code_field"], sigma_settings["name_field"]], aliases=["시군구 코드", "시군구"]), style_function=lambda feature: {"color": "#1d4ed8", "weight": 3, "fillOpacity": 0.02}).add_to(result)
    folium.GeoJson(emd.to_json(), name="행정 읍면동 경계: 2025-06-30", tooltip=folium.GeoJsonTooltip(fields=[emd_settings["code_field"], emd_settings["name_field"]], aliases=["행정동 코드", "행정동"]), style_function=lambda feature: {"color": "#64748b", "weight": 1, "fillOpacity": 0.04}).add_to(result)
    import html
    for row in samples.to_dict("records"):
        if not row["LONGITUDE"] or not row["LATITUDE"]:
            continue
        from column_labels import COLUMN_LABELS, VALUE_LABELS
        text = "<br>".join(html.escape(COLUMN_LABELS.get(field, "검토 범주") + ": " + " · ".join(VALUE_LABELS.get(token, token) for token in str(row[field]).split("|"))) for field in ("STD_YM", "BLOCK_CD", "SIGUNGU_NM", "EMD_NM", "MATCH_STATUS", "SAMPLE_CATEGORIES"))
        folium.CircleMarker(location=[float(row["LATITUDE"]), float(row["LONGITUDE"])], radius=4,
            color="#16a34a" if row["MATCH_STATUS"] == "OK" else "#dc2626", fill=True, tooltip=text).add_to(result)
    folium.LayerControl().add_to(result)
    result.save(mapping_dir / "review_map.html")
    return result
