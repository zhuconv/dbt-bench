#!/usr/bin/env python3
"""Launch one prepared no-model batch; Harbor owns scheduling and cleanup."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import os
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench import io


def check_batch(batch, root=ROOT):
    batch, root = Path(batch).resolve(), Path(root)
    manifest = io.read_json(batch / 'manifest.json')
    if manifest.get('phase') != 4 or manifest.get('model_requests') != 0:
        raise ValueError('Require a prepared phase4 no-model batch')
    if not io.evidence_ok(manifest['config'], root):
        raise ValueError('Prepared configuration changed')
    config = io.read_json(batch / 'config.yaml')
    agents = config.get('agents', [])
    if len(agents) != 1 or agents[0].get('import_path') not in {
            'audit_rebuild_agent:AuditRebuildAgent', 'phase4_case_agent:Phase4CaseAgent'}:
        raise ValueError('Only reviewed no-model reconstruction/case adapters may launch')
    if any(agents[0].get(key) for key in ('model_name', 'base_url', 'mcp_servers', 'skills')):
        raise ValueError('Model/remote-tool configuration is not a no-model validation')
    if config['environment']['type'] != 'singularity' or config['job_name'] != batch.name:
        raise ValueError('Require the prepared Apptainer job identity')
    if Path(config['jobs_dir']).resolve() != batch / 'jobs':
        raise ValueError('Prepared job output path differs')
    if any(not io.evidence_ok(ref, root) for ref in manifest['tools']):
        raise ValueError('Prepared adapter/tool source changed')
    for label, entry in manifest['tasks'].items():
        if io.tree_hash(batch / 'tasks' / label) != entry['validation_copy_hash']:
            raise ValueError('Prepared validation task changed: ' + label)
    return manifest


def launch(batch):
    batch = Path(batch).resolve()
    if not batch.is_relative_to('/scratch'):
        raise ValueError('Validation output must be on SCRATCH')
    job = os.environ.get('SLURM_JOB_ID', '')
    if not job.isdigit() or not os.environ.get('SLURM_JOB_NODELIST'):
        raise ValueError('Launch inside the allocated compute-node shell')
    if any((batch / name).exists() for name in ('launch.json', 'launch-intent.json', 'jobs', 'harbor.log')):
        raise ValueError('Batch already launched or attempted; preserve its artifacts')
    manifest = check_batch(batch)
    binary = ROOT / 'runtime/.venv/bin/harbor'
    if not binary.is_file():
        raise ValueError('Pinned Harbor runtime is unavailable')
    link = ROOT / 'artifacts/validation' / batch.name
    if not link.exists():
        link.symlink_to(batch, target_is_directory=True)
    if link.resolve() != batch:
        raise ValueError('Validation artifact link points elsewhere')
    env = os.environ.copy(); env.pop('LD_PRELOAD', None)
    temp = Path('/tmp') / ('dbt-bench-validation-' + job); temp.mkdir(exist_ok=True)
    env.update(PYTHONPATH=str(batch / 'tools'),
        PATH=str(ROOT / 'runtime/.venv/bin') + ':/opt/apps/tacc-apptainer/1.4.1/bin:' + env.get('PATH', ''),
        APPTAINER_CACHEDIR=str(ROOT / '.cache/apptainer'), APPTAINER_TMPDIR=str(temp),
        OMP_NUM_THREADS='4', PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
    info = {'host': socket.getfqdn(), 'slurm_job_id': job,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'config': io.evidence_ref(batch / 'config.yaml', ROOT),
            'manifest': io.evidence_ref(batch / 'manifest.json', ROOT),
            'tools': manifest['tools'], 'model_requests': 0,
            'launcher': io.evidence_ref(Path(__file__), ROOT)}
    with (batch / 'launch-intent.json').open('x') as stream:
        import json
        stream.write(json.dumps(info, indent=2, sort_keys=True) + '\n')
    with (batch / 'harbor.log').open('x') as log:
        process = subprocess.Popen([str(binary), 'run', '-c', str(batch / 'config.yaml')],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        io.write_json(batch / 'launch.json', {**info, 'pid': process.pid})
        print({'batch': batch.name, 'job_id': job, 'pid': process.pid}, flush=True)
        code = process.wait()
    io.write_json(batch / 'launch-result.json', {'returncode': code,
        'finished_at': datetime.now(timezone.utc).isoformat(),
        'log': io.evidence_ref(batch / 'harbor.log', ROOT), 'model_requests': 0})
    print({'batch': batch.name, 'returncode': code}, flush=True)
    return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', required=True, type=Path)
    raise SystemExit(launch(parser.parse_args().batch))
