"""검증된 STEP 1 입력에서 신규 분류 JSON 생성. 기존 통합 JSON은 덮어쓰지 않는다."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import argparse
import json
import sys

from build_agent_input import canonical_bytes, load_integrated, output_path as integrated_path
from roa_classification import RULE_VERSION, classify_dong, compare_legacy, summarize, type_catalog
from roa_data import ROOT, RoaValidationError, read_json_strict, require, sha256

SCHEMA_VERSION = "agent_input_classified.v1"
OUTPUT_RELATIVE = "Code/sk_admin_mapping/outputs/canonical/agent/agent_input_classified.v1.json"


def build_classified(project_root=None):
    root = Path(project_root or ROOT).resolve()
    source_path = integrated_path(root)
    source_hash = sha256(source_path)
    source = load_integrated(root)  # STEP 1의 계약·hash·내용 검증을 그대로 통과해야 한다.
    document = deepcopy(source)
    for dong in document["dongs"]:
        dong["classification"] = classify_dong(dong)
    metadata = document["meta"]
    metadata["integration_schema_version"] = metadata["schema_version"]
    metadata["schema_version"] = SCHEMA_VERSION
    metadata["stage"] = "STEP2_CLASSIFIED"
    metadata["classification_status"] = "CLASSIFIED_WITH_HOLDS"
    metadata["future_classification_contract"]["status"] = "IMPLEMENTED_STEP2"
    metadata["classification_rule"] = {
        "version": RULE_VERSION, "relationship": "세 기준월 각각 21동 중앙값 엄격 초과, 세 월 모두 충족",
        "activity": "9~11월 창 < 7~9월 창 AND 11월 < 7월",
        "numeric_comparison": "ORIGINAL_DECIMAL_TEXT_WHERE_AVAILABLE_ELSE_JSON_NUMERIC_TEXT",
        "missing_state": "UNDETERMINED", "minimum_decrease_threshold_added": False,
        "type_order_semantics": "직렬화용 범주 순서. 위험 순위 없음",
        "classification_scope": "2025년 10~12월 기준월 종합 점검. 상세 SK 월 선택과 독립",
    }
    metadata["classification_catalog"] = type_catalog()
    metadata["classification_summary"] = summarize(document["dongs"])
    metadata["classification_legacy_comparison"] = compare_legacy(document["dongs"])
    metadata["classification_generation"] = {
        "deterministic": True, "source_schema_version": source["meta"]["schema_version"],
        "source_input": {"path": source_path.relative_to(root).as_posix(), "sha256": source_hash},
        "implementation_sha256": {name: sha256(Path(__file__).with_name(name))
                                   for name in ("roa_classification.py", "build_classified_agent_input.py")},
    }
    metadata["classification_evaluation_scope"] = "규칙 재현성과 소프트웨어 정합성 검증. 실제 사회적 고립 예측 성능은 미평가."
    require(sha256(source_path) == source_hash, "분류 중 STEP 1 입력 변경")
    return document


def output_path(project_root=None):
    return Path(project_root or ROOT).resolve() / OUTPUT_RELATIVE


def write_classified(project_root=None):
    document = build_classified(project_root)
    path = output_path(project_root)
    payload = canonical_bytes(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == payload:
        return path, document
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path, document


def load_classified(project_root=None):
    """Dashboard/Agent 공통 진입점. 현재 STEP 1/분류 코드와 다른 산출물은 거부한다."""
    path = output_path(project_root)
    require(path.is_file(), "분류 JSON 없음. build_classified_agent_input.py를 먼저 실행하세요.")
    actual = read_json_strict(path)
    expected = build_classified(project_root)
    require(canonical_bytes(actual) == canonical_bytes(expected), "분류 JSON이 현재 입력/규칙/코드와 다릅니다. 재생성하세요.")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="분류 JSON을 읽기 전용 검증")
    args = parser.parse_args()
    try:
        if args.check:
            document = load_classified(args.project_root)
            path = output_path(args.project_root)
        else:
            path, document = write_classified(args.project_root)
        print(json.dumps({"status": "passed", "path": str(path), "schema": SCHEMA_VERSION,
                          "classified": document["meta"]["classification_summary"]["classified_count"],
                          "hold": document["meta"]["classification_summary"]["hold_count"]}, ensure_ascii=False))
    except (RoaValidationError, OSError, KeyError, TypeError) as error:
        print(f"ROA 분류 실패: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
