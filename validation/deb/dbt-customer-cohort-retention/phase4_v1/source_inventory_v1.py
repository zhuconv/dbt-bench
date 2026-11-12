"""Read only project source/dependency byte identities from a fixed image."""
from pathlib import Path
import hashlib,json
roots=['/app/dbt_models_duckdb','/app/dbt_project','/app/dbt_consolidate']
result={}
for root in map(Path,roots):
 files=[]
 for directory in ('models','macros','seeds','dbt_packages'):
  base=root/directory
  files.extend(p for p in base.rglob('*') if p.is_file() and not p.is_symlink())
 files.extend(p for p in root.glob('*') if p.is_file() and p.suffix in {'.yml','.yaml'})
 result[str(root)]={str(p.relative_to(root)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':p.stat().st_size} for p in sorted(set(files))}
print(json.dumps({'schema_version':1,'project_files':result,'scope':'Read-only source/package inventory; no task execution or correctness certification.'},sort_keys=True))
