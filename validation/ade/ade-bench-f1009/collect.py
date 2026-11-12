from pathlib import Path
import json,shutil
import duckdb
out=Path('/logs/verifier/f1009-age');out.mkdir(parents=True,exist_ok=True)
with duckdb.connect('/app/f1.duckdb',read_only=True) as c:
 q=c.execute('SELECT * FROM main.analysis__drivers_current_age');cols=[x[0] for x in q.description]
 result={'columns':cols,'types':[str(x[1]) for x in q.description],'rows':[dict(zip(cols,r)) for r in q.fetchall()]}
 d,ts,tz=c.execute("SELECT CURRENT_DATE::VARCHAR,CURRENT_TIMESTAMP::VARCHAR,current_setting('TimeZone')").fetchone()
(out/'after-native.json').write_text(json.dumps(result,sort_keys=True,indent=2,default=str)+'\n')
(out/'clock-after-native.json').write_text(json.dumps({'date':d,'timestamp':ts,'timezone':tz},sort_keys=True,indent=2)+'\n')
for name in ['run_results.json','manifest.json']:
 p=Path('/app/target')/name
 if p.exists():shutil.copyfile(p,out/name)
for p in Path('/app/target/compiled').rglob('*.sql'):
 if 'age' in p.name:
  target=out/'compiled'/p.relative_to('/app/target/compiled');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
