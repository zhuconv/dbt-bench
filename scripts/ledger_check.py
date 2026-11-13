#!/usr/bin/env python3
"""Check a ledger pull request before the experiment owner merges it.

A ledger PR comes from a branch named ledger/<batch>/<worker> (scripts/worker.py opens it). It
may only add or change:
  ledger/claims/<batch>/<worker>.json
  ledger/results/<batch>/<task>/summary.json    added once, never modified
Checks:
  - no other path is touched;
  - the batch exists in release/batches.json, and each task belongs to it;
  - each JSON file is well formed and names the same batch, task and worker;
  - each summary points at a raw archive in a `raw-v1` GitHub release, with SHA256 and size.
With --verify-archives, every raw archive is also downloaded and its size and SHA256
compared; the workflow does this once the PR is marked ready for review.

usage: ledger_check.py --repo DIR --base SHA --branch ledger/<batch>/<worker> [--verify-archives]
The workflow runs this file from the base branch against the PR checkout, so a PR cannot
weaken the check by editing it.
"""
import argparse, datetime as dt, hashlib, json, re, subprocess, sys, urllib.request
from pathlib import Path

STATES = {'claimed', 'queued', 'running', 'finished', 'released'}
HEX64 = re.compile(r'[0-9a-f]{64}')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--base', required=True)
    p.add_argument('--branch', required=True)
    p.add_argument('--verify-archives', action='store_true')
    a = p.parse_args()
    repo, errors = Path(a.repo), []
    m = re.fullmatch(r'ledger/(batch-\d+)/([A-Za-z0-9._-]+)', a.branch)
    if not m:
        sys.exit(f'not a ledger branch: {a.branch}')
    batch, worker = m[1], m[2]
    batches = {b['id']: b for b in json.loads(git(repo, 'show', f'{a.base}:release/batches.json'))['batches']}
    if batch not in batches:
        sys.exit(f'unknown batch {batch}')
    tasks = set(batches[batch]['task_keys'])
    changes = [line.split('\t') for line in git(repo, 'diff', '--name-status', f'{a.base}...HEAD').splitlines()]
    archives = []
    for status, *paths in changes:
        path = paths[-1]
        if path == f'ledger/claims/{batch}/{worker}.json':
            if status not in ('A', 'M'):
                errors.append(f'{path}: unexpected change {status}')
                continue
            c = json.loads((repo / path).read_text())
            if c.get('batch') != batch or c.get('worker') != worker or c.get('state') not in STATES:
                errors.append(f'{path}: batch/worker/state do not match the branch')
            for k in ('claimed_at', 'lease_until'):
                try:
                    dt.datetime.fromisoformat(str(c.get(k)).replace('Z', '+00:00'))
                except ValueError:
                    errors.append(f'{path}: bad {k}')
            continue
        r = re.fullmatch(rf'ledger/results/{batch}/([^/]+)/summary\.json', path)
        if not r:
            errors.append(f'{path}: a ledger PR may only touch its own claim and result files')
            continue
        if status != 'A':
            errors.append(f'{path}: results are added once and never changed ({status})')
            continue
        task = r[1]
        s = json.loads((repo / path).read_text())
        if task not in tasks:
            errors.append(f'{path}: {task} is not part of {batch}')
        if s.get('batch') != batch or s.get('task_key') != task or s.get('worker') != worker:
            errors.append(f'{path}: batch/task_key/worker do not match the path and branch')
        ra = s.get('raw_archive') or {}
        url = str(ra.get('url', ''))
        if not (HEX64.fullmatch(str(ra.get('sha256', ''))) and isinstance(ra.get('bytes'), int) and ra['bytes'] > 0
                and re.fullmatch(r'https://github\.com/[^/]+/[^/]+/releases/download/raw-v1/[^/]+', url)
                and url.endswith('/' + str(ra.get('name')))):
            errors.append(f'{path}: raw_archive needs name, sha256, bytes and a raw-v1 release URL')
        else:
            archives.append((path, ra))
    if not changes:
        errors.append('the PR changes nothing')
    if a.verify_archives:
        for path, ra in archives:
            h, n = hashlib.sha256(), 0
            try:
                with urllib.request.urlopen(ra['url'], timeout=120) as resp:
                    for chunk in iter(lambda: resp.read(1 << 22), b''):
                        h.update(chunk)
                        n += len(chunk)
            except Exception as exc:
                errors.append(f'{path}: cannot download the raw archive ({type(exc).__name__})')
                continue
            if n != ra['bytes'] or h.hexdigest() != ra['sha256']:
                errors.append(f'{path}: raw archive size or SHA256 differs from the summary')
    print(f'{batch} by {worker}: {len(changes)} changed files, {len(archives)} result summaries'
          + (', archives verified' if a.verify_archives and not errors else ''))
    for e in errors:
        print('ERROR', e)
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
