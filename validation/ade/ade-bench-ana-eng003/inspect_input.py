"""Inspect the named customer source and pinned comparator before task solving."""
from collections import Counter,defaultdict
from pathlib import Path
import hashlib,json
import duckdb

out=Path('/logs/agent/customer-control-input');out.mkdir(exist_ok=False)
with duckdb.connect('/app/analytics_engineering.duckdb',read_only=True) as conn:
    tables=conn.execute("SELECT table_schema,table_name,table_type FROM information_schema.tables ORDER BY 1,2").fetchall()
    inputs={}
    for name in ['customer','stg_customer']:
        q=conn.execute('SELECT * FROM main.'+name+' ORDER BY id')
        columns=[c[0] for c in q.description]
        inputs[name]={'columns':columns,'rows':[dict(zip(columns,r)) for r in q.fetchall()]}
(out/'inputs.json').write_text(json.dumps({'tables':tables,'inputs':inputs},default=str,sort_keys=True,indent=2)+'\n')
files=['dbt_project.yml','profiles.yml','models/staging/stg_customer.sql','models/staging/source.yml',
       'dbt_packages/dbt_utils/macros/generic_tests/equality.sql']
for name in files:
    source=Path('/app')/name;dest=out/'public-project'/name;dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_bytes(source.read_bytes())
groups=defaultdict(list)
for r in inputs['customer']['rows']:groups[r['id']].append(r)
summary={'raw_rows':len(inputs['customer']['rows']),'staging_rows':len(inputs['stg_customer']['rows']),
    'unique_ids':len(groups),'null_ids':len(groups.get(None,[])),
    'repeated_ids':{str(k):len(v) for k,v in groups.items() if len(v)>1},
    'conflicting_same_id_rows':{str(k):len({json.dumps(r,sort_keys=True,default=str) for r in v}) for k,v in groups.items() if len({json.dumps(r,sort_keys=True,default=str) for r in v})>1},
    'declared_output_initially_exists':any(t[0]=='main' and t[1]=='dim_customer' for t in tables),
    'scope':'Pristine public source and comparator inspection, no solution or test upload'}
(out/'summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
print(json.dumps(summary))
