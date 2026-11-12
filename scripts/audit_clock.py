"""Capture SQL clock facts in a read-only connection to a declared database.

Task review must separately verify that its dbt profile/session uses matching
timezone settings. Host wall-clock date is not substituted for SQL CURRENT_DATE.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


def file_state(database):
    result = {}
    for path in (Path(database), Path(database + '.wal')):
        if path.is_file():
            with path.open('rb') as f: result[str(path)] = hashlib.file_digest(f, 'sha256').hexdigest()
        else: result[str(path)] = None
    return result


def capture(database):
    result = {'database': database, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'python_executable': sys.executable, 'process_TZ': os.environ.get('TZ'),
              'sql': "SELECT CURRENT_DATE, CURRENT_TIMESTAMP, current_setting('TimeZone')",
              'scope': 'Read-only SQL connection; dbt profile/session timezone equivalence needs separate review'}
    try:
        import duckdb
        before = file_state(database)
        with duckdb.connect(database, read_only=True) as con:
            day, timestamp, zone = con.execute(result['sql']).fetchone()
        after = file_state(database)
        result.update(duckdb_version=duckdb.__version__, current_date=day.isoformat(),
                      current_timestamp=timestamp.isoformat(), timezone=zone,
                      database_hashes_before=before, database_hashes_after=after,
                      input_unchanged=before == after)
        if before != after: raise ValueError('Database changed during clock capture')
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    result['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--database', required=True); p.add_argument('--output', required=True)
    args = p.parse_args(); output = Path(args.output); output.mkdir(parents=True, exist_ok=False)
    result = capture(args.database)
    (output / 'clock.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'date': result.get('current_date'), 'timezone': result.get('timezone'), 'error': result.get('error')}))
