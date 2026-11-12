"""Submit an already sealed finite plan to Slurm once; no scheduling or retries."""
from dbt_bench.local_config import expand as _release_expand
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from review_tools import cached_hashes

def sbatch_args(batch, manifest, *, test_only=False):
    batch = Path(batch)
    command = ['sbatch', '--parsable', _release_expand('--account=${ALLOCATION}'), '--partition=gh', '--nodes=1', '--ntasks=1', '--time=48:00:00', '--job-name=dbt-diff-gh-' + batch.name.removeprefix('batch-'), '--comment=dbt-difficulty-' + manifest['experiment_id'], '--output=' + str(batch / 'slurm-%j.out'), '--error=' + str(batch / 'slurm-%j.err')]
    if test_only:
        command.append('--test-only')
    return command + [str(ROOT / 'scripts/slurm/gh-difficulty-batch.sbatch'), str(batch)]

def submit(plan):
    from run_difficulty_gh_batch import validate_batch
    plan = Path(plan).resolve()
    if 'login' not in socket.gethostname():
        raise ValueError('Submit from Vista login node')
    if (plan / 'submission-intent.json').exists():
        raise ValueError('Submission was already attempted; reconcile Slurm before any retry')
    sealed = io.read_json(plan / 'sealed.json')
    for ref in [sealed['summary'], *sealed['preflights']]:
        if not io.evidence_ok(ref, ROOT):
            raise ValueError('Sealed preparation changed')
    summary = io.read_json(plan / 'summary.json')
    entries = []
    with cached_hashes():
        for entry in summary['batches']:
            directory = Path(entry['batch'])
            validate_batch(directory)
            if any(((directory / name).exists() for name in ('submission-intent.json', 'submission.json', 'launch.json'))):
                raise ValueError('Batch was previously submitted/launched')
            entries.append((directory, io.read_json(directory / 'manifest.json')))
    test_results = []
    for directory, manifest in entries:
        command = sbatch_args(directory, manifest, test_only=True)
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        test_results.append({'batch': str(directory), 'argv': command, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    with (plan / 'submission-test.json').open('x') as stream:
        json.dump({'checks': test_results, 'no_jobs_submitted': True}, stream, indent=2)
    if any((r['returncode'] for r in test_results)):
        raise RuntimeError('Slurm test-only rejected a batch; no job was submitted')
    intent = {'at': datetime.now(timezone.utc).isoformat(), 'plan': io.evidence_ref(plan / 'sealed.json', ROOT), 'submitter': io.evidence_ref(__file__, ROOT), 'training_prohibited': True}
    with (plan / 'submission-intent.json').open('x') as stream:
        json.dump(intent, stream, indent=2)
    submitted = []
    for directory, manifest in entries:
        command = sbatch_args(directory, manifest)
        with (directory / 'submission-intent.json').open('x') as stream:
            json.dump({**intent, 'argv': command, 'experiment_id': manifest['experiment_id']}, stream, indent=2)
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        row = {'batch': str(directory), 'argv': command, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr, 'at': datetime.now(timezone.utc).isoformat(), 'manifest': io.evidence_ref(directory / 'manifest.json', ROOT), 'training_prohibited': True}
        job_id = result.stdout.strip().split(';')[0]
        if result.returncode == 0 and job_id.isdigit():
            row['slurm_job_id'] = job_id
        io.write_json(directory / 'submission.json', row)
        submitted.append(row)
        io.write_json(plan / 'submissions.json', {'jobs': submitted, 'requested_jobs': len(entries), 'training_prohibited': True})
        print(json.dumps({k: row[k] for k in ('batch', 'returncode', 'slurm_job_id') if k in row}), flush=True)
        if 'slurm_job_id' not in row:
            raise RuntimeError('Submission failed or uncertain; stop to prevent duplicates')
    return submitted
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    submit(p.parse_args().plan)
