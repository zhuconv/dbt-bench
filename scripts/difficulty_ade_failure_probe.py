"""Independently recheck fresh failed compiled SQL with DuckDB's typed API.

Only parser/binder/catalog failures on one SELECT qualify here. Filesystem,
resource, connection, extension and other failures stay unknown. No string
matching of error messages assigns a failure class.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path


def sha(path):
    with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def probe(project,database,target,output):
    project,database,target,output=map(Path,(project,database,target,output))
    if output.exists():raise ValueError('Preserve existing SQL-failure probe')
    output.mkdir(parents=True)
    result={'schema_version':1,'producer':'readonly_typed_duckdb_failure_probe',
        'started_at_utc':datetime.now(timezone.utc).isoformat(),'proofs':[],
        'database':str(database),'model_requests':0,'training_prohibited':True}
    paths=[database,Path(str(database)+'.wal')]
    before={str(p):sha(p)if p.is_file()else None for p in paths}
    try:
        import duckdb
        manifest_path=target/'manifest.json';runs_path=target/'run_results.json'
        manifest=json.loads(manifest_path.read_text());runs=json.loads(runs_path.read_text())
        if manifest['metadata']['invocation_id']!=runs['metadata']['invocation_id']:
            raise ValueError('Fresh manifest and run results disagree')
        result.update(duckdb_version=duckdb.__version__,manifest_sha256=sha(manifest_path),
            run_results_sha256=sha(runs_path),invocation_id=manifest['metadata']['invocation_id'])
        connection=duckdb.connect(str(database),read_only=True)
        for row in runs['results']:
            if row['status']!='error':continue
            node=manifest['nodes'][row['unique_id']]
            item={'node_id':row['unique_id'],'dbt_status':row['status'],'proved_failure':False,
                  'original_file_path':node['original_file_path']}
            if node.get('resource_type')!='model' or node.get('language','sql')!='sql':
                item['reason']='Unsupported failed node type';result['proofs'].append(item);continue
            source=project/node['original_file_path']
            if not source.resolve().is_relative_to(project.resolve()):raise ValueError('Model source escaped project')
            item['source_sha256']=sha(source)
            if node['checksum'].get('name')!='sha256' or node['checksum']['checksum']!=item['source_sha256']:
                raise ValueError('Compiled node is not bound to current source bytes')
            sql=node.get('compiled_code')
            if not isinstance(sql,str)or not sql.strip():
                item['reason']='No captured compiled SQL';result['proofs'].append(item);continue
            item['compiled_sql_sha256']=hashlib.sha256(sql.encode()).hexdigest()
            try:
                statements=connection.extract_statements(sql)
                if len(statements)!=1 or str(statements[0].type)!='StatementType.SELECT':
                    item['reason']='Only one SELECT can be independently explained read-only'
                else:
                    try:
                        connection.execute('EXPLAIN '+sql).fetchall()
                        item['reason']='Compiled SELECT parses/binds; runtime error not independently established'
                    except (duckdb.BinderException,duckdb.CatalogException)as exc:
                        item.update(proved_failure=True,typed_exception=type(exc).__name__,
                                    independent_method='readonly_explain',detail=str(exc))
            except duckdb.ParserException as exc:
                item.update(proved_failure=True,typed_exception=type(exc).__name__,
                            independent_method='duckdb_extract_statements',detail=str(exc))
            result['proofs'].append(item)
        connection.close()
    except Exception as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)}
    after={str(p):sha(p)if p.is_file()else None for p in paths}
    result.update(database_hashes_before=before,database_hashes_after=after,input_unchanged=before==after,
                  finished_at_utc=datetime.now(timezone.utc).isoformat())
    (output/'sql-failure-probe.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',type=Path,required=True);p.add_argument('--database',type=Path,required=True)
    p.add_argument('--target',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(probe(a.project,a.database,a.target,a.output))
