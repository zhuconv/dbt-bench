# DBT Bench

Pinned Harbor + Qwen Code evaluation for ADE-bench, Spider2-DBT and data-eng-bench (DEB), with separate native and audit scores. Licensed under Apache-2.0; see [License](#license).

The original catalog contains **135 tasks**: ADE 16, Spider 55, DEB 64. The current qualified measurement population contains **67 tasks**: ADE 15, Spider 16, DEB 36. This is a candidate count, not 67 completed difficulty measurements or training-ready traces. See `catalog/measurement-tasks.json`. The new train/test split has not been drawn or frozen. Existing splits are retained as history.

**All difficulty measurements and their trajectories are prohibited from training use.** Four independent valid attempts per task are used only for stratification. A success requires native and audit acceptance of the same submission.

## Supported environment

The tested deployment is TACC Vista GH200, Linux aarch64, Apptainer 1.4.1, Slurm, Harbor 0.25.0 and Qwen Code 0.26.5. The model is Qwen3.6-35B-A3B at the revision in `inference/qwen3.6-35b-a3b.json`; vLLM 0.19.0 and its dependencies are locked separately. Docker remains the benchmark reference backend; the current measurement release uses the reviewed Apptainer images. Other architectures/backends require separate qualification.

Run installations, agents, containers and inference on compute nodes. Use login nodes only for short Slurm commands. Obtain your own allocation membership; no project credential is required for the public model or DuckDB warehouse. Optional provider/registry credentials belong in your environment, never this repository.

## Install the two locked runtimes

Use `uv` on a compute node. Keep large caches and environments on SCRATCH:

```bash
export UV_CACHE_DIR="$SCRATCH/dbt-bench-cache/uv"
export UV_PROJECT_ENVIRONMENT="$SCRATCH/dbt-bench-runtime/base"
uv sync --locked --project runtime --python 3.13
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts   "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/install_release_runtime.py   --output "$SCRATCH/dbt-bench-runtime/timeout-v2"
unset UV_PROJECT_ENVIRONMENT
UV_PROJECT_ENVIRONMENT="$SCRATCH/dbt-bench-inference"   uv sync --locked --project inference --python 3.12
```

The installer creates a new runtime, applies exact preimage/postimage patches, and verifies all 515 Harbor package files. It will not overwrite a sealed runtime. The host interpreter path is a deployment coordinate; the package contents and dependency locks remain fixed. The inference lock is aarch64-specific and must be validated on the target GPU.

## Configure and acquire external assets

```bash
cp configs/local.example.toml configs/local.toml
export DBT_BENCH_LOCAL_CONFIG="$PWD/configs/local.toml"
```

Fill your allocation, artifact/cache locations, patched Python, vLLM executable, model snapshot, Qwen bundle and CUDA toolchain. These fields only control deployment coordinates. Model settings, seeds, concurrency, attempts and native budgets come from locked protocol files.

`release/assets.json` lists **171 external objects**: 33 SIF images, 67 prepared task trees, 67 qualification records, and 4 Qwen Code bundle files. The images are published on GHCR. The task trees and the qualification records are attachments to the `assets-v1-20261112` GitHub Release. The Qwen Code bundle is not redistributed; build it yourself as described below. The 67 qualification records are evidence indexes, not a complete audit bundle. The published bytes are exactly the measured ones, so historical build paths inside them were left in place. See [the publication review](docs/asset-publication.md).

Images use one package, `ghcr.io/zhuconv/dbt-bench-images`, with a distinct tag `v1-linux-arm64-<full SIF SHA256>` for each image. Pull and verify an image as follows (replace the digest with the selected image's `sha256` from `release/assets.json`):

```bash
image_sha256='<64 hexadecimal characters from release/assets.json>'
image_path="$SCRATCH/dbt-bench-assets/image-${image_sha256}.sif"
mkdir -p "$(dirname "$image_path")"
apptainer pull "$image_path" "oras://ghcr.io/zhuconv/dbt-bench-images:v1-linux-arm64-${image_sha256}"
printf '%s  %s\n' "$image_sha256" "$image_path" | sha256sum --check --strict
```

The SIF SHA256 is the image identity; the OCI manifest digest is a different value and is not used for verification. Task archives and the qualification index archive are attachments to the `assets-v1-20261112` Release. Check both the downloaded archive SHA256 and the extracted task/file hashes; commands are in [asset acquisition](docs/artifacts.md). Create the private JSON `asset_map` named in local config, mapping each asset ID to its verified local file/directory. Missing assets remain blocking errors.

The Qwen Code bundle (Qwen Code 0.26.5, Node 22.23.4 and npm 10.9.9 for Linux ARM64) is not redistributed. Follow the [locked rebuild commands](runtime/qwen-code/README.md) using npm, Node and pinned NVM downloads. Two independent npm installations must match all 1,608 native package files. The builder normalizes the reviewed permissions and requires the complete archive, inventory and installer to match their original hashes. Set `qwen_bundle` to your new `bundle.json`; preparation records its actual manifest hash after verifying the unchanged payloads. The manifest and execution identity may differ because of local provenance. [CPU verification and merge limits](docs/qwen-bundle-rebuild.md) explain the distinction.

For the public model, `scripts/prefetch_smoke_model.py` downloads the exact pinned revision and checks every file against HF metadata. The full expected inventory is also in `release/model-files.json`. Set `model_snapshot` to the verified snapshot path. No model request is sent during download.

## Prepare and run one assigned batch

Distributed workers take batches from the shared ledger and publish results as described in [AGENTS.md](AGENTS.md). The steps below run one batch.

The 35 prepared 24h batch declarations are in `release/batches.json`. They cover 64 still-unstarted tasks; the two completed ADE pilots and repeat-purchase recovery are intentionally excluded. Coordinate whole-batch ownership with the experiment owner **before** running to prevent duplicate attempts. Do not select batches based on model outcomes.

Use the patched Python specified in local config for every command below:

```bash
export PYTHONPATH="$PWD/src:$PWD/scripts"
export PYTHONDONTWRITEBYTECODE=1
runtime_python="$SCRATCH/dbt-bench-runtime/timeout-v2/venv/bin/python"
batch_dir="$SCRATCH/dbt-bench-storage/artifacts/my-assigned-batch-01"
"$runtime_python" scripts/bench.py prepare --batch-id batch-01 --output "$batch_dir"
"$runtime_python" scripts/bench.py check --batch "$batch_dir"
"$runtime_python" scripts/bench.py sbatch-command --batch "$batch_dir" --test-only
"$runtime_python" scripts/bench.py sbatch-command --batch "$batch_dir"
```

The last two commands **print** commands. Execute the test-only command on login1 first, and submit the printed non-test command only after authorization. From a compute node use `timeout 60 ssh login1 'cd /your/release; sbatch ...' < /dev/null`; do not use `BatchMode=yes`. The wrapper launches one model service, then native `harbor run -c` jobs sequentially; Harbor owns every clean trial. No extra smoke generation or automatic model retry is added.

Task relocation changes only the declared image coordinate in `task.toml`; the canonical and relocated hashes are recorded and parsed semantic content is checked. Native test bytes, instructions, frozen dbt packages, collect commands and reference assets are preserved. Local configuration is sealed into the new execution identity. It does not impersonate the historical run identity.

## Check results

```bash
"$runtime_python" scripts/bench.py inspect --batch "$batch_dir" > result-index.json
```

The index retains raw reward maps, exceptions and missing slots; audit and joint success remain `null` until the same captured submission is audited. Raw Qwen/Harbor records and pre-verifier captures remain under each member's `jobs/` directory. `launch-result.json` describes execution completion, **not** audit success. Review `interruption.json` before assigning failure or replacement slots. Never restart a used output directory.

Qualified task checkers are included under `validation/`; full contracts, expected outputs, qualification evidence and immutable audit request specifications are external assets. The historical audit/reconcile scripts require those exact artifacts. The release does not synthesize missing audit receipts, reinterpret native successes as joint successes, or implement cross-account result import. Return raw jobs, captures, manifests and later audit receipts to the experiment owner for adjudication; cross-account import tooling is deferred.

## CPU regression checks

```bash
PYTHONPATH=src:scripts "$runtime_python" -m unittest   tests.test_release_portability tests.test_comparison tests.test_validation   tests.test_reporting tests.test_training_purpose_exclusion   tests.test_runtime_timeout_v2 tests.test_runtime_patch_chain -v
```

These checks use small fixtures and do not count as benchmark runs. Older task-specific tests/scripts may additionally require the external qualification archive; source is retained for review and reuse. The release's portable GPU path has CPU preparation/equivalence coverage, but no new GPU run was launched from this export. The active experiment's separate runtime GPU validation is tracked independently.

See [experiment protocol](docs/experiment-protocol.md), [release scope](docs/release-scope.md), and [asset acquisition](docs/artifacts.md).

## License

The code, patches, checkers and documentation in this repository are licensed under the Apache License 2.0 (`LICENSE`). Task content derives from ADE-bench (Apache-2.0), Spider 2.0 / Spider2-DBT (MIT) and data-eng-bench (Apache-2.0). These and the other third-party components keep their own licenses; see `NOTICE` and `release/third-party-notices/`. The published images are based on Debian 13. `release/third-party-notices/os-sources-unique.tsv` lists the Debian source package and version behind every installed package, with a snapshot.debian.org link; `os-source-packages.tsv` gives the per-image list.
