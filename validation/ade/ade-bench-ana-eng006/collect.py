from pathlib import Path
import json,shutil
import duckdb
out=Path('/logs/verifier/inventory-three-models');out.mkdir(parents=True,exist_ok=True)
rows={}
with duckdb.connect('/app/analytics_engineering.duckdb',read_only=True) as c:
 for name in ['dim_products','fact_inventory','obt_product_inventory']:
  try:
   q=c.execute('SELECT * FROM main."'+name+'"');cols=[x[0] for x in q.description]
   rows[name]={'columns':cols,'types':[str(x[1]) for x in q.description],'rows':[dict(zip(cols,r)) for r in q.fetchall()]}
  except Exception as e:rows[name]={'read_error':str(e)}
(out/'after-native.json').write_text(json.dumps(rows,sort_keys=True,indent=2,default=str)+'\n')
for name in ['run_results.json','manifest.json']:
 p=Path('/app/target')/name
 if p.exists():shutil.copyfile(p,out/name)
for p in Path('/app/target/compiled').rglob('*.sql'):
 if p.name.startswith('AUTO_') or p.name=='check_row_count.sql':
  target=out/'compiled'/p.relative_to('/app/target/compiled');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
