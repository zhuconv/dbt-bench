"""Parse bounded captured dbt SQL in the exact locked DuckDB task image."""
from pathlib import Path
import json,hashlib,argparse
import duckdb
p=argparse.ArgumentParser();p.add_argument('--plan');p.add_argument('--output');a=p.parse_args();plan=json.loads(Path(a.plan).read_text());records=[]
for item in plan['manifests']:
 path=Path(item['path']);assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256'];m=json.loads(path.read_text());nodes=[]
 for uid,n in m['nodes'].items():
  if n.get('resource_type')!='model' or n.get('name')not in plan['models']:continue
  sql=n['compiled_code'];ast=json.loads(duckdb.sql('select json_serialize_sql(?)',params=[sql]).fetchone()[0]);assert not ast.get('error'),ast
  nodes.append({'unique_id':uid,'name':n['name'],'raw_path':n['original_file_path'],'compiled_sha256':hashlib.sha256(sql.encode()).hexdigest(),'ast':ast})
 assert len(nodes)==len(plan['models']);records.append({'manifest':item,'nodes':nodes})
Path(a.output).write_text(json.dumps({'records':records,'duckdb_version':duckdb.__version__,'model_requests':0,'scope':'Parser only; captured SQL is not executed and no task database is opened.'},indent=2)+'\n');print('Parsed',len(records),'manifests,',len(records)*len(plan['models']),'models')
