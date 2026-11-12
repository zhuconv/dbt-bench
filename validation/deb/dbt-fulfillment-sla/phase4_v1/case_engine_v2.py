"""Actually build a NULL-name violation and prove it without any target checker."""
import argparse,json,hashlib,subprocess
from pathlib import Path
import duckdb

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rows(rel):
 with duckdb.connect('/app/database/retail.duckdb',read_only=True) as c:
  q=c.execute('SELECT * FROM '+rel);cols=[x[0].lower()for x in q.description];return [dict(zip(cols,r))for r in q.fetchall()]
def bag(rs):return sorted(json.dumps({k:[type(v).__name__,str(v)]for k,v in r.items()},sort_keys=True)for r in rs)
def main(spec,out):
 s=json.loads(spec.read_text());raw=rows('main.stg_wms__ship_methods');expected={r['shipping_method_id'].strip():r['shipping_method_code'].strip()for r in raw if r['shipping_method_name']is None};assert expected
 before=rows('main.fulfillment_sla');affected=[r for r in before if r['shipping_method_id']in expected];assert affected and all(r['shipping_method_name']==expected[r['shipping_method_id']]for r in affected)
 p=Path('/app/dbt_models_duckdb/models/intermediate/int_shipment_times.sql');old=digest(p);text=p.read_text();needle="coalesce(nullif(trim(shipping_method_name), ''), trim(shipping_method_code)) as shipping_method_name";assert text.count(needle)==1;p.write_text(text.replace(needle,"nullif(trim(shipping_method_name), '') as shipping_method_name"))
 cmd=['dbt','run','--select','+fulfillment_sla','+carrier_performance','+warehouse_performance','+fulfillment_summary','--target-path','phase4_name_case_target','--log-path','phase4_name_case_log'];r=subprocess.run(cmd,cwd='/app/dbt_models_duckdb',capture_output=True,text=True);log=out.with_name('case-build.log');log.write_text(r.stdout+r.stderr);assert r.returncode==0
 after=rows('main.fulfillment_sla');changed=[r for r in after if r['shipping_method_id']in expected];strip=lambda rs:[{k:v for k,v in row.items()if k!='shipping_method_name'}for row in rs]
 valid=len(changed)==len(affected)and all(r['shipping_method_name']is None for r in changed)and bag(strip(before))==bag(strip(after))
 out.write_text(json.dumps({'schema_version':1,'task_key':s['task_key'],'validity':'business_error'if valid else'invalid_mutation','tested_checker_invoked_to_define_validity':False,'independent_raw_rule':{'method_code_by_null_name':expected,'query':'SELECT * FROM main.stg_wms__ship_methods','rule':'Actual NULL input names must display their method code; no expected/gold table queried.'},'before':affected,'after':changed,'other_declared_values_unchanged':bag(strip(before))==bag(strip(after)),'changed_source':{'path':str(p),'before_sha256':old,'after_sha256':digest(p)},'actual_command':cmd,'dbt_returncode':r.returncode,'build_log_sha256':digest(log),'model_requests':0},indent=2,default=str)+'\n');assert valid
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--spec',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();main(a.spec,a.output)
