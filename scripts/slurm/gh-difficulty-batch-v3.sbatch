#!/bin/bash
#SBATCH --job-name=dbt-difficulty-runtime-v3
#SBATCH --partition=gh
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=24:00:00
#SBATCH --signal=B:TERM@180

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit from repository root}"
unset LD_PRELOAD
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONPATH="$PWD/src:$PWD/scripts"
export HF_HOME="$PWD/.cache/huggingface"
export APPTAINER_CACHEDIR="$PWD/.cache/apptainer"
runtime_python="${2:?Sealed independent runtime Python required}"
exec "$runtime_python" scripts/run_difficulty_gh_batch_v3.py --batch "${1:?Prepared batch directory required}"
