from pathlib import Path
import json,shutil
import duckdb
out=Path('/logs/verifier/f1010-pair');out.mkdir(parents=True,exist_ok=True)
with duckdb.connect('/app/f1.duckdb',read_only=True) as c:
 q=c.execute('SELECT * FROM main.analysis__lap_times');cols=[x[0] for x in q.description]
 rows=[dict(zip(cols,r)) for r in q.fetchall()]
 physical={r[0]:r[1] for r in c.execute('DESCRIBE SELECT * FROM main.analysis__lap_times').fetchall()}
 result={'columns':cols,'types':[physical[col] for col in cols],'rows':rows}
(out/'after-native.json').write_text(json.dumps(result,sort_keys=True,indent=2,default=str)+'\n')
for name in ['run_results.json','manifest.json']:
 p=Path('/app/target')/name
 if p.exists():shutil.copyfile(p,out/name)
for p in Path('/app/target/compiled').rglob('*.sql'):
 if 'analysis__lap_times' in p.name:
  target=out/'compiled'/p.relative_to('/app/target/compiled');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
