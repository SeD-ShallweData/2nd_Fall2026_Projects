"""Check actual public files and, if staged, their Git blobs against the manifest."""
from pathlib import Path
import ast
import hashlib
import json
import re
import subprocess
import sys

TEAM = Path(__file__).resolve().parents[1]
REPO = TEAM.parent
MANIFEST = TEAM / 'PUBLIC_FILE_MANIFEST.json'


def check_bytes(path, raw):
    text = raw.decode('utf-8-sig')
    if path.endswith('.py'):
        ast.parse(text, filename=path)
    if path.endswith(('.json', '.ipynb')):
        value = json.loads(text)
        if path.endswith('.ipynb'):
            assert all(not c.get('outputs') and not c.get('attachments') and c.get('execution_count') is None for c in value['cells']), path
        text += json.dumps(value, ensure_ascii=False).replace('\\\\', '\\')
    personal = r'[A-Z]:[\\/]+Us' + r'ers[\\/]+' + r'|/Us' + r'ers/[^/\s]+|/ho' + r'me/[^/\s]+'
    assert not re.search(personal, text), 'personal path: ' + path
    assert not re.search(r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,}', text), 'token pattern: ' + path


def main():
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    expected = {f['path']: f for f in manifest['files']}
    self_path = MANIFEST.relative_to(REPO).as_posix()
    expected[self_path] = {'sha256': hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), 'bytes': MANIFEST.stat().st_size}
    initial = '--initial-package' in sys.argv
    if not initial:
        expected = {p: f for p, f in expected.items() if p.startswith(TEAM.name + '/') or p == '.github/workflows/team-bbigeudeok.yml'}
    actual = set()
    for p in REPO.rglob('*'):
        rel = p.relative_to(REPO)
        if any(x in rel.parts for x in ('.git', '__pycache__', '.venv', 'outputs')):
            continue
        if p.is_file():
            assert not p.is_symlink(), 'symlink rejected'
            path = rel.as_posix()
            if initial or path.startswith(TEAM.name + '/') or path == '.github/workflows/team-bbigeudeok.yml':
                actual.add(path)
    assert actual == set(expected), 'unexpected or missing public files: ' + repr(sorted(actual ^ set(expected)))
    for path, item in expected.items():
        raw = (REPO / path).read_bytes()
        assert len(raw) == item['bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256'], 'content differs: ' + path
        check_bytes(path, raw)
    # This initial package is a new repository. Existing multi-team repositories
    # must scope this check to the manifest and team additions before integration.
    git = subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, encoding='utf-8')
    staged_count = None
    if git.returncode == 0 and Path(git.stdout.strip()).resolve() == REPO.resolve():
        index = subprocess.run(['git', '-C', str(REPO), 'ls-files', '-z'], capture_output=True, check=True).stdout
        staged = [p.decode('utf-8') for p in index.split(b'\0') if p]
        if not initial:
            staged = [p for p in staged if p.startswith(TEAM.name + '/') or p == '.github/workflows/team-bbigeudeok.yml']
        if staged:
            assert set(staged) == set(expected), 'index differs from approved files'
            for path in staged:
                raw = subprocess.run(['git', '-C', str(REPO), 'show', ':' + path], capture_output=True, check=True).stdout
                assert hashlib.sha256(raw).hexdigest() == expected[path]['sha256'], 'staged content differs: ' + path
                check_bytes(path, raw)
            staged_count = len(staged)
    print(json.dumps({'passed': True, 'files': len(expected), 'bytes': sum(v['bytes'] for v in expected.values()), 'staged_files_checked': staged_count}))


if __name__ == '__main__':
    main()
