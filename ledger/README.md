# Ledger

The shared queue of the distributed difficulty measurement. Workers follow [AGENTS.md](../AGENTS.md) and change these files only through `scripts/worker.py`. [docs/distributed-workers.md](../docs/distributed-workers.md) has a sequence diagram of the workflow.

- `claims/<batch>/<worker>.json`: one worker's claim on a batch. It records the platform, state (`claimed`, `queued`, `running`, `finished` or `released`), lease and Slurm job.
- `results/<batch>/<task>/summary.json`: one task's raw results, as exported by the worker that ran it. It is added once and never edited. Each summary names its raw archive (a `raw-v1` release asset) with URL and SHA256.
- The work units themselves are `release/batches.json`.

Who holds a batch:
- complete results, or a `finished` claim on main, mean finished (the owner's own runs publish no summaries);
- otherwise a running claim holds;
- otherwise an open PR whose claim is `finished` holds as `review`, until the owner merges or closes it;
- otherwise a fresh `claimed` claim on main holds;
- otherwise the lowest-numbered open ledger PR holds. This is how a PR takes over a batch that the owner has only queued.

The owner's own batches were submitted on TACC Vista on 2026-11-12 and are claimed on main by `owner-vista`. A merged PR means the experiment owner accepted its results; joint native and audit success is decided centrally afterwards. Measurement data must never be used for training.
