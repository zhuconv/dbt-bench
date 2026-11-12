#!/bin/bash
#SBATCH --job-name=dbt-difficulty
#SBATCH --partition=gh-dev
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=02:00:00
#SBATCH --signal=B:TERM@180

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit from the repository root}"
unset LD_PRELOAD
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONPATH="$PWD/src:$PWD/scripts"
export PATH="$PWD/runtime/.venv/bin:/opt/apps/tacc-apptainer/1.4.1/bin:$PATH"
export HF_HOME="$PWD/.cache/huggingface"
export VLLM_CACHE_ROOT="$PWD/.cache/vllm"
export APPTAINER_CACHEDIR="$PWD/.cache/apptainer"
exec runtime/.venv/bin/python scripts/run_difficulty_gpu.py --run "${1:?Prepared difficulty run directory required}"
