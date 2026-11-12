"""Submit an already sealed finite plan to Slurm once; no scheduling or retries."""
from dbt_bench.local_config import expand as _release_expand
import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import socket
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from review_tools import cached_hashes

def parse_job_id(stdout):
    """TACC wraps --parsable with a banner; accept one standalone ID only."""
    matches = [m.group(1) for line in stdout.splitlines() if (m := re.fullmatch('\\s*([1-9][0-9]*)(?:;[A-Za-z0-9_.-]+)?\\s*', line))]
    if len(matches) != 1:
        raise ValueError('Submission response has no unique standalone job ID')
    return matches[0]

def confirm_job(job_id, directory, manifest):
    result = subprocess.run(['scontrol', 'show', 'job', '-o', job_id], capture_output=True, text=True, check=True, timeout=60)
    fields = dict((part.split('=', 1) for part in result.stdout.split() if '=' in part))
    expected = {'JobId': job_id, 'Account': _release_expand('${ALLOCATION}'), 'Partition': 'gh', 'Comment': 'dbt-difficulty-' + manifest['experiment_id'], 'JobName': 'dbt-diff-gh-' + directory.name.removeprefix('batch-'), 'TimeLimit': '2-00:00:00', 'NumTasks': '1'}
    if fields.get('NumNodes') not in ('1', '1-1') or any((fields.get(k) != v for k, v in expected.items())):
        raise ValueError('Slurm job does not match this exact batch identity')
    return result.stdout

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
    if (plan / 'submission-continuation-intent-v2.json').exists():
        raise ValueError('Continuation was already attempted; reconcile before any retry')
    sealed = io.read_json(plan / 'sealed.json')
    for ref in [sealed['summary'], *sealed['preflights']]:
        if not io.evidence_ok(ref, ROOT):
            raise ValueError('Sealed preparation changed')
    summary = io.read_json(plan / 'summary.json')
    entries, confirmed = ([], [])
    with cached_hashes():
        for entry in summary['batches']:
            directory = Path(entry['batch'])
            manifest = io.read_json(directory / 'manifest.json')
            if (directory / 'submission.json').exists():
                previous = io.read_json(directory / 'submission.json')
                if previous['returncode'] != 0 or previous['manifest'] != io.evidence_ref(directory / 'manifest.json', ROOT):
                    raise ValueError('Previous submission is failed/uncertain; manual reconciliation required')
                job_id = parse_job_id(previous['stdout'])
                confirmed.append({'batch': str(directory), 'slurm_job_id': job_id, 'prior_submission': io.evidence_ref(directory / 'submission.json', ROOT), 'allocation': confirm_job(job_id, directory, manifest), 'preserved_without_resubmission': True})
                continue
            if any(((directory / name).exists() for name in ('submission-intent.json', 'launch.json'))):
                raise ValueError('Unresolved prior submission or launch')
            validate_batch(directory)
            entries.append((directory, manifest))
    test_results = []
    for directory, manifest in entries:
        command = sbatch_args(directory, manifest, test_only=True)
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        test_results.append({'batch': str(directory), 'argv': command, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    with (plan / 'submission-test-v2.json').open('x') as stream:
        json.dump({'checks': test_results, 'no_jobs_submitted': True}, stream, indent=2)
    if any((r['returncode'] for r in test_results)):
        raise RuntimeError('Slurm test-only rejected a batch; no job was submitted')
    intent = {'at': datetime.now(timezone.utc).isoformat(), 'plan': io.evidence_ref(plan / 'sealed.json', ROOT), 'submitter': io.evidence_ref(__file__, ROOT), 'training_prohibited': True}
    with (plan / 'submission-continuation-intent-v2.json').open('x') as stream:
        json.dump(intent, stream, indent=2)
    submitted = confirmed
    io.write_json(plan / 'submissions-v2.json', {'jobs': submitted, 'requested_jobs': len(summary['batches']), 'training_prohibited': True})
    for directory, manifest in entries:
        command = sbatch_args(directory, manifest)
        with (directory / 'submission-intent.json').open('x') as stream:
            json.dump({**intent, 'argv': command, 'experiment_id': manifest['experiment_id']}, stream, indent=2)
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        row = {'batch': str(directory), 'argv': command, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr, 'at': datetime.now(timezone.utc).isoformat(), 'manifest': io.evidence_ref(directory / 'manifest.json', ROOT), 'training_prohibited': True}
        if result.returncode == 0:
            try:
                job_id = parse_job_id(result.stdout)
                row['allocation'] = confirm_job(job_id, directory, manifest)
                row['slurm_job_id'] = job_id
            except (ValueError, subprocess.SubprocessError) as exc:
                row['confirmation_error'] = str(exc)
        io.write_json(directory / 'submission.json', row)
        submitted.append(row)
        io.write_json(plan / 'submissions-v2.json', {'jobs': submitted, 'requested_jobs': len(summary['batches']), 'training_prohibited': True})
        print(json.dumps({k: row[k] for k in ('batch', 'returncode', 'slurm_job_id') if k in row}), flush=True)
        if 'slurm_job_id' not in row:
            raise RuntimeError('Submission failed or uncertain; stop to prevent duplicates')
    return submitted
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    submit(p.parse_args().plan)
