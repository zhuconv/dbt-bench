#!/usr/bin/env python3
"""Check initialization and persistence through the actual Harbor exec transport."""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import harbor_process_env
from dbt_bench.io import evidence_ref, write_json


async def main(args):
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths

    args.output.mkdir(parents=True, exist_ok=False)
    os.environ['PATH'] = harbor_process_env('apptainer')['PATH']
    task = Task(task_dir=args.task)
    paths = TrialPaths(trial_dir=args.output / 'environment')
    paths.mkdir()
    logger = logging.getLogger('initial-tree')
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(args.output / 'harbor.log')
    logger.addHandler(handler)
    env = SingularityEnvironment(environment_dir=task.paths.environment_dir,
        environment_name='initial-tree', session_id=args.output.name, trial_paths=paths,
        task_env_config=task.config.environment.model_copy(update={'docker_image': str(args.image.resolve())}),
        persistent_env={'DB_TYPE': 'duckdb'}, logger=logger,
        singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
    result = {'image': evidence_ref(args.image, ROOT), 'script': evidence_ref(__file__, ROOT),
              'scope': 'Clean native Harbor environment; not an actual Qwen tool call or full backend equivalence proof',
              'checks': [], 'passed': False}
    try:
        await asyncio.wait_for(env.start(force_build=False), 300)
        code = '''import hashlib,json,stat
from pathlib import Path
def manifest(p):
    return [(str(f.relative_to(p)),stat.S_IMODE(f.stat().st_mode),hashlib.sha256(f.read_bytes()).hexdigest()) for f in sorted(p.rglob('*')) if f.is_file()]
source=Path(SOURCE); destination=Path(DESTINATION)
a,b=manifest(source),manifest(destination)
assert a and a==b, (len(a),len(b))
victim=destination/a[0][0]
victim.unlink()
print(json.dumps({'initial_tree_equal': True,'files':len(a),'removed_relative_path':a[0][0],'manifest_sha256':hashlib.sha256(json.dumps(a).encode()).hexdigest()}))
'''.replace('SOURCE', repr(args.source)).replace('DESTINATION', repr(args.destination))
        first = await env.exec('python3 -c ' + shlex.quote(code), timeout_sec=90)
        result['checks'].append(first.model_dump(mode='json'))
        assert first.return_code == 0, first.stderr
        initial = json.loads(first.stdout)
        victim = str(Path(args.destination) / initial['removed_relative_path'])
        second = await env.exec('test ! -e ' + shlex.quote(victim) + " && printf 'DELETION_PRESERVED\\n'", user='root', timeout_sec=30)
        result['checks'].append(second.model_dump(mode='json'))
        assert second.return_code == 0, second.stderr
        result.update(passed=True, initial=initial, subsequent_root_exec_preserves_deletion=True)
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        try:
            await env.stop(delete=True)
        except Exception as exc:
            result.update(passed=False, cleanup_error=str(exc))
        handler.close()
        logger.removeHandler(handler)
        result['log'] = evidence_ref(args.output / 'harbor.log', ROOT)
        write_json(args.output / 'result.json', result)
    print(json.dumps({'passed': result['passed'], 'output': str(args.output / 'result.json')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
