"""Actual source mutation with an input-only NULL-population proof gate."""
from pathlib import Path
from collections import defaultdict
from decimal import Decimal
import argparse,hashlib,json,subprocess
import duckdb

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def records(c,relation):
    q=c.execute('SELECT * FROM '+relation);names=[x[0].lower() for x in q.description]
    return [dict(zip(names,row)) for row in q.fetchall()]
def independent(c,observed):
    raw=records(c,'main.int_sales__orders_enriched');groups=defaultdict(list)
    for r in raw:
        if r['customer_id'] is not None and r['ordered_at'] is not None:groups[r['customer_id']].append(r)
    got={r['customer_id']:r for r in observed};errors=[]
    for cid in set(groups)^set(got):errors.append({'customer_id':cid,'reason':'population'})
    for cid in set(groups)&set(got):
        want=sum((Decimal(r['grand_total']) for r in groups[cid] if r['grand_total'] is not None),Decimal(0))
        if got[cid]['lifetime_revenue']!=want:errors.append({'customer_id':cid,'reason':'dated-only revenue','expected':str(want),'actual':str(got[cid]['lifetime_revenue'])})
    return {'errors':errors,'raw_rows':len(raw),'expected_customers':len(groups),'observed_customers':len(observed),
        'null_date_rows':sum(r['ordered_at'] is None for r in raw),'rule':'Patched instruction excludes NULL customer or timestamp before all metrics and before as-of; original-input Python set/sum, no gold or tested verifier.'}
def main(spec,out):
    s=json.loads(spec.read_text());model=Path(s['project_root'])/s['model_path'];original=model.read_text();original_hash=digest(model)
    with duckdb.connect(s['database'],read_only=True) as c:
        before=records(c,s['relation']);a=independent(c,before)
    if a['errors']:raise ValueError('Author baseline fails independent NULL population rule')
    if s['kind']=='equivalent':new=original+'\n-- phase4 equivalent source presentation only.\n'
    else:
        old='and ordered_at is not null'
        if original.count(old)!=1:raise ValueError('Mutation source context differs')
        new=original.replace(old,'and true')
    model.write_text(new);command=['dbt','run','--select',model.stem,'--target-path','phase4_case_target_v1','--log-path','phase4_case_logs_v1']
    r=subprocess.run(command,cwd=s['project_root'],text=True,capture_output=True);log=out.with_name('case-build.log');log.write_text(r.stdout+r.stderr)
    if r.returncode:raise ValueError('Case failed to build; not a semantic rejection')
    with duckdb.connect(s['database'],read_only=True) as c:
        after=records(c,s['relation']);b=independent(c,after)
    bag=lambda rows:sorted(json.dumps(r,sort_keys=True,default=str) for r in rows)
    valid='legal_equivalent' if s['kind']=='equivalent' and bag(before)==bag(after) else 'business_error' if s['kind']=='negative' and b['errors'] else 'invalid_mutation'
    out.write_text(json.dumps({'schema_version':1,'task_key':s['task_key'],'case_id':s['case_id'],'validity':valid,
        'tested_checker_invoked_to_define_validity':False,'before':a,'after':b,'source_before':original_hash,'source_after':digest(model),
        'actual_command':command,'returncode':r.returncode,'build_log_sha256':digest(log),'model_requests':0},indent=2,default=str)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--spec',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();main(a.spec,a.output)
