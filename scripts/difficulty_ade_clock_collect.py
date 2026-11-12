"""Optional read-only SQL clock sidecar after native submission collection.

This records a fact about the original task environment. It does not claim
that a separate dbt connection used this session or certify the date relation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

SQL = "SELECT CURRENT_DATE, CURRENT_TIMESTAMP, current_setting('TimeZone')"


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collect(database, capture_root, output):
    database, capture_root, output = Path(database), Path(capture_root), Path(output)
    if output.exists():
        raise ValueError('Preserve an existing SQL clock sidecar')
    capture_path = capture_root/'capture.json'
    capture = json.loads(capture_path.read_text())
    entry = next(x for x in capture['databases'] if x['path']==str(database))
    result = {'schema_version':1, 'task_key':capture['task_key'], 'database':str(database),
        'sql':SQL, 'started_at_utc':datetime.now(timezone.utc).isoformat(),
        'capture_sha256':sha(capture_path), 'model_requests':0, 'training_prohibited':True,
        'scope':'Original pre-verifier SQL clock only. Separate dbt session timezone is not inferred.',
        'dbt_session_timezone_matches':None}
    paths=[database, Path(str(database)+'.wal')]
    before={str(p):sha(p) if p.is_file() else None for p in paths}
    result['source_hashes_before']=before
    try:
        if not capture['complete'] or not entry['unchanged'] or before[str(database)]!=entry['sha256']:
            raise ValueError('Clock database differs from the preserved actual submission')
        import duckdb
        connection=duckdb.connect(str(database),read_only=True)
        day, instant, zone=connection.execute(SQL).fetchone()
        connection.close()
        result.update(current_date=day.isoformat(),current_timestamp=instant.isoformat(),
                      timezone=zone,duckdb_version=duckdb.__version__,error=None)
    except Exception as exc:
        result.update(error={'type':type(exc).__name__,'message':str(exc)})
    after={str(p):sha(p) if p.is_file() else None for p in paths}
    result.update(source_hashes_after=after,input_unchanged=before==after,
                  finished_at_utc=datetime.now(timezone.utc).isoformat())
    output.write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True,type=Path)
    p.add_argument('--capture-root',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(collect(a.database,a.capture_root,a.output))
