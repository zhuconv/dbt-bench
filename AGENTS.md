# AGENTS.md — dbt-bench measurement workers

You are a worker agent helping measure task difficulty for dbt-bench. Workers on different servers take batches from one shared queue, kept in this repository under `ledger/`.

Each batch you take is a pull request from your fork:
- opening the PR claims the batch;
- pushing to it publishes your results;
- the experiment owner merging it accepts them.

Follow this file exactly. When it and your own judgement disagree, stop and ask the experiment owner in an issue. [docs/distributed-workers.md](docs/distributed-workers.md) draws the whole workflow as a sequence diagram.

**For people joining as a worker:** three things only you can do.
1. Get a Slurm allocation with H100 80 GB (or GH200) GPUs and Apptainer.
2. Run `gh auth login`.
3. Start your agent (Codex or Claude Code) in a clone of this repository and tell it: "Read AGENTS.md and work as a dbt-bench measurement worker."

The agent does the rest.

Status (2026-11-12):
- linux-arm64 (GH200) and linux-amd64 (H100) images are published. Each amd64 image passed a CPU reference smoke and a Qwen Code CLI check inside the image on an x86 GitHub runner. The x86 inference lock installs and imports there too.
- Nobody has started the model server on an H100 yet, so your first batch is also that check. If its job fails while the model server starts (for example, out of GPU memory), `run` stops. Report it in your PR, and do not change the server arguments.

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
   - fill in `configs/local.toml`, including `platform` and `asset_map`;
   - build the Qwen Code bundle;
   - download the model.

   On x86_64 (H100), also follow [docs/h100-worker.md](docs/h100-worker.md): the x64 runtime patch, the x86 inference lock, the x64 Qwen Code bundle and `configs/local.amd64.example.toml`. Do all of this on compute nodes.

   You do not download task trees or images by hand: `worker.py run` fetches each batch's assets for your architecture into the directory next to `asset_map`, checks every SHA256 and records them in `asset_map`.
4. Pick a platform string such as `linux-amd64/H100-80GB/apptainer`.

## The loop

Run the loop unattended with `worker.py run`. A batch takes many hours, longer than an agent session stays awake:

```bash
python scripts/worker.py run --platform P \
  --python "$SCRATCH/dbt-bench-runtime/<runtime>/venv/bin/python" \
  --batch-root "$SCRATCH/dbt-bench-batches" \
  --submit-via 'timeout 60 ssh login1'   # only where compute nodes cannot run sbatch
```

Where to run it and what it does:
- **Where:** somewhere that stays up, such as a `tmux` session on a login node (if your site allows light background processes there) or a small long-running CPU job.
- **What:** it does steps 1–8 below on its own, including downloading each batch's assets (pass `--no-assets` if you manage `asset_map` by hand).
- **When it stops:**
  - when no batch is free (exit 0);
  - after `--max-batches`;
  - when a job fails (exit 4). It then has already published every finished task and commented on the PR. Report this to the experiment owner and do not resubmit.
- **Restarting:** progress is kept in `~/.dbt-bench-worker/<worker>.json`, so a restarted `run` resumes the batch it was watching.

The steps below are what `run` does. You can also run them by hand. Commands are `python scripts/worker.py ...`, run from your working checkout with the two variables above set.

1. **Claim a batch:** `worker.py next --platform P`.
   - It reads the ledger on main and every open ledger PR, so work that others pushed while you were busy is respected.
   - It takes the first free batch. If none is free, it takes over a batch the owner has only queued. It then opens a draft PR named `ledger: <batch> (<worker>)` and prints the batch.
   - If two workers race for one batch, the lower PR number keeps it, and the tool closes the other PR and tries the next batch.
2. **Fetch its assets, then prepare and check the batch.**
   - `worker.py assets B --asset-map <asset_map from configs/local.toml> --dest <download dir>` downloads the batch's task trees and images for this machine, checks every SHA256 and updates `asset_map`.
   - Then prepare and check the batch as in README.md ("Prepare and run one assigned batch").
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
