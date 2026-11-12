"""Read-only exports immediately before native grading and after native dbt setup.

This task's original setup builds the submitted model. The audit consumes that
build without invoking dbt or changing its inputs. This collector is not an
agent-context probe and does not certify all possible project dependencies.
"""
from pathlib import Path
import hashlib,json,os,sys
from datetime import datetime,timezone


def main(phase, setup_exit=None):
    import duckdb
    out=Path('/logs/verifier/customer-audit')/phase
    out.mkdir(parents=True,exist_ok=False)
    report={'phase':phase,'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'read_only_database':True,'dbt_invoked':False,'setup_exit':setup_exit,
        'files':{},'project_files':{},'errors':[],
        'context':{'uid':os.getuid(),'cwd':os.getcwd(),'python':sys.executable,
                   'python_version':sys.version,'duckdb_version':duckdb.__version__}}
    def save(name,payload,metadata=None):
        data=payload if isinstance(payload,bytes) else (json.dumps(payload,sort_keys=True,indent=2,default=str)+'\n').encode()
        target=out/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        report['files'][name]={'sha256':hashlib.sha256(data).hexdigest(),**(metadata or {})}
    project=Path('/app')
    try:
        sources=set()
        for dirname in ['models','macros','analyses','snapshots']:
            sources.update(p for p in (project/dirname).rglob('*') if p.is_file())
        for name in ['dbt_project.yml','profiles.yml','packages.yml','package-lock.yml']:
            if (project/name).is_file():sources.add(project/name)
        for p in sorted(sources):
            if p.is_symlink():raise ValueError('Symlink needs an explicit transfer review: '+str(p))
            relative=str(p.relative_to(project));name='project/'+relative
            save(name,p.read_bytes());report['project_files'][relative]=report['files'][name]['sha256']
        for filename in ['manifest.json','run_results.json']:
            p=project/'target'/filename
            if p.is_file():save(filename,p.read_bytes())
            else:report['files'][filename]={'absent':True}
        with duckdb.connect('/app/analytics_engineering.duckdb',read_only=True) as conn:
            for table,name in [('customer','input.json'),('dim_customer','output.json')]:
                exists=conn.execute('SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?',['main',table]).fetchone()[0]
                if not exists:
                    report['files'][name]={'absent':True};continue
                q=conn.execute('SELECT * FROM main.'+table+(' ORDER BY id' if table=='customer' else ''))
                columns=[c[0] for c in q.description]
                save(name,{'columns':columns,'rows':[dict(zip(columns,r)) for r in q.fetchall()]},
                     {'types':{c[0]:str(c[1]) for c in q.description}})
    except Exception as exc:
        report['errors'].append({'type':type(exc).__name__,'message':str(exc)})
    (out/'snapshot.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'phase':phase,'collector_errors':report['errors']}))


if __name__=='__main__':main(sys.argv[1],int(sys.argv[2]) if len(sys.argv)>2 else None)
