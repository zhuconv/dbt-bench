# AGENTS.md — dbt-bench measurement workers

You are a worker agent helping measure task difficulty for dbt-bench. Workers on different servers take batches from one shared queue, kept in this repository under `ledger/`.

Each batch you take is a pull request from your fork:
- opening the PR claims the batch;
- pushing to it publishes your results;
- the experiment owner merging it accepts them.

Follow this file exactly. When it and your own judgement disagree, stop and ask the experiment owner in an issue. [docs/distributed-workers.md](docs/distributed-workers.md) draws the whole workflow as a sequence diagram.

Status (2026-11-12):
- linux-arm64 (GH200) images are published.
- linux-amd64 (H100) images and the `scripts/bench.py export` command are still being prepared.
- Until `docs/artifacts.md` lists amd64 images and README.md documents `export`, do not start the loop.

## Once per server

1. You need:
   - a GitHub account, with `gh` logged in;
   - your own Slurm allocation with one H100 80 GB or one GH200 per job;
   - Apptainer 1.4 or later, `git` and `uv`.

   You do not need write access to this repository.
2. Fork the repository and make a second clone used only for the ledger. Never use the checkout your jobs run from:
   ```bash
   gh repo fork zhuconv/dbt-bench --clone=false
   gh repo clone <you>/dbt-bench "$HOME/dbt-bench-ledger"   # origin = your fork, upstream = zhuconv/dbt-bench
   export DBT_BENCH_LEDGER="$HOME/dbt-bench-ledger"
   export DBT_BENCH_WORKER="<github-user>-<host>"            # letters, digits, . _ - only
   ```
3. In your working checkout, follow README.md:
   - install the runtimes;
   - fill in `configs/local.toml`;
   - pull and verify the images for your architecture;
   - build the Qwen Code bundle;
   - download the model.

   Do all of this on compute nodes.
4. Pick a platform string such as `linux-amd64/H100-80GB/apptainer`.

## The loop

Commands are `python scripts/worker.py ...`, run from your working checkout with the two variables above set.

1. **Claim a batch:** `worker.py next --platform P`.
   - It reads the ledger on main and every open ledger PR, so work that others pushed while you were busy is respected.
   - It takes the first free batch. If none is free, it takes over a batch the owner has only queued. It then opens a draft PR named `ledger: <batch> (<worker>)` and prints the batch.
   - If two workers race for one batch, the lower PR number keeps it, and the tool closes the other PR and tries the next batch.
2. **Prepare and check the batch** as in README.md ("Prepare and run one assigned batch").
3. **Check before submitting:** run `worker.py check B` right before you submit. If it reports a conflict, do not submit; go back to step 1.
4. **Submit the job**, then run `worker.py heartbeat B --state queued --slurm-job ID`.
5. **When the job starts**, run `worker.py heartbeat B --state running`. Repeat it at least every 2 hours while the job runs; a running claim expires after 6 hours without one.
6. **Publish each task as soon as it finishes**, that is, as soon as its `launch-result.json` appears:
   - export it with `scripts/bench.py export` (see README.md);
   - then run `worker.py publish B TASK summary.json RAW_ARCHIVE`.

   The raw archive goes to the `raw-v1` release of your fork, and the summary is pushed to your PR. A task's results are written once.
7. **When every task is published**, run `worker.py finish B`.
   - This marks the PR ready for review.
   - The `ledger-check` workflow then verifies the files and downloads every raw archive to check its SHA256.
   - The experiment owner reviews and merges.
8. **Go back to step 1.** Never launch a job without a fresh `next` or `check`.

`worker.py status` lists every batch with its state, holder, PR and number of accepted results.

## Rules

- **Run whole batches exactly as declared** in `release/batches.json`.
  - Never change task versions, images, model settings, seeds, budgets or protocol files.
  - Never choose batches by expected outcome.
- **One attempt per slot:**
  - never rerun a task that already has results;
  - never rerun a slot because of its outcome;
  - never edit or delete published summaries or archives.
- **If a job fails, times out or is interrupted, do not resubmit it.** Publish the tasks that finished, keep the raw outputs, and say what happened in your PR. The experiment owner decides about reruns. Never `release` a running batch.
- **Only `worker.py` writes ledger files.**
  - A ledger PR touches nothing but its own claim and result files.
  - Never rewrite or force-push a ledger branch.
  - If `worker.py` reports a conflict, do not work around it: run `next` again, or stop and report.
- **Measurement trajectories must never be used for training or fine-tuning.**
- **Never commit credentials, tokens or personal paths.**
- **Workers do not judge success.** The experiment owner audits every submission centrally: native and audit acceptance of the same submission.
- **When `next` reports `no batch is free`, stop.** Do not invent work.
