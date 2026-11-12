"""F1009 SQL-clock evidence from the actual dbt DuckDB connection manager.

No project is parsed or run. Credentials are read from unchanged source; only
the observer's underlying connection is forced read-only. Unsupported dynamic
profiles/initializers yield unknown instead of evaluating candidate code.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path

SQL="SELECT CURRENT_DATE, CURRENT_TIMESTAMP, current_setting('TimeZone')"


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def selected_credentials(project_root,database):
    import yaml
    project_root=Path(project_root)
    project_path=project_root/'dbt_project.yml'
    profiles_path=Path(os.environ.get('DBT_PROFILES_DIR',str(project_root)))/'profiles.yml'
    documents=[]
    for path in (project_path,profiles_path):
        text=path.read_text()
        # No candidate Jinja, executable config or macros are evaluated here.
        documents.append(yaml.safe_load(text))
    project,profiles=documents
    name=os.environ.get('DBT_PROFILE') or project['profile']
    profile=profiles[name];target=os.environ.get('DBT_TARGET') or profile.get('target','default')
    selected=profile['outputs'][target]
    if any(marker in json.dumps([name,target,selected])for marker in ('{{','{%','!!python')):
        raise ValueError('Dynamic selected profile/session configuration is outside clock observer scope')
    allowed={'type','path','schema','database','threads','settings'}
    if set(selected)-allowed or selected['type']!='duckdb':
        raise ValueError('Unreviewed dbt connection initializer/configuration')
    path=Path(selected['path']);path=path if path.is_absolute() else project_root/path
    if path.resolve()!=Path(database).resolve():raise ValueError('Selected dbt database differs from declared F1009 database')
    settings=selected.get('settings') or {}
    if any(k.casefold() not in ('timezone',) or not isinstance(v,str) for k,v in settings.items()):
        raise ValueError('Clock observer accepts only explicit static TimeZone settings')
    return {'profile_name':name,'target':target,'selected':selected,'resolved_database':str(path.resolve()),
            'source_files':[{'path':str(p),'sha256':sha(p)}for p in (project_path,profiles_path)],
            'environment':{k:os.environ.get(k)for k in ('DBT_PROFILE','DBT_TARGET','DBT_PROFILES_DIR','TZ')}}


def collect(project_root,database,output,capture_root=None):
    output=Path(output)
    if output.exists():raise ValueError('Preserve prior session observation')
    database=Path(database);paths=[database,Path(str(database)+'.wal')]
    before={str(p):sha(p)if p.is_file()else None for p in paths}
    result={'schema_version':1,'task_key':'ade__ade-bench-f1009','producer':'actual_dbt_connection_manager_readonly',
        'database':str(database),'sql':SQL,'started_at_utc':datetime.now(timezone.utc).isoformat(),
        'source_hashes_before':before,'model_requests':0,'training_prohibited':True,
        'scope':'Real installed dbt adapter session with delivered static credentials; observer connection read_only=True, no project compilation, no model/source/profile modification.'}
    try:
        if capture_root is not None:
            capture_path=Path(capture_root)/'capture.json';capture=json.loads(capture_path.read_text())
            entry=next(r for r in capture['databases']if r['path']==str(database))
            if capture['task_key']!=result['task_key']or not entry['unchanged']or before[str(database)]!=entry['sha256']:
                raise ValueError('SQL session probe differs from preserved model submission')
            result['capture_sha256']=sha(capture_path)
        context=selected_credentials(project_root,database)
        import duckdb
        from dbt.adapters.duckdb.connections import DuckDBConnectionManager
        from dbt.adapters.duckdb.credentials import DuckDBCredentials
        from dbt.adapters.contracts.connection import Connection
        import dbt.adapters.duckdb.environments as environments
        import dbt.adapters.duckdb.environments.local as local
        source=duckdb.connect;calls=[]
        def readonly_connect(*args,**kwargs):
            calls.append({'database':args[0]if args else kwargs.get('database'),
                          'requested_read_only':kwargs.get('read_only'),
                          'actual_read_only':True,'config':kwargs.get('config',{})})
            return source(*args,**(kwargs|{'read_only':True}))
        # This isolated observer process changes only its connection access
        # mode. The same dbt manager subsequently initializes session settings.
        duckdb.connect=readonly_connect
        selected=context['selected'];kw={k:v for k,v in selected.items()if k not in ('type','threads')}
        kw['path']=context['resolved_database']
        creds=DuckDBCredentials(**kw)
        connection=DuckDBConnectionManager.open(Connection(type='duckdb',name='clock-evidence',credentials=creds))
        cursor=connection.handle.cursor();cursor.execute(SQL);day,instant,zone=cursor.fetchone()
        cursor.close();DuckDBConnectionManager.close(connection);duckdb.connect=source
        modules=[Path(inspect.getfile(v))for v in (DuckDBConnectionManager,DuckDBCredentials,environments,local)]
        result.update(error=None,current_date=day.isoformat(),current_timestamp=instant.isoformat(),timezone=zone,
            duckdb_version=duckdb.__version__,dbt_core_version=importlib.metadata.version('dbt-core'),
            dbt_duckdb_version=importlib.metadata.version('dbt-duckdb'),session=context,
            connection_open_calls=calls,adapter_sources=[{'path':str(p),'sha256':sha(p)}for p in modules],
            observer_only_override={'read_only':True,'session_settings_modified':False})
    except Exception as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)}
    after={str(p):sha(p)if p.is_file()else None for p in paths}
    result.update(source_hashes_after=after,input_unchanged=before==after,finished_at_utc=datetime.now(timezone.utc).isoformat())
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project-root',type=Path,required=True);p.add_argument('--database',type=Path,required=True)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--output',type=Path);g.add_argument('--output-dir',type=Path)
    p.add_argument('--capture-root',type=Path)
    a=p.parse_args();print(collect(a.project_root,a.database,a.output or a.output_dir/'session-clock.json',a.capture_root))
