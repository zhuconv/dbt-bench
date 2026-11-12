# External artifacts

Git contains source, locks, recipes, checker code, small manifests and synthetic tests. It excludes `tasks/`, `jobs/`, `artifacts/`, `reports/`, caches, all nested virtual environments, SIFs, database files, archives, model weights and full qualification ledgers.

`release/assets.json` is the required-object index. Each image/file is SHA256-addressed; task trees use `dbt_bench.io.tree_hash`, which includes relative names, bytes, symlinks and executable bits. Preserve modes during transfer. `catalog/measurement-tasks.json` binds67 task versions and unchanged verifier hashes. `release/model-files.json` specifies40 model files using SHA256 for LFS and Git-blob SHA1 for ordinary files.

Obtain prepared tasks/SIFs/Qwen payloads/full qualification records through the experiment owner's existing approved shared storage. No public SIF/evidence URL is currently configured. Fill a private JSON mapping of asset ID to local path and point `asset_map` at it. Do not commit the map. For example an ID from `release/assets.json` maps to a file under your own SCRATCH. Preparation rejects missing or changed assets. The current immutable prepared task objects contain historical provenance; their local relocation changes only the reviewed image coordinate and records both hashes.

Public upstream task sources are fixed in `sources.lock.yaml`; `scripts/prepare.py` can retrieve those upstream snapshots. Upstream retrieval alone does not reproduce all reviewed instruction variants, image fixes, frozen packages or qualification evidence. Transfer the locked objects to reproduce this measurement pool. Rebuilding recipes with mutable apt/pip/dbt resolution cannot be represented as the same image.

Public model acquisition uses `scripts/prefetch_smoke_model.py`, the fixed HF repository/revision in `inference/qwen3.6-35b-a3b.json`, and exact file checksums. The download does not generate text. Node/npm/Qwen are fixed by `runtime/qwen-code/` locks; for exact offline task installation use the reviewed bundle and its manifest/archive/installer/inventory hashes in `release/assets.json`. Do not freshly resolve task dbt packages.

Frozen ledgers, audit contracts/expected artifacts and original receipts stay external. The checker source in Git is reviewable but does not replace the missing qualification evidence. A path-sanitized copy is a new release artifact with its own hash, never a replacement for a historical evidence object.

Your own TACC login and allocation membership are needed for Slurm. Current public downloads/localhost model serving require no project secret; use your own optional HF/registry credentials if required by your access. DuckDB tasks need no Snowflake account. No keys, tokens or email credentials should be placed in local config or result manifests.
