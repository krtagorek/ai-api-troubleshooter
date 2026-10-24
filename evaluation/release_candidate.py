"""Local RC identity only: no deployment, provider transport or schema writes."""
import importlib.metadata
import json
from pathlib import Path
from evaluation.quality_pilot import sha

PATH=Path('reports/release-candidate-safety/code-dependency-hashes.json')


def dependencies():
    python={d.metadata['Name']:d.version for d in importlib.metadata.distributions() if d.metadata['Name']}
    package=json.loads(Path('frontend/package.json').read_text())
    frontend={}
    for name in sorted({**package['dependencies'],**package['devDependencies']}):
        frontend[name]=json.loads((Path('frontend/node_modules')/name/'package.json').read_text())['version']
    return {'python_installed_versions':dict(sorted(python.items())),
            'frontend_direct_installed_versions':frontend}


def snapshot():
    files=[]
    for folder in ('diagnostic_contracts','agents','backend','graph','models','mock_api','tools','registry','persistence','worker','evaluation','migrations','tests','frontend/src','frontend/e2e'):
        files.extend(p for p in Path(folder).rglob('*') if p.is_file() and p.suffix in ('.py','.ts','.tsx','.mjs','.json','.css','.svg','.html') and '__pycache__' not in p.parts)
    files.extend(Path('data').rglob('*.py'))
    files.extend(p for p in map(Path,('main.py','requirements.txt','uv.lock','pyproject.toml','alembic.ini','.env.example','docker-compose.yml','frontend/index.html','frontend/tsconfig.json','frontend/tsconfig.app.json','frontend/tsconfig.node.json','frontend/package.json','frontend/package-lock.json','frontend/openapi.json','frontend/vite.config.ts','frontend/playwright.config.ts')) if p.exists())
    return {'version':'local-release-candidate-1','files_sha256':{str(p):sha(p) for p in sorted(set(files))},'dependencies':dependencies()}


def verify():
    saved=json.loads(PATH.read_text())
    if snapshot()!=saved:
        raise ValueError('Release candidate code or installed dependencies changed')
    return saved


if __name__=='__main__':
    with PATH.open('x') as stream:json.dump(snapshot(),stream,indent=2)
    print('RC code/dependency identity recorded; no deployment or paid execution')
