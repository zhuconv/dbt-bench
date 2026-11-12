"""Replay a source-comment equivalent after an actual clean positive build."""
import argparse,hashlib,json,subprocess
from pathlib import Path
import duckdb
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def capture(database,relations):
    with duckdb.connect(database,read_only=True) as c:
        out={}
        for rel in relations:
            q=c.execute('SELECT * FROM '+rel);cols=[x[0] for x in q.description];rows=q.fetchall()
            bag=sorted(json.dumps([[type(v).__name__,str(v)] for v in row]) for row in rows)
            out[rel]={'columns':cols,'rows':len(rows),'typed_multiset_sha256':hashlib.sha256(json.dumps(bag).encode()).hexdigest()}
        return out
def main(spec,out):
    s=json.loads(spec.read_text());before=capture(s['database'],s['relations']);changed=[]
    for name in s['models']:
        p=Path(s['project_root'])/name;old=digest(p);p.write_text(p.read_text()+'\n-- phase4 legal comment-only presentation.\n');changed.append({'path':name,'before':old,'after':digest(p)})
    command=['dbt','run','--select',*map(lambda n:Path(n).stem,s['models']),'--target-path','phase4_equivalent_target_v1','--log-path','phase4_equivalent_logs_v1']
    r=subprocess.run(command,cwd=s['project_root'],text=True,capture_output=True);log=out.with_name('case-build.log');log.write_text(r.stdout+r.stderr)
    if r.returncode:raise ValueError('Equivalent did not build; no legality certification')
    after=capture(s['database'],s['relations']);out.write_text(json.dumps({'schema_version':1,'task_key':s['task_key'],'case_id':s['case_id'],
        'validity':'legal_equivalent' if before==after else 'invalid_mutation','tested_checker_invoked_to_define_validity':False,
        'before':before,'after':after,'changed_sources':changed,'actual_command':command,'returncode':r.returncode,'build_log_sha256':digest(log),
        'model_requests':0,'scope':'Comment-only SQL source change; fresh executed model outputs equal in all declared columns, types and multiplicities. Baseline semantic legitimacy is independently bound to phase4 raw-input reconstruction review.'},indent=2)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--spec',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();main(a.spec,a.output)
