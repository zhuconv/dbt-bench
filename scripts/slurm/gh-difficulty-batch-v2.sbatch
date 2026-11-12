#!/bin/bash
#SBATCH --job-name=dbt-difficulty-batch-v2
#SBATCH --partition=gh
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=48:00:00
#SBATCH --time-min=24:00:00
#SBATCH --signal=B:TERM@180

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit from the repository root}"
unset LD_PRELOAD
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONPATH="$PWD/src:$PWD/scripts"
export PATH="$PWD/runtime/.venv/bin:/opt/apps/tacc-apptainer/1.4.1/bin:$PATH"
export HF_HOME="$PWD/.cache/huggingface"
export APPTAINER_CACHEDIR="$PWD/.cache/apptainer"
# The runner assigns per-batch VLLM, Triton and FlashInfer compilation caches.
exec runtime/.venv/bin/python scripts/run_difficulty_gh_batch_v2.py --batch "${1:?Prepared batch directory required}"
