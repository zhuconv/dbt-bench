"""Run an explicitly pinned Spider SQL candidate; used only inside validation copies."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(directory):
    items = [(str(p.relative_to(directory)), digest_file(p))
             for p in sorted(directory.rglob('*')) if p.is_file()]
    return {'files': len(items), 'sha256': hashlib.sha256(json.dumps(items).encode()).hexdigest()}


def main(source=Path('/solution'), workspace=Path('/workspace'), logs=Path('/logs/agent')):
    candidate = json.loads((source / 'candidate.json').read_text())
    report = {'task_key': candidate['task_key'], 'mode': candidate['mode'],
              'started_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'candidate_manifest_sha256': digest_file(source / 'candidate.json'),
              'installed_files': [], 'commands': [], 'model_requests': 0,
              'gold_access': 'No gold/config/evaluator files are included in this solution payload.'}
    environment = os.environ.copy()
    environment.pop('LD_PRELOAD', None)
    environment.pop('FAKECLOCK_OFFSET', None)
    environment.update(DBT_SEND_ANONYMOUS_USAGE_STATS='false', DO_NOT_TRACK='1')
    report['packages_before'] = tree_digest(workspace / 'dbt_packages')
    try:
        for item in candidate['files']:
            relative = Path(item['destination'])
            assert not relative.is_absolute() and '..' not in relative.parts
            payload, target = source / item['payload'], workspace / relative
            assert digest_file(payload) == item['sha256']
            previous = digest_file(target) if target.exists() else None
            # Source files and the reviewed project YAML are the only replacements.
            assert relative.parts[0] == 'models' or relative.as_posix() == 'dbt_project.yml'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(payload, target)
            report['installed_files'].append({**item, 'previous_sha256': previous,
                                              'actual_sha256': digest_file(target)})
        if candidate['mode'] == 'fixed-clock':
            shim = source / 'fakeclock.so'
            assert digest_file(shim) == candidate['clock_shim']['sha256']
            target_epoch = int(datetime.datetime(2024, 9, 8, 12, tzinfo=datetime.timezone.utc).timestamp())
            offset = int(time.time()) - target_epoch
            environment.update(LD_PRELOAD=str(shim), FAKECLOCK_OFFSET=str(offset))
            report['clock_diagnostic'] = {'target_date': '2024-09-08', 'offset_seconds': offset,
                'shim_sha256': digest_file(shim), 'scope': 'dbt and pre-build clock probe only; verifier clock unchanged'}
        probe = subprocess.run(['python3', '-c',
            'import datetime,json,duckdb;print(json.dumps({"python_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"duckdb_now":str(duckdb.connect().execute("select current_timestamp").fetchone()[0]),"duckdb_version":duckdb.__version__}))'],
            cwd=workspace, env=environment, capture_output=True, text=True)
        report['clock_probe'] = {'returncode': probe.returncode, 'stdout': probe.stdout, 'stderr': probe.stderr}
        assert probe.returncode == 0
        if candidate['mode'] == 'fixed-clock':
            actual = json.loads(probe.stdout)
            assert actual['python_utc'].startswith('2024-09-08') and actual['duckdb_now'].startswith('2024-09-08')
        commands = ([['dbt', 'seed', '--profiles-dir', '/workspace']] if candidate['seed_first'] else [])
        commands.append(['dbt', 'run', '--profiles-dir', '/workspace'])
        for index, command in enumerate(commands):
            path = logs / f'witness-dbt-{index}.log'
            with path.open('w') as stream:
                result = subprocess.run(command, cwd=workspace, env=environment,
                                        stdout=stream, stderr=subprocess.STDOUT)
            report['commands'].append({'argv': command, 'returncode': result.returncode,
                                       'log': str(path), 'log_sha256': digest_file(path)})
            if result.returncode:
                return result.returncode
        return 0
    except Exception as error:
        report['exception'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        report['packages_after'] = tree_digest(workspace / 'dbt_packages')
        report['packages_unchanged'] = report['packages_before'] == report['packages_after']
        report['finished_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (logs / 'witness-build.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
