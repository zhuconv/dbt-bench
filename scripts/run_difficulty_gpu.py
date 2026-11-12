"""Run one prepared difficulty job with native Harbor; no protocol/model smoke."""
from dbt_bench.local_config import expand as _release_expand
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from run_gh_smoke import inference_environment, stop

def validate(run, root=ROOT):
    run, root = (Path(run), Path(root))
    manifest = io.read_json(run / 'manifest.json')
    identity = manifest['identity']
    config = io.read_json(run / 'config.yaml')
    if io.file_hash(run / 'config.yaml') != manifest['config_hash'] or config != identity['config'] or io.digest(identity) != manifest['experiment_id']:
        raise ValueError('Prepared configuration identity changed')
    if not manifest['training_prohibited'] or identity['purpose'] != 'difficulty_only_no_training' or (not io.evidence_ok(identity['measurement_rules'], root)):
        raise ValueError('Not an authorized locked difficulty experiment')
    rules = io.read_json(root / identity['measurement_rules']['path'])
    if rules['attempts_per_task'] != 4 or not rules['training_prohibited']:
        raise ValueError('Difficulty protocol changed')
    for name in ('serving_spec', 'qwen_settings', 'harbor_runtime_lock', 'inference_runtime_lock'):
        if not io.evidence_ok(rules[name], root):
            raise ValueError('Locked runtime changed: ' + name)
    serving = io.read_json(root / rules['serving_spec']['path'])
    client = io.read_json(root / rules['qwen_settings']['path'])
    if config['n_attempts'] != len(identity['attempt_ids']) or config['n_concurrent_trials'] != 1 or config['retry']['max_retries'] != 0 or (len(config['tasks']) != 1) or (len(config['agents']) != 1) or (config['agents'][0]['name'] != 'qwen-coder') or config['agents'][0].get('import_path'):
        raise ValueError('Unexpected model execution scope or adapter')
    agent = config['agents'][0]
    if agent['model_name'] != 'openai/' + serving['served_model_name'] or agent['base_url'] != 'http://127.0.0.1:8000/v1' or agent['kwargs'].get('version') != '0.26.5' or (agent['kwargs'].get('config') != client):
        raise ValueError('Model, endpoint or generation configuration changed')
    if len(set(identity['attempt_ids'])) != len(identity['attempt_ids']) or not set(identity['attempt_ids']) <= {1, 2, 3, 4}:
        raise ValueError('Invalid independent attempt slots')
    if config['job_name'] != run.name or Path(config['jobs_dir']).resolve() != run.resolve() / 'jobs':
        raise ValueError('Unexpected output destination')
    if io.tree_hash(config['tasks'][0]['path']) != identity['actual_task_hash']:
        raise ValueError('Prepared task changed')
    for ref in [*identity['tools'], identity['generation']['server'], identity['production_manifest']]:
        if not io.evidence_ok(ref, root):
            raise ValueError('Prepared source evidence changed')
    for binding in identity['phase3_production'].values():
        if not io.evidence_ok(binding['image'], root):
            raise ValueError('Pinned SIF changed')
    return (manifest, config, rules)

def run_job(directory):
    out = Path(directory).resolve()
    job = os.environ.get('SLURM_JOB_ID')
    if not str(out).startswith('/scratch/') or not job:
        raise ValueError('Require a prepared SCRATCH run and actual Slurm allocation')
    if (out / 'launch.json').exists():
        raise ValueError('Invocation already exists; preserve it')
    manifest, config, rules = validate(out)
    preflight = io.read_json(out / 'preflight.json')
    for name in ('runner', 'slurm_script', 'manifest'):
        if not io.evidence_ok(preflight[name], ROOT):
            raise ValueError('Submitted execution source changed: ' + name)
    allocation = subprocess.run(['scontrol', 'show', 'job', '-o', job], capture_output=True, text=True, check=True).stdout
    if not all((x in allocation for x in (_release_expand('Account=${ALLOCATION}'), 'Partition=gh-dev', 'TimeLimit=02:00:00'))):
        raise ValueError(_release_expand('Difficulty job must use ${ALLOCATION} / gh-dev / 02:00:00'))
    (out / 'allocation.txt').write_text(allocation)
    shutil.copyfile(__file__, out / 'runner.py')
    state = {'schema_version': 1, 'slurm_job_id': job, 'hostname': socket.gethostname(), 'started_at': time.time(), 'experiment_id': manifest['experiment_id'], 'runner': io.evidence_ref(out / 'runner.py', ROOT), 'helper': io.evidence_ref(ROOT / 'scripts/run_gh_smoke.py', ROOT), 'planned': manifest['planned'], 'training_prohibited': True, 'protocol_model_requests': 0, 'complete': False}
    io.write_json(out / 'launch.json', state)
    server = harbor = None
    server_log = harbor_log = None

    def interrupted(signum, frame):
        raise RuntimeError('Allocation/process signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    env = {**os.environ, 'OPENAI_API_KEY': 'local-no-auth', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(ROOT / 'src') + os.pathsep + str(ROOT / 'scripts'), 'APPTAINER_TMPDIR': '/tmp/dbt-difficulty-' + job, 'OMP_NUM_THREADS': '8'}
    env.pop('LD_PRELOAD', None)
    env['PATH'] = str(ROOT / 'runtime/.venv/bin') + ':/opt/apps/tacc-apptainer/1.4.1/bin:' + env.get('PATH', '')
    Path(env['APPTAINER_TMPDIR']).mkdir(exist_ok=True)
    try:
        subprocess.run(['nvidia-smi', '-q'], stdout=(out / 'gpu.txt').open('w'), stderr=subprocess.STDOUT, check=True, timeout=30)
        spec = io.read_json(ROOT / 'inference/qwen3.6-35b-a3b.json')
        model = io.read_json(ROOT / 'artifacts/inference/model.json')
        if not model['verified'] or model['revision'] != spec['revision'] or model['spec_sha256'] != io.file_hash(ROOT / 'inference/qwen3.6-35b-a3b.json'):
            raise ValueError('Locked model inventory differs')
        snapshot = Path(model['snapshot'])
        for item in model['files']:
            if (snapshot / item['path']).stat().st_size != item['size']:
                raise ValueError('Locked model cache changed')
        server_env = inference_environment(ROOT, env)
        with socket.socket() as port_probe:
            port_probe.bind(('127.0.0.1', 8000))
        command = [str(ROOT / 'inference/.venv/bin/vllm'), 'serve', str(snapshot), '--served-model-name', spec['served_model_name'], *spec['server_args'], '--override-generation-config', json.dumps(spec['generation_defaults']), '--seed', str(manifest['identity']['server_seed'])]
        io.write_json(out / 'server-command.json', {'argv': command, 'model': io.evidence_ref(ROOT / 'artifacts/inference/model.json', ROOT), 'runtime_lock': io.evidence_ref(ROOT / 'inference/uv.lock', ROOT), 'runtime_environment': io.evidence_ref(ROOT / 'inference/runtime-env.json', ROOT), 'model_requests_outside_measurement': 0, 'training_prohibited': True})
        server_log = (out / 'vllm.log').open('w')
        server = subprocess.Popen(command, cwd=ROOT, env=server_env, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
        state['server_pid'] = server.pid
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError('Pinned model server exited: ' + str(server.returncode))
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/v1/models', timeout=5) as response:
                    models = json.load(response)
                if spec['served_model_name'] not in [m['id'] for m in models['data']]:
                    raise ValueError('Unexpected endpoint model')
                io.write_json(out / 'models.json', models)
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(3)
        else:
            raise TimeoutError('Server startup exceeded20minutes')
        state['server_ready_at'] = time.time()
        io.write_json(out / 'runtime-status.json', state)
        harbor_log = (out / 'harbor.log').open('w')
        harbor = subprocess.Popen([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(out / 'config.yaml')], cwd=ROOT, env=env, stdout=harbor_log, stderr=subprocess.STDOUT, start_new_session=True)
        state['harbor_pid'] = harbor.pid
        state['harbor_started_at'] = time.time()
        io.write_json(out / 'runtime-status.json', state)
        state['harbor_returncode'] = harbor.wait()
        paths = sorted((out / 'jobs' / config['job_name']).glob('*/result.json'))
        state['raw_trials'] = [io.evidence_ref(p, ROOT) for p in paths]
        state['complete'] = len(paths) == len(manifest['planned'])
        state['audit_status'] = 'pending_posthoc_submission_audit'
        cleanup = subprocess.run([str(ROOT / 'runtime/.venv/bin/python'), str(ROOT / 'scripts/verify_container_cleanup.py'), '--jobs', str(out / 'jobs'), '--output', str(out / 'cleanup.json')], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        (out / 'cleanup.log').write_text(cleanup.stdout + cleanup.stderr)
        state['cleanup_returncode'] = cleanup.returncode
    except BaseException as exc:
        state['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        raise
    finally:
        stop(harbor)
        stop(server)
        if server_log:
            server_log.close()
        if harbor_log:
            harbor_log.close()
        state['finished_at'] = time.time()
        io.write_json(out / 'launch-result.json', state)
    return state
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    a = p.parse_args()
    r = run_job(a.run)
    print(json.dumps({'complete': r['complete'], 'raw_trials': len(r.get('raw_trials', [])), 'audit_status': r.get('audit_status'), 'training_prohibited': True}))
