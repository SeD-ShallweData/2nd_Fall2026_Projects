"""GitHub 공개 전 읽기 전용 비밀값 검사. 파일 내용/키/매치 문자열은 출력하지 않는다."""
from pathlib import Path
import json
import os
import re

from llm_config import load_llm_config
from seoul_config import load_seoul_config
from roa_data import ROOT

SKIP_DIRS={'.git','.venv','__pycache__','node_modules','.ipynb_checkpoints','데이터','_local_archive','boundary_cache','population_cache'}
SUFFIXES={'.py','.js','.json','.html','.md','.txt','.ipynb','.yml','.yaml','.toml','.example'}


def audit(root=ROOT, include_runtime=True):
    root=Path(root).resolve()
    keys=[load_llm_config().api_key, load_seoul_config().api_key]
    keys += [value for name,value in os.environ.items() if re.search(r'API_KEY$|ACCESS_TOKEN$|SECRET_KEY$',name,re.I)]
    keys=[k for k in set(keys) if len(k)>=12]
    public_count=runtime_count=0
    findings=[]
    for directory,dirs,names in os.walk(root):
        dirs[:]=[d for d in dirs if d not in SKIP_DIRS and (include_runtime or d!='outputs')]
        for name in names:
            p=Path(directory)/name
            rel=p.relative_to(root).as_posix()
            if ('.env' in name.lower() and not name.endswith('.example')) or p.suffix.lower() in ('.pem','.key','.p12','.pfx'):
                findings.append({'path':rel,'kind':'PRIVATE_CREDENTIAL_FILE_IN_PROJECT','publishable':False})
                continue
            if p.suffix.lower() not in SUFFIXES and name!='.gitignore':continue
            if p.stat().st_size>20_000_000:continue
            raw=p.read_bytes()
            found=any(k.encode() in raw for k in keys)
            if found:findings.append({'path':rel,'kind':'EXACT_ACTIVE_SECRET_MATCH','publishable':'outputs/' not in rel})
            if 'outputs/' in rel:runtime_count+=1
            else:public_count+=1
    return {'status':'passed' if not findings else 'failed','public_text_files_checked':public_count,
            'ignored_runtime_text_files_checked':runtime_count,'active_secret_values_compared':len(keys),
            'findings':findings,'scope':'CURRENT_EXACT_CONFIGURED_SECRETS_AND_PRIVATE_FILE_PLACEMENT_NOT_ALL_HISTORIC_KEYS',
            'git_history_checked':False,'remote_upload_performed':False}


if __name__=='__main__':
    result=audit();print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='passed' else 1)
