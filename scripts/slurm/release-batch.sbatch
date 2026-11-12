#!/bin/bash
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?Submit from release repository root}"
unset LD_PRELOAD
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export PYTHONPATH="$PWD/src:$PWD/scripts"
export DBT_BENCH_LOCAL_CONFIG="${3:?Local configuration required}"
exec "${2:?Patched runtime Python required}" scripts/run_release_batch.py --batch "${1:?Prepared batch required}"
