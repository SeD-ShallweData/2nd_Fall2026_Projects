"""한 곳에서 관리하는 프로젝트 경로, 기준 입력, 공간 캐시 채택 조건."""
from pathlib import Path
from collections import Counter
import copy
import json
import sqlite3

import pandas as pd

from workflow_io import file_state, write_json

INPUT_FILES = {
    "flow_age_pop": "성·연령별 유동",
    "flow_time_pop": "24시간 유동",
    "flow_wkdy_pop": "요일별 유동",
    "shinhan_card_2": "카드 통합본 (이번 분석에서는 사용하지 않음)",
}


def build_config(project_root=None):
    root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
    template = Path(__file__).with_name("config.example.json")
    config = copy.deepcopy(json.loads(template.read_text(encoding="utf-8")))
    config["input_dir"] = str(root / config["input_dir"])
    config["output_dir"] = str(root / config["output_dir"])
    config["layout_file"] = str(root / config["layout_file"])
    work = root / "Code" / "sk_admin_mapping"
    for level in ("sigungu", "emd"):
        config["boundaries"][level]["path"] = str(root / config["boundaries"][level]["path"])
    config["parent_crosswalk"]["path"] = str(root / config["parent_crosswalk"]["path"])
    for name in INPUT_FILES:
        if not (Path(config["input_dir"]) / (name + ".csv")).is_file():
            raise FileNotFoundError(f"기준 입력이 없습니다: {name}.csv / README의 데이터 준비를 확인하세요.")
    if not all(Path(config["boundaries"][level]["path"]).is_file() for level in ("sigungu", "emd")):
        from notebook_helpers import prepare_notebook_config
        prepared = prepare_notebook_config(work, base_config=config)
        for key in ("boundaries", "parent_crosswalk", "targets", "review"):
            config[key] = prepared[key]
    for table in config["tables"]:
        if table["name"] == "flow_age_pop":
            table["population_columns"] = [f"{sex}_FLOW_POP_CNT_{age}" for sex in ("MAN", "WMAN") for age in ("10G", "20G", "30G", "40G", "50G", "60GU")]
        elif table["name"] == "flow_time_pop":
            table["population_columns"] = [f"TMST_{h:02d}" for h in range(24)]
        else:
            table["population_columns"] = ["FLOW_POP_CNT_" + d for d in ("MON", "TUS", "WED", "THU", "FRI", "SAT", "SUN")]
    config["source_coordinates"].update(crs="EPSG:5179", evidence="사용자 요청에 따른 임시 가정: EPSG:5179, metre, xy, 배율 1. 공급자 확인 전이며 결과는 검토용.", unit="metre", xy_order="xy", scale_x=1.0, scale_y=1.0)
    return config


def mapping_settings(config):
    keys = ("columns", "source_coordinates", "boundary_mode", "boundaries", "parent_crosswalk", "block_crosswalk", "targets", "review")
    return {key: config.get(key) for key in keys}


def boundary_states(config):
    files = []
    for boundary in config["boundaries"].values():
        path = Path(boundary["path"])
        files.extend(sorted(path.parent.glob(path.stem + ".*")))
    files.append(Path(config["parent_crosswalk"]["path"]))
    return [file_state(p) for p in files if p.suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg", ".csv"}]


def validate_location_keys(config, sources, index_directory, result_directory):
    """새 통합 입력과 기존 인덱스의 정규화 키 집합을 양방향 대조한다."""
    import sk_admin_mapping as sk
    keys = set()
    monthly_rows = {}
    source_rows = {}
    for item in sources:
        counts = Counter()
        row_count = 0
        for chunk in pd.read_csv(item["path"], dtype=str, keep_default_na=False, encoding="utf-8-sig", chunksize=config["chunksize"], usecols=[config["columns"][k] for k in sk.ROLES]):
            normalized = [sk.normalize_values(row) for row in chunk.itertuples(index=False, name=None)]
            keys.update(normalized)
            counts.update(key[0] for key in normalized)
            row_count += len(chunk)
        monthly_rows[item["table"]] = dict(sorted(counts.items()))
        source_rows[item["table"]] = row_count
    database = sk.readonly_db(Path(index_directory) / "location_index.sqlite")
    try:
        old_keys = set(database.execute("SELECT ym, block, x, y FROM locations"))
    finally:
        database.close()
    same = keys == old_keys
    write_json(Path(result_directory) / "source_audit.json", {
        "input_basis": "데이터/전처리 데이터_통합본",
        "location_keys_match": same, "unique_location_keys": len(keys),
        "index_location_keys": len(old_keys), "input_only_keys": len(keys - old_keys),
        "index_only_keys": len(old_keys - keys), "monthly_rows": monthly_rows,
        "source_rows": source_rows, "input_files": [file_state(item["path"]) for item in sources],
        "note": "정규화 위치 키가 같은지 확인한 결과이며, 인구 값의 동일성이나 공급자 좌표계 확인을 뜻하지 않는다.",
    })
    print("통합 입력 위치 키:", len(keys), "/ 기존 인덱스:", len(old_keys), "/ 양방향 일치:", same)
    return same
