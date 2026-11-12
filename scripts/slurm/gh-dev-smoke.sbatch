#!/bin/bash
#SBATCH --job-name=dbt-qwen-smoke
#SBATCH --partition=gh-dev
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=02:00:00

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit from the repository root}"
unset LD_PRELOAD
export PATH="$PWD/runtime/.venv/bin:/opt/apps/tacc-apptainer/1.4.1/bin:$PATH"
export UV_CACHE_DIR="$PWD/.cache/uv"
export HF_HOME="$PWD/.cache/huggingface"
export VLLM_CACHE_ROOT="$PWD/.cache/vllm"
export APPTAINER_CACHEDIR="$PWD/.cache/apptainer"
export APPTAINER_TMPDIR="/tmp/dbt-bench-${SLURM_JOB_ID}"
export OMP_NUM_THREADS=8
export PYTHONUNBUFFERED=1
mkdir -p "$APPTAINER_TMPDIR" "artifacts/slurm/$SLURM_JOB_ID"
if runtime/.venv/bin/python scripts/run_gh_smoke.py; then
    exit 0
else
    smoke_exit=$?
fi
printf '%s\n' "$smoke_exit" > "artifacts/slurm/$SLURM_JOB_ID/failed-exit-code"
printf 'Smoke failed (exit %s). Allocation retained for debugging until time limit or release-allocation marker.\n' "$smoke_exit"
# Retain only this already-requested two-hour allocation; no automatic job retry.
# A debug rerun uses DBT_BENCH_SMOKE_ATTEMPT=1 to preserve the failed invocation.
while [[ ! -f "artifacts/slurm/$SLURM_JOB_ID/release-allocation" ]]; do
    sleep 30
done
exit "$smoke_exit"
