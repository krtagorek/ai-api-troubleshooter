"""Read-only public-file inventory. Findings never include matched secret values.

This is a bounded local scanner, not proof that Git history or all secret formats
are clean. Do not export the directory wholesale or treat .gitignore as erasure.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {'.git', '.codex', '.agents', '.aws', 'node_modules', '__pycache__',
                 '.pytest_cache', '.mypy_cache', '.ruff_cache', '.cache', 'htmlcov',
                 'dist', 'coverage', 'test-results', 'playwright-report'}
RULES = {
    'provider_key_candidate': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b'),
    'github_token_candidate': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    'aws_access_key_candidate': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'complete_private_key_candidate': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[\s\S]{40,}?-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    'machine_home_path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
}


def public_files(root=ROOT):
    for path in sorted(root.rglob('*')):
        parts = path.relative_to(root).parts
        if not path.is_file() or path.is_symlink():
            continue
        if any(p in EXCLUDED_DIRS or p.startswith('.venv') or p == 'venv' for p in parts):
            continue
        if parts[0] == 'reports' and path.name not in ('__init__.py', '.gitkeep'):
            continue
        if path.name.startswith('.env') and path.name != '.env.example':
            continue
        if path.suffix in ('.pyc', '.pyo', '.tsbuildinfo', '.dump', '.backup', '.pem', '.key', '.p12', '.pfx', '.har'):
            continue
        if path.name in ('.DS_Store', '.coverage') or path.name.endswith('.sql.gz'):
            continue
        yield path


def audit(root=ROOT):
    files, findings = [], []
    for path in public_files(root):
        raw = path.read_bytes()
        name = path.relative_to(root).as_posix()
        files.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        if len(raw) > 10 * 1024 * 1024:
            findings.append({'path': name, 'rule': 'file_over_10_MiB'})
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            findings.append({'path': name, 'rule': 'binary_requires_manual_review'})
            continue
        for rule, pattern in RULES.items():
            for match in pattern.finditer(text):
                # Invalid test-only markers are not key material. Real PEM blobs
                # are still candidates and require an explicit human response.
                if rule == 'complete_private_key_candidate' and 'private-material' in match.group():
                    continue
                findings.append({'path': name, 'line': text.count('\n', 0, match.start()) + 1, 'rule': rule})
    git = subprocess.run(['git', '-C', str(root), 'rev-parse', '--is-inside-work-tree'],
                         capture_output=True, text=True)
    return {'scope': 'Explicit public-file candidate inventory; private reports/env excluded',
            'git_metadata_present': git.returncode == 0,
            'git_history_audited': False,
            'file_count': len(files), 'files': files, 'findings': findings,
            'limitations': ['Regex coverage is bounded; manual review and a maintained secret scanner are still required.',
                           'Excluded files remain on disk; no deletion, rewrite, commit, or push is performed.',
                           'Git tracked/index/history audit must be performed on the actual repository before publication.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, indent=2))
    raise SystemExit(1 if result['findings'] else 0)
