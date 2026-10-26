"""Create a reviewed migration inventory; package protected data only after approval."""
from hashlib import sha256
from pathlib import Path, PureWindowsPath
import argparse
import json
import tarfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'Code/sk_admin_mapping/outputs/validation/step4gcp'
WORK = ROOT / 'Code/sk_admin_mapping'


def file_hash(path):
    before = path.stat()
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
        raise ValueError('SOURCE_CHANGED_DURING_HASH')
    return digest.hexdigest()


def inventory():
    clone = ROOT.parent / '2nd_Fall2026_Projects/BbiGeuDeok'
    public = json.loads((clone/'PUBLIC_FILE_MANIFEST.json').read_text(encoding='utf-8'))
    files = {}
    def add(path, role, legacy=None):
        path = path.resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or path.is_symlink():
            raise ValueError('UNREVIEWED_SOURCE_PATH')
        relative = path.relative_to(ROOT).as_posix()
        old = files.get(relative)
        files[relative] = {'path':relative, 'role':role if not old else old['role'],
                           'bytes':path.stat().st_size,'sha256':file_hash(path)}
        if legacy:
            files[relative]['legacy_state'] = legacy
    for entry in public['files']:
        prefix='BbiGeuDeok/Code/sk_admin_mapping/'
        if entry['path'].startswith(prefix):
            relative=entry['path'].removeprefix('BbiGeuDeok/')
            if not relative.endswith('.ipynb'):
                add(ROOT/relative, 'code')
    runtime_files={'gateway.py','state_store.py','runtime_backend.py','migration.py','healthcheck.py','dashboard.js','requirements-linux.txt','bootstrap.sh','install_application.sh',
                   'test_state_store.py','test_gateway.py','test_migration.py','test_healthcheck.py',
                   'bbigeudeok-backend.service','bbigeudeok-gateway.service','bbigeudeok-funnel.service','bbigeudeok-health.service','bbigeudeok-health.timer'}
    for path in (ROOT/'infra/gcp').glob('*'):
        if path.is_file() and path.name in runtime_files:
            add(path,'code')
    for name in ('demo_gateway.py','preview.html'):
        add(ROOT/'tools/step4'/name,'code')
    for contract in ('roa_contract.json','welfare_contract.json'):
        document=json.loads((WORK/contract).read_text(encoding='utf-8-sig'))
        for item in document.get('inputs', document.get('sources',{})).values():
            if isinstance(item,dict) and 'path' in item:
                add(ROOT/item['path'],'protected')
                if item.get('sha256') and file_hash(ROOT/item['path']) != item['sha256']:
                    raise ValueError('CONTRACT_SOURCE_HASH_MISMATCH')
    manifest=WORK/'outputs/canonical/spatial_explorer/manifest.json'
    doc=json.loads(manifest.read_text(encoding='utf-8-sig'))
    if doc.get('status') != 'complete':
        raise ValueError('SPATIAL_CACHE_NOT_COMPLETE')
    add(manifest,'protected')
    for item in doc['inputs']+doc['outputs']:
        add(Path(item['path']),'protected',item)
    for folder in ('agent','welfare'):
        for path in (WORK/'outputs/canonical'/folder).glob('*.json'):
            add(path,'protected')
    for name in ('step1b/source_checks.json',):
        path=WORK/'outputs/validation'/name
        if path.exists():
            add(path,'protected')
    result={'schema':'bbigeudeok.migration.v1','files':[files[p] for p in sorted(files)],
            'data_rights_confirmed':False,'original_root':str(ROOT)}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'migration_inventory.local.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'files':len(files),'protected_files':sum(x['role']=='protected' for x in files.values()),
                      'protected_bytes':sum(x['bytes'] for x in files.values() if x['role']=='protected')},ensure_ascii=False))
    return result


def install_verified_cache_adapter(root, manifest_path):
    """Verify original bytes before adapting only the manifest's explicit legacy paths."""
    root=Path(root).resolve()
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    records={}
    for item in manifest['files']:
        path=(root/item['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_hash(path) != item['sha256']:
            raise ValueError('MIGRATED_BYTES_NOT_VERIFIED')
        if 'legacy_state' in item:
            state=path.stat()
            records[str(PureWindowsPath(item['legacy_state']['path'])).lower()] = (path,item['legacy_state'],state.st_size,state.st_mtime_ns)
    import workflow_io
    original=workflow_io.file_state
    def portable_file_state(value):
        key=str(PureWindowsPath(str(value))).lower()
        if key not in records:
            return original(value)
        path,legacy,size,mtime=records[key]
        stat=path.stat()
        if (stat.st_size,stat.st_mtime_ns) != (size,mtime):
            raise ValueError('VERIFIED_MIGRATED_FILE_CHANGED')
        return dict(legacy)
    workflow_io.file_state=portable_file_state


def package(include_protected=False):
    plan=json.loads((OUT/'infrastructure_plan.local.json').read_text(encoding='utf-8'))
    if include_protected and plan.get('data_transfer_approved') is not True:
        raise ValueError('PROTECTED_GCP_TRANSFER_NOT_APPROVED')
    document=inventory()
    target=OUT/('application-private.tar.gz' if include_protected else 'application-code-only.tar.gz')
    with tarfile.open(target,'w:gz',compresslevel=3) as archive:
        for item in document['files']:
            if include_protected or item['role']=='code':
                archive.add(ROOT/item['path'],arcname=item['path'],recursive=False)
        if include_protected:
            archive.add(OUT/'migration_inventory.local.json',arcname='migration_inventory.local.json')
    print(json.dumps({'archive':target.name,'bytes':target.stat().st_size,'contains_protected':include_protected}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',action='store_true')
    parser.add_argument('--include-protected',action='store_true')
    args=parser.parse_args()
    package(args.include_protected) if args.package else inventory()
