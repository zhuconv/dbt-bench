#!/usr/bin/env python3
"""Exercise real Harbor/Apptainer teardown without model calls or task scoring.

An optional candidate source runs in isolation before a runtime patch is locked.
Check process groups and closed HTTP ports, including launcher death, alongside
an independent live container. All environments are disposable overlays.
"""
import argparse
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import signal
import shutil
import socket
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import harbor_process_env
from dbt_bench.io import evidence_ref, write_json


def group_members(pgid):
    found = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            stat = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(stat[2]) == pgid:
                found.append({'pid': int(path.name), 'state': stat[0], 'session': int(stat[3])})
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.5):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


async def main(args):
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths

    source = Path(sys.modules[SingularityEnvironment.__module__].__file__)
    if args.candidate_source:
        source = args.candidate_source.resolve()
        spec = importlib.util.spec_from_file_location('harbor_lifecycle_candidate', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        SingularityEnvironment = module.SingularityEnvironment
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(__file__), out / 'check_harbor_lifecycle.py')
    shutil.copyfile(source, out / 'adapter-source.py')
    logger = logging.getLogger('lifecycle-regression')
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(out / 'runtime.log')
    logger.addHandler(handler)
    os.environ['PATH'] = harbor_process_env('apptainer')['PATH']
    task = Task(task_dir=args.task)
    environments = []
    result = {'scope': 'Native Harbor process lifecycle integration, no model or benchmark scoring',
              'script': evidence_ref(out / 'check_harbor_lifecycle.py', ROOT),
              'source': evidence_ref(out / 'adapter-source.py', ROOT),
              'loaded_source_path': str(source),
              'image': evidence_ref(args.image.resolve(), ROOT), 'checks': [], 'passed': False}

    async def start(name):
        paths = TrialPaths(trial_dir=out / name)
        paths.mkdir()
        env = SingularityEnvironment(environment_dir=task.paths.environment_dir,
            environment_name='lifecycle-regression', session_id=name, trial_paths=paths,
            task_env_config=task.config.environment.model_copy(update={'docker_image': str(args.image.resolve())}),
            persistent_env={'DB_TYPE':'duckdb'}, logger=logger,
            singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
        environments.append(env)
        await asyncio.wait_for(env.start(force_build=False), timeout=300)
        pid = env._server_process.pid
        if os.getpgid(pid) != pid or os.getsid(pid) != pid:
            raise AssertionError('Container does not own a separate session')
        execution = await env.exec('nohup sleep 600 >/dev/null 2>&1 & printf LIFECYCLE_READY', timeout_sec=10)
        if execution.return_code or execution.stdout.strip() != 'LIFECYCLE_READY':
            raise AssertionError(execution)
        return env, pid, env._server_port, env._staging_dir

    async def assert_stopped(label, state):
        env, pgid, port, staging = state
        for _ in range(30):
            members = group_members(pgid)
            opened = port_open(port)
            if not members and not opened:
                break
            await asyncio.sleep(0.1)
        check = {'name':label, 'pgid':pgid, 'remaining_members':members,
                 'http_port':port, 'port_open':opened, 'staging_exists':staging.exists()}
        check['passed'] = not members and not opened and not staging.exists()
        result['checks'].append(check)
        write_json(out / 'result.json', result)
        if not check['passed']:
            raise AssertionError(check)

    try:
        # Independent live peer detects accidental shared-process-group killing.
        first = await start('normal')
        peer = await start('peer')
        await asyncio.wait_for(first[0].stop(delete=True), timeout=20)
        await assert_stopped('normal_stop_with_background_child', first)
        peer_exec = await peer[0].exec('printf PEER_ALIVE', timeout_sec=10)
        result['checks'].append({'name':'independent_peer_survives',
            'passed':peer_exec.return_code == 0 and peer_exec.stdout == 'PEER_ALIVE'})
        if not result['checks'][-1]['passed']:
            raise AssertionError('Unrelated peer was affected')
        await asyncio.wait_for(peer[0].stop(delete=True), timeout=20)
        await assert_stopped('second_independent_stop', peer)
        await peer[0].stop(delete=True)
        result['checks'].append({'name':'stop_is_idempotent', 'passed':True})

        # Reproduce the problematic parent-gone state before invoking stop.
        orphan = await start('launcher-killed')
        os.kill(orphan[1], signal.SIGKILL)
        for _ in range(100):
            if orphan[0]._server_process.returncode is not None:
                break
            await asyncio.sleep(0.01)
        if orphan[0]._server_process.returncode is None:
            raise AssertionError('Launcher did not exit')
        result['checks'].append({'name':'launcher_death_fixture', 'passed':True,
            'returncode':orphan[0]._server_process.returncode,
            'descendants_before_cleanup':group_members(orphan[1]),
            'port_open_before_cleanup':port_open(orphan[2])})
        await asyncio.wait_for(orphan[0].stop(delete=True), timeout=20)
        await assert_stopped('stop_after_launcher_exit', orphan)

        replacement = await start('resource-replacement')
        await asyncio.wait_for(replacement[0]._cleanup_server_resources(), timeout=20)
        await assert_stopped('resource_replacement_cleanup', replacement)

        watchdog = await start('memory-watchdog')
        watchdog[0]._memory_limit_bytes = 1  # Force this path without exhausting host memory.
        await asyncio.wait_for(watchdog[0]._memory_watchdog(), timeout=10)
        if not watchdog[0]._memory_limit_exceeded:
            raise AssertionError('Memory watchdog fixture was not triggered')
        await asyncio.wait_for(watchdog[0].stop(delete=True), timeout=20)
        await assert_stopped('memory_watchdog_cleanup', watchdog)

        # Actual failed starts must reap every retry, not only the last one.
        fixture = out / 'failed-bootstrap'
        fixture.mkdir()
        shutil.copyfile(source.parent / 'server.py', fixture / 'server.py')
        bootstrap = fixture / 'bootstrap.sh'
        bootstrap.write_text('#!/bin/sh\nexit 73\n')
        bootstrap.chmod(0o755)
        module_globals = SingularityEnvironment._start_server.__globals__
        original_file = module_globals['__file__']
        original_create = asyncio.create_subprocess_exec
        launches = []

        async def capture_create(*cmd, **kwargs):
            process = await original_create(*cmd, **kwargs)
            if cmd[0] == 'singularity':
                launches.append({'pgid':process.pid, 'port':int(cmd[cmd.index('--port') + 1])})
            return process

        try:
            module_globals['__file__'] = str(fixture / 'singularity.py')
            asyncio.create_subprocess_exec = capture_create
            try:
                await start('failed-start')
                raise AssertionError('Failing bootstrap unexpectedly started')
            except RuntimeError as exc:
                if 'Server process died' not in str(exc):
                    raise
                failure_message = str(exc)
        finally:
            asyncio.create_subprocess_exec = original_create
            module_globals['__file__'] = original_file
        await asyncio.wait_for(environments[-1].stop(delete=True), timeout=20)
        for attempt in launches:
            attempt['remaining_members'] = group_members(attempt['pgid'])
            attempt['port_open'] = port_open(attempt['port'])
        check = {'name':'failed_start_retries_cleanup', 'fixture':evidence_ref(bootstrap, ROOT),
                 'error':failure_message, 'attempts':launches,
                 'passed':len(launches) == 3 and all(not a['remaining_members'] and not a['port_open'] for a in launches)}
        result['checks'].append(check)
        if not check['passed']:
            raise AssertionError(check)
        result['passed'] = all(c['passed'] for c in result['checks'])
    except Exception as exc:
        result['error'] = {'type':type(exc).__name__, 'message':str(exc), 'traceback':traceback.format_exc()}
    finally:
        for env in environments:
            try:
                await asyncio.wait_for(env.stop(delete=True), timeout=20)
            except Exception as exc:
                result.setdefault('cleanup_errors', []).append(str(exc))
        handler.flush(); handler.close(); logger.removeHandler(handler)
        result['log'] = evidence_ref(out / 'runtime.log', ROOT)
        result['passed'] = result['passed'] and not result.get('cleanup_errors')
        write_json(out / 'result.json', result)
    print(json.dumps({'passed':result['passed'], 'checks':result['checks'], 'error':result.get('error')}, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--candidate-source', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
