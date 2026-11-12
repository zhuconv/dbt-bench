from pathlib import Path
import json,shutil
import duckdb
out=Path('/logs/verifier/product-inventory');out.mkdir(parents=True,exist_ok=True)
try:
 with duckdb.connect('/app/analytics_engineering.duckdb',read_only=True) as c:
  cursor=c.execute('SELECT * FROM main.obt_product_inventory');columns=[x[0] for x in cursor.description]
  result={'columns':columns,'types':[str(x[1]) for x in cursor.description],'rows':[dict(zip(columns,r)) for r in cursor.fetchall()]}
except Exception as error:result={'read_error':str(error)}
(out/'after-native.json').write_text(json.dumps(result,sort_keys=True,indent=2,default=str)+'\n')
for name in ['run_results.json','manifest.json']:
 p=Path('/app/target')/name
 if p.exists():shutil.copyfile(p,out/name)
for p in Path('/app/target/compiled').rglob('AUTO_obt_product_inventory_equality.sql'):
 destination=out/'compiled'/p.relative_to('/app/target/compiled');destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,destination)
