from pathlib import Path
import json,shutil
import duckdb
out=Path('/logs/verifier/f1002-schema');out.mkdir(parents=True,exist_ok=True)
actual={}
with duckdb.connect('/app/f1.duckdb',read_only=True) as c:
 for name in ['finishes_by_driver','most_podiums','most_pole_positions','most_fastest_laps']:
  q=c.execute('SELECT * FROM "'+name+'"');cols=[x[0] for x in q.description]
  actual[name]={'columns':cols,'types':[str(x[1]) for x in q.description],'rows':[dict(zip(cols,row)) for row in q.fetchall()]}
(out/'after-native.json').write_text(json.dumps(actual,sort_keys=True,indent=2,default=str)+'\n')
for name in ['run_results.json','manifest.json']:
 p=Path('/app/target')/name
 if p.exists():shutil.copyfile(p,out/name)
for p in Path('/app/target/compiled').rglob('AUTO_most_podiums_equality.sql'):
 target=out/'compiled'/p.relative_to('/app/target/compiled');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
