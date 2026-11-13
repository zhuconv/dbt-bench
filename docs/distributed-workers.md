# Distributed workers

Several people run measurement batches on their own servers. The diagram shows what happens to a batch between three places:

- Person 1, the experiment owner on TACC Vista: the owner, Codex and a sync script run by Claude.
- GitHub (this repository): `main`, ledger pull requests, the `ledger-check` workflow and releases.
- Person 2, a worker on its own H100 server: an agent following [AGENTS.md](../AGENTS.md), with its own fork.

Person 1 and Person 2 never talk to each other directly. Every claim, result and acceptance goes through GitHub. In the diagram:

- solid arrows write or act;
- dashed arrows read or notify;
- shaded blocks are the takeover and the race.

```mermaid
sequenceDiagram
    participant P1 as Person 1 · owner on Vista<br/>owner · Codex · sync script
    participant GH as GitHub · zhuconv/dbt-bench<br/>main · PRs · Actions · releases
    participant P2 as Person 2 · worker on H100<br/>worker agent · own fork

    Note over P1,P2: Start: all 35 batches are claimed on main by owner-vista
    P1->>GH: 35 Vista jobs queued, claimed on main as queued

    rect rgba(245, 158, 11, 0.12)
    Note over P1,P2: Case 1: Person 2 takes over a batch the owner only queued
    P2-->>GH: next: read main and every open ledger PR
    Note over P2: no free batch, so pick the queued batch-07
    P2->>GH: open draft PR #12, which is the claim (effective at once)
    GH-->>P1: sync script (every 5 min) sees batch-07 taken over
    Note over P1: job still pending: check its experiment id, then scancel
    P1->>GH: main: owner claim on batch-07 set to released
    P2-->>GH: check: batch-07 is still mine
    Note over P2: sbatch on its own cluster
    P2->>GH: heartbeat queued, then running (pushed to PR #12)
    P2->>GH: after each task: summary to PR #12, raw archive to fork release raw-v1
    P2->>GH: finish: PR #12 marked ready for review
    Note over GH: ledger-check: only its own batch, raw archive SHA256 verified
    GH-->>P1: notification: check passed
    P1->>GH: owner merges PR #12, which accepts the results
    end

    Note over P1,P2: Case 2: an owner batch that nobody took over (no PR)
    Note over P1: the Vista job starts running
    P1->>GH: commit to main: claim set to running, no takeover from now on
    P1->>GH: after each task: summary to main, raw archive to release raw-v1

    rect rgba(245, 158, 11, 0.12)
    Note over P1,P2: Case 3: race, the owner job started first
    P2->>GH: open a PR to take over batch-09
    P1->>GH: job already running: main claim set to running, which wins
    P2-->>GH: check: conflict
    Note over P2: do not submit, close the PR, run next again
    end

    Note over P1,P2: Wrap-up, on the owner side only
    GH-->>P1: Codex imports accepted results, downloads archives, checks SHA256
    Note over P1: audit (native and audit) → reconcile → rerun decisions → split draft for the owner to confirm
```

## Who holds a batch

The rules below are applied in order:

1. When every task of the batch has a summary on `main`, the batch is finished.
2. Otherwise a running claim holds it.
3. Otherwise a fresh `claimed` claim on `main` holds it.
4. Otherwise the lowest-numbered open ledger PR holds it. This is how a worker takes over a batch the owner has only queued. The owner's sync script then cancels the pending Vista job and releases the owner claim.

The rules are implemented in `scripts/worker.py`. The ledger layout is in [ledger/README.md](../ledger/README.md), and the PR check is `scripts/ledger_check.py`. Merging a PR accepts its raw results only; joint native and audit success is decided centrally afterwards.
