"""공통 파일 IO와 완료된 캐시 검증. 입력 파일에는 쓰지 않는다."""
from pathlib import Path
import json
import hashlib
import pandas as pd

def read_json(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        return {}

def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)

def file_state(path):
    path = Path(path).resolve()
    info = path.stat()
    return {'path': str(path), 'bytes': info.st_size, 'mtime_ns': info.st_mtime_ns}

def fingerprint(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def cache_valid(state, key):
    if state.get('status') != 'complete':
        return False
    if state.get('fingerprint') != key:
        return False
    outputs = state.get('outputs', [])
    if not outputs:
        return False
    try:
        return all((file_state(item['path']) == item for item in outputs))
    except OSError:
        return False

def complete_state(path, key, files, **extra):
    write_json(path, {'status': 'complete', 'fingerprint': key, 'outputs': [file_state(p) for p in files], **extra})

def read_result_csv(path):
    identifiers = {'STD_YM': str, 'STD_YM_KEY': str, 'SIGUNGU_CD': str, 'EMD_CD': str, 'TABLE': str, 'METRIC_FIELD': str}
    return pd.read_csv(path, encoding='utf-8-sig', dtype=identifiers)

def read_csv(path, **kwargs):
    return pd.read_csv(Path(path), encoding="utf-8-sig", **kwargs)

def check_cache(state, label):
    if state.get("status") != "complete" or not state.get("outputs"):
        raise ValueError(f"{label}: 01_데이터_준비.ipynb를 먼저 실행하세요.")
    for item in state["outputs"]:
        if not Path(item["path"]).is_file() or file_state(item["path"]) != item:
            raise ValueError(f"{label}: 입력/결과가 바뀌었습니다. 01_데이터_준비.ipynb를 다시 실행하세요.")
