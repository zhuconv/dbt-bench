#!/bin/bash
#SBATCH --job-name=dbt-native-validation
#SBATCH --partition=gg
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --time=02:00:00

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit from the repository root}"
unset LD_PRELOAD
export PATH="$PWD/runtime/.venv/bin:/opt/apps/tacc-apptainer/1.4.1/bin:$PATH"
export APPTAINER_CACHEDIR="$PWD/.cache/apptainer"
export APPTAINER_TMPDIR="/tmp/dbt-bench-validation-${SLURM_JOB_ID}"
export OMP_NUM_THREADS=8
export PYTHONUNBUFFERED=1
mkdir -p "$APPTAINER_TMPDIR"
# An explicit prepared config only; Harbor owns attempts, concurrency and cleanup.
validation_config="${DBT_BENCH_VALIDATION_CONFIG:?Set this to a newly prepared Harbor config before submission}"
test -f "$validation_config"
exec runtime/.venv/bin/harbor run -c "$validation_config"
