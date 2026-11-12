from pathlib import Path
import json,hashlib
r=Path('/opt/dbt-bench-initial/dbt_packages/dbt_utils')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'package_root':str(r),'package_files':{str(p.relative_to(r)):sha(p) for p in sorted(r.rglob('*')) if p.is_file()},'public_csv':{str(p):sha(p) for p in sorted(Path('/app/data').glob('*.csv'))},'consolidate_db_exists':Path('/app/consolidate.duckdb').exists(),'scope':'Read-only immutable image dependency and public input identities; no native setup certification.'}))
