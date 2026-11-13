#!/usr/bin/env python3
"""Coordinate dbt-bench measurement batches between workers through GitHub.

The ledger lives in this repository (zhuconv/dbt-bench):

  release/batches.json                           the work units
  ledger/claims/<batch>/<worker>.json            one worker's claim on a batch: platform, state, lease, Slurm job
  ledger/results/<batch>/<task>/summary.json     one task's results, written once and never edited

Raw run archives are GitHub release assets (release `raw-v1`). Each summary records the
archive's URL and SHA256.

Modes:
  pr      Default, for anyone. Each batch gets one pull request from your fork, on a branch
          named ledger/<batch>/<worker>:
            - opening the draft PR is the claim;
            - pushing to it publishes results;
            - the experiment owner merging it accepts them.
          Among open PRs for the same batch, the lowest PR number wins. Raw archives go to
          the `raw-v1` release of your fork.
  direct  For maintainers, and only for batches they already hold on main (the owner's
          Vista runs). Updates commit straight to main, and raw archives go to the `raw-v1`
          release of zhuconv/dbt-bench. New batches are always claimed in pr mode, because
          PR numbers give one claim order.

Run the tool in a dedicated ledger clone (--ledger or DBT_BENCH_LEDGER), never in the
checkout your jobs run from:
  - pr mode: origin = your fork, upstream = zhuconv/dbt-bench, as `gh repo clone <you>/dbt-bench` sets up;
  - direct mode: origin = zhuconv/dbt-bench.

Claim states:
  claimed   taken; must move to queued or running within 6 hours or the batch is free again
  queued    the Slurm job is submitted but has not started. Another worker may take over a
            queued claim that sits on main; that claim's owner then cancels its job and
            releases the claim.
  running   the job has started; renew the 6 h lease with `heartbeat` at least every 2 hours.
            An expired running claim shows as stale and is never reassigned automatically.
  finished  the batch is done. In an open PR the batch shows as `review` and stays held
            until the owner merges or closes the PR. On main it holds the batch even
            without result summaries (the owner's own Vista runs are recorded this way).
  released  given up before running
"""
import argparse, base64, datetime as dt, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

UPSTREAM = 'zhuconv/dbt-bench'
RAW_RELEASE = 'raw-v1'
LEASE_HOURS = {'claimed': 6, 'queued': 24 * 14, 'running': 6}
ACTIVE = ('claimed', 'queued', 'running')
CLAIM_RE = re.compile(r'ledger/claims/(batch-\d+)/([A-Za-z0-9._-]+)\.json')
RESULT_RE = re.compile(r'ledger/results/(batch-\d+)/([^/]+)/summary\.json')
BRANCH_RE = re.compile(r'ledger/(batch-\d+)/([A-Za-z0-9._-]+)')


class Conflict(Exception):
    """The ledger forbids this action; nothing was changed."""


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.isoformat().replace('+00:00', 'Z')


def parse(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))


def run(cmd, check=True):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(cmd[:4])} ... failed: {(p.stderr or p.stdout).strip()[-400:]}")
    return p


def gh(*args, check=True):
    return run(['gh', *args], check=check)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def resolve(tasks, claims, prs, results, t=None):
    """Who holds one batch. Returns (label, holder claim or None).

    claims:  claims on main, each a dict
    prs:     open ledger PRs for this batch: {'number', 'worker', 'claim'}
    results: {task: worker} of summaries on main
    Rules: complete results mean finished, and so does a `finished` claim on main (the
    owner's own runs publish no summaries); a running claim always holds; an open PR whose
    claim is `finished` holds as `review` until the owner merges or closes it; a fresh
    `claimed` claim on main holds; otherwise the lowest-numbered open PR with an active claim
    holds (this is how a PR takes over a queued claim on main); otherwise the oldest active
    claim on main holds.
    """
    t = t or now()
    if set(tasks) <= set(results):
        return 'finished', None
    done = [dict(c, via='main') for c in claims if c.get('state') == 'finished']
    if done:
        return 'finished', min(done, key=lambda c: c.get('finished_at') or c.get('claimed_at') or '')

    def active(c):
        if not c or c.get('state') not in ACTIVE:
            return False
        return not (c['state'] == 'claimed' and parse(c['lease_until']) < t)

    on_main = [dict(c, via='main') for c in claims if active(c)]
    in_prs = [dict(p['claim'], via=f"pr#{p['number']}", pr=p['number'])
              for p in sorted(prs, key=lambda p: p['number'])
              if active(p['claim']) or (p['claim'] or {}).get('state') == 'finished']
    running = [c for c in on_main + in_prs if c['state'] == 'running']
    if len(running) > 1:
        return 'conflict', running[0]
    if running:
        c = running[0]
        return ('stale' if parse(c['lease_until']) < t else 'running'), c
    review = [c for c in in_prs if c['state'] == 'finished']
    if review:
        return 'review', review[0]
    fresh = sorted((c for c in on_main if c['state'] == 'claimed'), key=lambda c: (c['claimed_at'], c['worker']))
    if fresh:
        return 'claimed', fresh[0]
    if in_prs:
        return in_prs[0]['state'], in_prs[0]
    if on_main:
        c = min(on_main, key=lambda c: c['claimed_at'])
        return c['state'], c
    return ('partial-unclaimed' if results else 'free'), None


class Ledger:
    def __init__(self, path, mode, worker=None):
        self.path, self.mode, self.worker = Path(path).resolve(), mode, worker
        self.remote = 'upstream' if mode == 'pr' else 'origin'
        self.ref = f'{self.remote}/main'

    def git(self, *args, check=True):
        return run(['git', '-C', str(self.path), *args], check=check)

    def fetch(self):
        self.git('fetch', '--quiet', self.remote, 'main')

    def show(self, rel, ref=None):
        p = self.git('show', f'{ref or self.ref}:{rel}', check=False)
        return p.stdout if p.returncode == 0 else None

    def files(self, rel, ref=None):
        return self.git('ls-tree', '-r', '--name-only', ref or self.ref, '--', rel).stdout.split()

    def batches(self):
        return {b['id']: b for b in json.loads(self.show('release/batches.json'))['batches']}

    def main_claims(self):
        out = {}
        for f in self.files('ledger/claims'):
            if CLAIM_RE.fullmatch(f):
                out.setdefault(CLAIM_RE.fullmatch(f)[1], []).append(json.loads(self.show(f)))
        return out

    def main_results(self):
        out = {}
        for f in self.files('ledger/results'):
            m = RESULT_RE.fullmatch(f)
            if m:
                out.setdefault(m[1], {})[m[2]] = json.loads(self.show(f)).get('worker')
        return out

    def open_prs(self):
        raw = gh('pr', 'list', '--repo', UPSTREAM, '--state', 'open', '--limit', '300', '--json',
                 'number,headRefName,headRepositoryOwner,headRepository').stdout
        out = []
        for p in json.loads(raw or '[]'):
            m = BRANCH_RE.fullmatch(p['headRefName'])
            if not m:
                continue
            repo = f"{p['headRepositoryOwner']['login']}/{p['headRepository']['name']}"
            path = f'ledger/claims/{m[1]}/{m[2]}.json'
            r = gh('api', f"repos/{repo}/contents/{path}?ref={p['headRefName']}", check=False)
            claim = json.loads(base64.b64decode(json.loads(r.stdout)['content'])) if r.returncode == 0 else None
            out.append({'number': p['number'], 'batch': m[1], 'worker': m[2], 'repo': repo,
                        'branch': p['headRefName'], 'claim': claim})
        return out

    def view(self):
        """Fetch and return [(batch, tasks, label, holder, prs, results)]."""
        self.fetch()
        claims, results = self.main_claims(), self.main_results()
        prs = self.open_prs()
        rows = []
        for bid, b in sorted(self.batches().items()):
            mine = [p for p in prs if p['batch'] == bid]
            label, holder = resolve(b['task_keys'], claims.get(bid, []), mine, results.get(bid, {}))
            rows.append((bid, b['task_keys'], label, holder, mine, results.get(bid, {})))
        return rows

    # -- writing -------------------------------------------------------------------------
    def branch(self, bid):
        return f'ledger/{bid}/{self.worker}'

    def claim_rel(self, bid):
        return f'ledger/claims/{bid}/{self.worker}.json'

    def fork(self):
        if os.environ.get('DBT_BENCH_FORK'):
            return os.environ['DBT_BENCH_FORK']
        url = self.git('remote', 'get-url', 'origin').stdout.strip()
        return re.search(r'github\.com[:/](.+?)(?:\.git)?$', url)[1]

    def commit(self, bid, message, mutate, start=None):
        """Apply mutate(path) -> [changed rel paths] on the right branch, commit and push.

        pr mode: the branch ledger/<batch>/<worker> on your fork (created from upstream main
        when `start` is given). direct mode: main, retried when the push is rejected.
        """
        for _ in range(6):
            if self.mode == 'pr':
                if start:
                    self.fetch()
                    self.git('checkout', '--quiet', '-B', self.branch(bid), self.ref)
                else:
                    self.git('fetch', '--quiet', 'origin', self.branch(bid))
                    self.git('checkout', '--quiet', '-B', self.branch(bid), f'origin/{self.branch(bid)}')
            else:
                self.fetch()
                self.git('checkout', '--quiet', '-B', 'main', self.ref)
            changed = mutate(self.path)
            if not changed:
                return
            self.git('add', '--', *changed)
            self.git('commit', '--quiet', '-m', message)
            target = self.branch(bid) if self.mode == 'pr' else 'main'
            if self.git('push', '--quiet', 'origin', f'HEAD:refs/heads/{target}', check=False).returncode == 0:
                return
            if self.mode == 'pr':
                raise RuntimeError('push to your fork was rejected; check the branch by hand')
        raise RuntimeError('push to main was rejected repeatedly; try again later')

    def my_pr(self, bid):
        for p in self.open_prs():
            if p['batch'] == bid and p['worker'] == self.worker:
                return p
        return None


def write_json(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    return rel


def holder_is_me(L, holder):
    if not holder or holder.get('worker') != L.worker:
        return False
    return holder['via'] == 'main' if L.mode == 'direct' else holder['via'].startswith('pr#')


# -- commands ------------------------------------------------------------------------------
def cmd_status(L, a):
    for bid, tasks, label, holder, prs, results in L.view():
        who = f"{holder['worker']} via {holder['via']} ({holder.get('platform', '?')})" if holder else '-'
        job = f" job {holder['slurm_job']}" if holder and holder.get('slurm_job') else ''
        others = [p['number'] for p in prs if not holder or holder.get('pr') != p['number']]
        extra = f'  other open PRs: {others}' if others else ''
        print(f'{bid}  {label:<17} results {len(results)}/{len(tasks)}  {who}{job}{extra}')


def claim_next(L, platform, no_takeover=False):
    """Claim the first free batch (or take over a queued one); return {'batch', 'pr', 'took_over'}."""
    if L.mode != 'pr':
        # Claim order must come from one authority. PR numbers are assigned by GitHub in
        # creation order; self-reported timestamps on main are not. Maintainers claim new
        # batches with --mode pr too (a branch in the upstream repository works).
        raise Conflict('claim new batches with --mode pr; direct mode only updates batches you already hold on main')
    skip = set()
    for _ in range(10):
        rows = [r for r in L.view() if r[0] not in skip]
        free = [r for r in rows if r[2] == 'free']
        taken = [r for r in rows if r[2] == 'queued' and r[3]['via'] == 'main'
                 and r[3]['worker'] != L.worker and not no_takeover]
        if not free and not taken:
            raise Conflict('no batch is free')
        bid, _, label, holder, _, _ = (free or taken)[0]
        t = now()
        claim = {'batch': bid, 'worker': L.worker, 'platform': platform, 'state': 'claimed',
                 'claimed_at': iso(t), 'lease_until': iso(t + dt.timedelta(hours=LEASE_HOURS['claimed']))}
        if label == 'queued':
            claim['took_over_from'] = {'worker': holder['worker'], 'slurm_job': holder.get('slurm_job')}
        L.commit(bid, f'ledger: claim {bid} ({L.worker})',
                 lambda root: [write_json(root, L.claim_rel(bid), claim)], start=True)
        gh('pr', 'create', '--repo', UPSTREAM, '--base', 'main', '--draft',
           '--head', f"{L.fork().split('/')[0]}:{L.branch(bid)}",
           '--title', f'ledger: {bid} ({L.worker})',
           '--body', f'Claim and results for {bid} by worker `{L.worker}` on `{platform}`. '
                     'Opened by scripts/worker.py; see AGENTS.md.')
        _, _, label2, holder2, _, _ = next(r for r in L.view() if r[0] == bid)
        if holder_is_me(L, holder2):
            return {'batch': bid, 'pr': holder2.get('pr'), 'took_over': claim.get('took_over_from')}
        # Lost a race: someone else holds the batch now. Withdraw and try the next one.
        p = L.my_pr(bid)
        if p:
            gh('pr', 'close', str(p['number']), '--repo', UPSTREAM, '--delete-branch',
               '--comment', f'Lost the race for {bid}; withdrawn automatically.')
        skip.add(bid)
    raise RuntimeError('could not claim a batch after several races')


def cmd_next(L, a):
    print(json.dumps(claim_next(L, a.platform, a.no_takeover)))


def mine(L, bid):
    """The worker's current claim on the batch, checked against the whole ledger."""
    row = next((r for r in L.view() if r[0] == bid), None)
    if not row:
        raise Conflict(f'unknown batch {bid}')
    _, tasks, label, holder, _, results = row
    if not holder_is_me(L, holder):
        raise Conflict(f'{bid} is held by {holder and (holder["worker"], holder["via"], holder["state"])}, not by you ({label})')
    foreign = [t for t, w in results.items() if w != L.worker]
    if foreign:
        raise Conflict(f'{bid} already has results on main from other workers: {foreign}')
    return holder, tasks


def cmd_check(L, a):
    mine(L, a.batch)
    print(f'{a.batch}: held by you; safe to launch')


def update_claim(L, bid, message, **changes):
    holder, _ = mine(L, bid)
    claim = {k: v for k, v in holder.items() if k not in ('via', 'pr')}

    def mutate(root):
        claim.update(changes)
        return [write_json(root, L.claim_rel(bid), claim)]

    L.commit(bid, message, mutate)
    return claim


def heartbeat(L, bid, state, slurm_job=None):
    holder, _ = mine(L, bid)
    if holder['state'] == 'running' and state == 'queued':
        raise Conflict('a running batch cannot go back to queued')
    t = now()
    changes = {'state': state, 'heartbeat_at': iso(t),
               'lease_until': iso(t + dt.timedelta(hours=LEASE_HOURS[state]))}
    if slurm_job:
        changes['slurm_job'] = slurm_job
    update_claim(L, bid, f'ledger: {bid} {state} ({L.worker})', **changes)


def cmd_heartbeat(L, a):
    heartbeat(L, a.batch, a.state, a.slurm_job)


def cmd_publish(L, a):
    publish(L, a.batch, a.task, a.summary, a.raw)


def publish(L, bid, task, summary_path, raw_path):
    summary = json.loads(Path(summary_path).read_text())
    if summary.get('batch') != bid or summary.get('task_key') != task:
        raise Conflict('summary batch/task_key do not match the arguments')
    _, tasks = mine(L, bid)
    if task not in tasks:
        raise Conflict(f'{task} is not part of {bid}')
    rel = f'ledger/results/{bid}/{task}/summary.json'
    if L.show(rel) is not None:
        raise Conflict(f'{rel} already exists on main')
    raw = Path(raw_path)
    digest = sha256(raw)
    asset = f'{bid}--{task}--{L.worker}--{digest[:12]}{"".join(raw.suffixes)}'
    repo = L.fork() if L.mode == 'pr' else UPSTREAM
    if gh('release', 'view', RAW_RELEASE, '--repo', repo, check=False).returncode:
        gh('release', 'create', RAW_RELEASE, '--repo', repo, '--title', 'Raw run archives',
           '--notes', 'Raw dbt-bench run archives. Measurement data: never use for training.')
    staged = raw.with_name(asset)
    if staged != raw:
        staged.unlink(missing_ok=True)
        os.link(raw, staged)
    up = gh('release', 'upload', RAW_RELEASE, str(staged), '--repo', repo, check=False)
    if staged != raw:
        staged.unlink()
    if up.returncode and 'already exists' not in up.stderr:
        raise RuntimeError(f'raw archive upload failed: {up.stderr.strip()[-300:]}')
    summary.update(worker=L.worker, published_at=iso(now()), raw_archive={
        'name': asset, 'sha256': digest, 'bytes': raw.stat().st_size,
        'url': f'https://github.com/{repo}/releases/download/{RAW_RELEASE}/{asset}'})

    def mutate(root):
        if (root / rel).exists():
            raise Conflict(f'{rel} already exists')
        return [write_json(root, rel, summary)]

    L.commit(bid, f'ledger: results {bid} {task} ({L.worker})', mutate)
    print(f'published {bid}/{task}; raw archive {asset}')


def finish(L, bid, note=None):
    update_claim(L, bid, f'ledger: finish {bid} ({L.worker})', state='finished', finished_at=iso(now()))
    if L.mode == 'pr':
        p = L.my_pr(bid)
        if p:
            gh('pr', 'ready', str(p['number']), '--repo', UPSTREAM)
            gh('pr', 'comment', str(p['number']), '--repo', UPSTREAM, '--body',
               note or f'{bid} finished; ready for the experiment owner to review and merge.')


def cmd_finish(L, a):
    finish(L, a.batch)


def comment(L, bid, body):
    if L.mode == 'pr':
        p = L.my_pr(bid)
        if p:
            gh('pr', 'comment', str(p['number']), '--repo', UPSTREAM, '--body', body)


# -- the unattended loop -------------------------------------------------------------------
CODE = Path(__file__).resolve().parents[1]
ENDED = {'COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'OUT_OF_MEMORY', 'NODE_FAIL', 'PREEMPTED',
         'BOOT_FAIL', 'DEADLINE', 'GONE'}


def bench(a, *args):
    env = {**os.environ, 'PYTHONPATH': f"{CODE / 'src'}{os.pathsep}{CODE / 'scripts'}",
           'PYTHONDONTWRITEBYTECODE': '1'}
    p = subprocess.run([a.python, str(a.bench), *args], capture_output=True, text=True, env=env)
    if p.returncode:
        raise RuntimeError(f"bench.py {args[0]} failed: {(p.stderr or p.stdout).strip()[-600:]}")
    return p.stdout


def submit(a, command):
    """Run the printed sbatch command, through --submit-via when compute nodes cannot submit."""
    if a.submit_via:
        p = subprocess.run([*a.submit_via.split(), command], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    else:
        p = subprocess.run(command, shell=True, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    lines = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    job = lines[-1].split(';')[0] if lines else ''
    if p.returncode or not job.isdigit():
        raise RuntimeError(f'submission failed: {(p.stderr or p.stdout).strip()[-400:]}')
    return job


def job_state(job):
    p = subprocess.run(['sacct', '-X', '-n', '-P', '-j', job, '-o', 'State'], capture_output=True, text=True)
    state = p.stdout.split()[0] if p.returncode == 0 and p.stdout.split() else ''
    if not state:
        q = subprocess.run(['squeue', '-h', '-j', job, '-o', '%T'], capture_output=True, text=True)
        state = q.stdout.strip() or 'GONE'
    return state


def finalized_tasks(batch_dir):
    """Task keys whose member run has recorded its final launch result."""
    out = []
    for entry in json.loads((Path(batch_dir) / 'manifest.json').read_text())['identity']['members']:
        run = Path(entry['run'])
        try:
            launch = json.loads((run / 'launch-result.json').read_text())
            key = json.loads((run / 'manifest.json').read_text())['identity']['task_key']
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            continue
        if launch.get('finished_at') and launch.get('finalization_status') == 'recorded_after_cleanup':
            out.append(key)
    return out


def cmd_run(L, a):
    """Claim, run and publish batches until none is free, a job fails, or --max-batches is reached.

    Progress is kept in --state, so a restarted loop resumes the batch it was watching.
    """
    state_path = Path(a.state or Path.home() / '.dbt-bench-worker' / f'{L.worker}.json')
    state_path.parent.mkdir(parents=True, exist_ok=True)
    st = json.loads(state_path.read_text()) if state_path.exists() else {'done': 0}

    def save():
        state_path.write_text(json.dumps(st, indent=1, sort_keys=True))

    def say(msg):
        print(f"{iso(now())} {msg}", flush=True)

    while True:
        if not st.get('batch'):
            if a.max_batches and st['done'] >= a.max_batches:
                say(f"stopping: {st['done']} batches done (--max-batches)")
                return
            try:
                got = claim_next(L, a.platform, a.no_takeover)
            except Conflict as e:
                say(f'stopping: {e}')
                return
            bid = got['batch']
            batch_dir = Path(a.batch_root).resolve() / f"{bid}--{L.worker}--{now().strftime('%Y%m%dT%H%M%SZ')}"
            st.update(batch=bid, dir=str(batch_dir), job=None, published=[], heartbeat_at=None)
            save()
            say(f"claimed {bid} (PR {got['pr']}, took over {got['took_over']}); preparing {batch_dir}")
            try:
                bench(a, 'prepare', '--batch-id', bid, '--output', str(batch_dir))
                bench(a, 'check', '--batch', str(batch_dir))
                mine(L, bid)                               # check the ledger right before submitting
                command = bench(a, 'sbatch-command', '--batch', str(batch_dir)).strip()
                st['job'] = submit(a, command)
            except (RuntimeError, Conflict) as e:
                # Nothing ran: give the batch back and stop, so a broken setup cannot churn PRs.
                say(f'stopping: could not launch {bid}: {e}')
                if not isinstance(e, Conflict):
                    comment(L, bid, f'Could not launch {bid}; releasing it. Error: {str(e)[-500:]}')
                    p = L.my_pr(bid)
                    if p:
                        gh('pr', 'close', str(p['number']), '--repo', UPSTREAM, '--delete-branch')
                st = {'done': st['done']}
                save()
                sys.exit(4)
            save()
            heartbeat(L, bid, 'queued', st['job'])
            st['heartbeat_at'] = iso(now())
            save()
            say(f"submitted {bid} as job {st['job']}")
        bid, job = st['batch'], st['job']
        slurm = job_state(job)
        holder, tasks = mine(L, bid)
        if slurm == 'RUNNING' and (holder['state'] != 'running'
                                   or now() - parse(st['heartbeat_at']) > dt.timedelta(hours=2)):
            heartbeat(L, bid, 'running')
            st['heartbeat_at'] = iso(now())
            save()
        for task in finalized_tasks(st['dir']):
            if task in st['published']:
                continue
            out = Path(a.batch_root).resolve() / 'exports' / f"{bid}--{task}--{now().strftime('%Y%m%dT%H%M%SZ')}"
            try:
                receipt = json.loads(bench(a, 'export', '--batch', st['dir'], '--task', task, '--output', str(out)))
            except RuntimeError as e:
                say(f'export of {bid}/{task} failed; will retry: {str(e)[-300:]}')
                continue
            publish(L, bid, task, receipt['summary'], receipt['archive'])
            st['published'].append(task)
            save()
            say(f'published {bid}/{task}')
        if slurm in ENDED:
            if set(tasks) <= set(st['published']):
                finish(L, bid)
                say(f'finished {bid}')
                st = {'done': st['done'] + 1}
                save()
                continue
            missing = sorted(set(tasks) - set(st['published']))
            comment(L, bid, f'Job {job} ended as {slurm} with {len(missing)} task(s) unpublished: {missing}. '
                            'Stopping without resubmitting; the experiment owner decides about reruns.')
            say(f'stopping: job {job} ended as {slurm}; unpublished {missing}; see the PR')
            sys.exit(4)
        time.sleep(a.poll)


def cmd_release(L, a):
    holder, _ = mine(L, a.batch)
    if holder['state'] == 'running':
        raise Conflict('a running batch cannot be released; open an issue for the experiment owner')
    if L.mode == 'pr':
        p = L.my_pr(a.batch)
        gh('pr', 'close', str(p['number']), '--repo', UPSTREAM, '--delete-branch', '--comment',
           f'Released by {L.worker}: {a.reason}')
    else:
        update_claim(L, a.batch, f'ledger: release {a.batch} ({L.worker})',
                     state='released', released_at=iso(now()), release_reason=a.reason)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument('--ledger', default=os.environ.get('DBT_BENCH_LEDGER'),
                   help='dedicated ledger clone (or set DBT_BENCH_LEDGER)')
    p.add_argument('--mode', choices=['pr', 'direct'], default=os.environ.get('DBT_BENCH_LEDGER_MODE', 'pr'))
    p.add_argument('--worker', default=os.environ.get('DBT_BENCH_WORKER'),
                   help='your worker name, <github-user>-<host> (or set DBT_BENCH_WORKER)')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    s = sub.add_parser('next')
    s.add_argument('--platform', required=True, help='e.g. linux-amd64/H100-80GB/apptainer')
    s.add_argument('--no-takeover', action='store_true')
    for name in ('check', 'heartbeat', 'finish', 'release'):
        s = sub.add_parser(name)
        s.add_argument('batch')
        if name == 'heartbeat':
            s.add_argument('--state', required=True, choices=['queued', 'running'])
            s.add_argument('--slurm-job')
        if name == 'release':
            s.add_argument('--reason', required=True)
    s = sub.add_parser('publish')
    s.add_argument('batch')
    s.add_argument('task')
    s.add_argument('summary')
    s.add_argument('raw')
    s = sub.add_parser('run', help='claim, run and publish batches unattended')
    s.add_argument('--platform', required=True, help='e.g. linux-amd64/H100-80GB/apptainer')
    s.add_argument('--python', required=True, help='the runtime Python from configs/local.toml')
    s.add_argument('--batch-root', required=True, help='where prepared batch directories go (e.g. on SCRATCH)')
    s.add_argument('--submit-via', default='', help="prefix for submitting, e.g. 'timeout 60 ssh login1'")
    s.add_argument('--bench', default=str(CODE / 'scripts' / 'bench.py'))
    s.add_argument('--state', help='progress file (default ~/.dbt-bench-worker/<worker>.json)')
    s.add_argument('--poll', type=float, default=300)
    s.add_argument('--max-batches', type=int, default=0)
    s.add_argument('--no-takeover', action='store_true')
    a = p.parse_args()
    if not a.ledger:
        p.error('set DBT_BENCH_LEDGER or pass --ledger')
    if a.cmd != 'status' and not a.worker:
        p.error('set DBT_BENCH_WORKER or pass --worker')
    if a.worker and not re.fullmatch(r'[A-Za-z0-9._-]+', a.worker):
        p.error('worker names may use letters, digits, dot, underscore and dash only')
    try:
        globals()[f'cmd_{a.cmd}'](Ledger(a.ledger, a.mode, a.worker), a)
    except Conflict as e:
        print(f'conflict: {e}', file=sys.stderr)
        sys.exit(3)


if __name__ == '__main__':
    main()
