"""Tests for scripts/worker.py.

ResolveTest checks the holder rules. PrModeTest and DirectModeTest run the tool against
local git repositories and a fake `gh` that keeps pull requests in a JSON file. The fake
assigns PR numbers in creation order under a file lock, as GitHub does.
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('worker', REPO / 'scripts' / 'worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

T0 = dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.timezone.utc)


def claim(w, state, hours=6, at=T0):
    return {'worker': w, 'state': state, 'platform': 'p', 'claimed_at': worker.iso(at),
            'lease_until': worker.iso(at + dt.timedelta(hours=hours))}


def pr(n, c):
    return {'number': n, 'worker': c['worker'], 'claim': c}


class ResolveTest(unittest.TestCase):
    tasks = ['a', 'b']

    def r(self, claims=(), prs=(), results=None, t=T0):
        return worker.resolve(self.tasks, list(claims), list(prs), results or {}, t)

    def test_free_and_finished(self):
        self.assertEqual(self.r(), ('free', None))
        self.assertEqual(self.r(results={'a': 'x'})[0], 'partial-unclaimed')
        self.assertEqual(self.r(results={'a': 'x', 'b': 'y'})[0], 'finished')

    def test_pr_takes_over_queued_claim_on_main(self):
        label, h = self.r([claim('owner', 'queued', 999)], [pr(7, claim('alice', 'claimed'))])
        self.assertEqual((label, h['worker'], h['via']), ('claimed', 'alice', 'pr#7'))

    def test_lowest_pr_number_wins(self):
        label, h = self.r(prs=[pr(9, claim('bob', 'claimed')), pr(8, claim('alice', 'claimed'))])
        self.assertEqual(h['worker'], 'alice')

    def test_fresh_claim_on_main_beats_prs(self):
        label, h = self.r([claim('owner', 'claimed')], [pr(3, claim('alice', 'claimed'))])
        self.assertEqual((h['worker'], h['via']), ('owner', 'main'))

    def test_running_always_holds_and_two_running_conflict(self):
        label, h = self.r([claim('owner', 'running')], [pr(3, claim('alice', 'claimed'))])
        self.assertEqual((label, h['worker']), ('running', 'owner'))
        self.assertEqual(self.r([claim('owner', 'running')], [pr(3, claim('alice', 'running'))])[0], 'conflict')

    def test_expired_claimed_is_free_and_expired_running_is_stale(self):
        later = T0 + dt.timedelta(hours=7)
        self.assertEqual(self.r([claim('owner', 'claimed')], t=later)[0], 'free')
        self.assertEqual(self.r([claim('owner', 'running')], t=later)[0], 'stale')

    def test_released_and_finished_claims_do_not_hold(self):
        self.assertEqual(self.r([claim('owner', 'released'), claim('bob', 'finished')])[0], 'free')


FAKE_GH = r'''#!/usr/bin/env python3
import base64, fcntl, json, os, subprocess, sys
db_path, repos = os.environ['FAKE_GH_DB'], json.loads(os.environ['FAKE_GH_REPOS'])
args = sys.argv[1:]

def opt(name):
    return args[args.index(name) + 1] if name in args else None

with open(db_path, 'r+') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    db = json.load(f)
    out, code = '', 0
    if args[:2] == ['pr', 'list']:
        out = json.dumps([{'number': p['number'], 'headRefName': p['branch'],
                           'headRepositoryOwner': {'login': p['owner']}, 'headRepository': {'name': 'dbt-bench'}}
                          for p in db['prs'] if p['state'] == 'open'])
    elif args[:2] == ['pr', 'create']:
        owner, branch = opt('--head').split(':', 1)
        db['next'] += 1
        db['prs'].append({'number': db['next'], 'owner': owner, 'branch': branch, 'state': 'open', 'draft': True})
        out = f"https://github.com/zhuconv/dbt-bench/pull/{db['next']}"
    elif args[:2] == ['pr', 'close']:
        for p in db['prs']:
            if p['number'] == int(args[2]):
                p['state'] = 'closed'
    elif args[:2] == ['pr', 'ready']:
        for p in db['prs']:
            if p['number'] == int(args[2]):
                p['draft'] = False
    elif args[0] == 'api':
        path = args[1].split('?')[0]
        ref = args[1].split('ref=')[1]
        _, owner, name, _, rel = path.split('/', 4)
        r = subprocess.run(['git', '--git-dir', repos[f'{owner}/{name}'], 'show', f'{ref}:{rel}'],
                           capture_output=True)
        if r.returncode:
            code = 1
        else:
            out = json.dumps({'content': base64.b64encode(r.stdout).decode()})
    f.seek(0)
    f.truncate()
    json.dump(db, f)
print(out)
sys.exit(code)
'''


class LedgerRepos(unittest.TestCase):
    """An upstream repository with release/batches.json, forks, and a fake gh."""

    batch_ids = ('batch-01', 'batch-02', 'batch-03')
    seed_claims = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / 'bin').mkdir()
        gh = self.tmp / 'bin' / 'gh'
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        (self.tmp / 'db.json').write_text(json.dumps({'next': 0, 'prs': []}))
        self.up = self.tmp / 'upstream.git'
        self.sh('git', 'init', '-q', '--bare', '-b', 'main', str(self.up))
        seed = self.tmp / 'seed'
        self.sh('git', 'init', '-q', '-b', 'main', str(seed))
        batches = {'batches': [{'id': b, 'task_keys': [f'{b}-t1', f'{b}-t2']} for b in self.batch_ids]}
        (seed / 'release').mkdir()
        (seed / 'release' / 'batches.json').write_text(json.dumps(batches))
        for (bid, w), c in self.seed_claims.items():
            p = seed / 'ledger' / 'claims' / bid / f'{w}.json'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(c))
        self.git(seed, 'add', '-A')
        self.git(seed, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init')
        self.git(seed, 'push', '-q', str(self.up), 'main')
        self.repos = {'zhuconv/dbt-bench': str(self.up)}

    def sh(self, *cmd, env=None):
        return subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)

    def git(self, repo, *args):
        return self.sh('git', '-C', str(repo), *args)

    def fork(self, user):
        bare = self.tmp / f'fork-{user}.git'
        self.sh('git', 'clone', '-q', '--bare', str(self.up), str(bare))
        clone = self.tmp / f'ledger-{user}'
        self.sh('git', 'clone', '-q', str(bare), str(clone))
        self.git(clone, 'remote', 'add', 'upstream', str(self.up))
        self.git(clone, 'config', 'user.name', user)
        self.git(clone, 'config', 'user.email', f'{user}@t')
        self.repos[f'{user}/dbt-bench'] = str(bare)
        return clone

    def w(self, user, clone, mode, *args):
        env = {**os.environ, 'PATH': f"{self.tmp / 'bin'}:{os.environ['PATH']}",
               'FAKE_GH_DB': str(self.tmp / 'db.json'), 'FAKE_GH_REPOS': json.dumps(self.repos),
               'DBT_BENCH_FORK': f'{user}/dbt-bench'}
        return subprocess.run([sys.executable, '-I', str(REPO / 'scripts' / 'worker.py'), '--ledger', str(clone),
                               '--mode', mode, '--worker', user, *args], capture_output=True, text=True, env=env)

    def merge(self, user, branch, number):
        """What the owner's merge does: bring the PR branch into upstream main and close the PR."""
        clone = self.tmp / 'merger'
        if not clone.exists():
            self.sh('git', 'clone', '-q', str(self.up), str(clone))
        self.git(clone, 'pull', '-q')
        self.git(clone, 'fetch', '-q', self.repos[f'{user}/dbt-bench'], branch)
        self.git(clone, '-c', 'user.name=o', '-c', 'user.email=o@t', 'merge', '-q', '--squash', 'FETCH_HEAD')
        self.git(clone, '-c', 'user.name=o', '-c', 'user.email=o@t', 'commit', '-qm', f'merge #{number}')
        self.git(clone, 'push', '-q', 'origin', 'main')
        db = json.loads((self.tmp / 'db.json').read_text())
        for p in db['prs']:
            if p['number'] == number:
                p['state'] = 'merged'
        (self.tmp / 'db.json').write_text(json.dumps(db))


class PrModeTest(LedgerRepos):
    seed_claims = {('batch-01', 'owner'): claim('owner', 'queued', hours=24 * 365, at=dt.datetime.now(dt.timezone.utc))}

    def test_race_takeover_publish_and_merge(self):
        a, b = self.fork('alice'), self.fork('bob')
        out = {}
        threads = [threading.Thread(target=lambda u=u, c=c: out.__setitem__(u, self.w(u, c, 'pr', 'next', '--platform', 'p')))
                   for u, c in (('alice', a), ('bob', b))]
        [t.start() for t in threads]
        [t.join() for t in threads]
        got = {u: json.loads(p.stdout)['batch'] for u, p in out.items()}
        # Both wanted the first free batch; the lower PR number kept it, the other moved on.
        self.assertEqual(sorted(got.values()), ['batch-02', 'batch-03'], out)
        db = json.loads((self.tmp / 'db.json').read_text())
        self.assertEqual(sum(p['state'] == 'open' for p in db['prs']), 2)

        # No batch is free any more, so the next claim takes over the owner's queued batch.
        t = self.w('alice', a, 'pr', 'next', '--platform', 'p')
        self.assertEqual(json.loads(t.stdout)['took_over']['worker'], 'owner', t.stderr)
        owner = self.tmp / 'owner'
        self.sh('git', 'clone', '-q', str(self.up), str(owner))
        self.assertEqual(self.w('owner', owner, 'direct', 'check', 'batch-01').returncode, 3)

        ba = got['alice']
        self.assertEqual(self.w('bob', b, 'pr', 'check', ba).returncode, 3)
        self.assertEqual(self.w('alice', a, 'pr', 'check', ba).returncode, 0)
        self.assertEqual(self.w('alice', a, 'pr', 'heartbeat', ba, '--state', 'running', '--slurm-job', '7').returncode, 0)
        raw = self.tmp / 'raw.tar.gz'
        raw.write_bytes(b'raw')
        for task in (f'{ba}-t1', f'{ba}-t2'):
            s = self.tmp / f'{task}.json'
            s.write_text(json.dumps({'batch': ba, 'task_key': task}))
            p = self.w('alice', a, 'pr', 'publish', ba, task, str(s), str(raw))
            self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.w('alice', a, 'pr', 'finish', ba).returncode, 0)

        # The PR check accepts this branch, and rejects it once it touches anything else.
        base = self.git(a, 'rev-parse', 'upstream/main').stdout.strip()
        check = [sys.executable, '-I', str(REPO / 'scripts' / 'ledger_check.py'), '--repo', str(a),
                 '--base', base, '--branch', f'ledger/{ba}/alice']
        ok = subprocess.run(check, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn('2 result summaries', ok.stdout)
        (a / 'README.md').write_text('sneaky change\n')
        self.git(a, 'add', 'README.md')
        self.git(a, 'commit', '-qm', 'unrelated')
        bad = subprocess.run(check, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn('may only touch its own claim and result files', bad.stdout)
        self.git(a, 'reset', '-q', '--hard', 'HEAD~1')

        number = next(p['number'] for p in json.loads((self.tmp / 'db.json').read_text())['prs']
                      if p['owner'] == 'alice' and p['branch'] == f'ledger/{ba}/alice')
        self.merge('alice', f'ledger/{ba}/alice', number)
        status = self.w('bob', b, 'pr', 'status').stdout
        self.assertIn(f'{ba}  finished          results 2/2', status)


class DirectModeTest(LedgerRepos):
    seed_claims = {('batch-01', 'owner'): claim('owner', 'queued', hours=24 * 365, at=dt.datetime.now(dt.timezone.utc))}

    def test_owner_updates_its_held_batch(self):
        o = self.tmp / 'owner'
        self.sh('git', 'clone', '-q', str(self.up), str(o))
        self.git(o, 'config', 'user.name', 'owner')
        self.git(o, 'config', 'user.email', 'o@t')
        self.assertEqual(self.w('owner', o, 'direct', 'next', '--platform', 'p').returncode, 3)
        self.assertEqual(self.w('owner', o, 'direct', 'check', 'batch-01').returncode, 0)
        self.assertEqual(self.w('owner', o, 'direct', 'heartbeat', 'batch-01', '--state', 'running').returncode, 0)
        self.assertEqual(self.w('owner', o, 'direct', 'release', 'batch-01', '--reason', 'x').returncode, 3)
        raw = self.tmp / 'raw.tar.gz'
        raw.write_bytes(b'raw')
        s = self.tmp / 's.json'
        s.write_text(json.dumps({'batch': 'batch-01', 'task_key': 'batch-01-t1'}))
        self.assertEqual(self.w('owner', o, 'direct', 'publish', 'batch-01', 'batch-01-t1', str(s), str(raw)).returncode, 0)
        self.assertEqual(self.w('owner', o, 'direct', 'publish', 'batch-01', 'batch-01-t1', str(s), str(raw)).returncode, 3)
        self.assertIn('batch-01  running           results 1/2', self.w('owner', o, 'direct', 'status').stdout)


if __name__ == '__main__':
    unittest.main()
