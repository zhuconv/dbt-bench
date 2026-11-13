# Rebuild the locked Qwen installation

Qwen Code 0.26.5, Node 22.23.4 and npm 10.9.9 are fixed for Linux ARM64/glibc. The bundle is not redistributed. The files in this directory and the build scripts are sufficient to rebuild it from public npm, Node and the pinned NVM GitHub revision; no original private installation log is required.

`package-lock.json` pins every npm tarball and its integrity value. Use the replay workflow below (`npm ci`), not a fresh `npm install -g @qwen-code/qwen-code@0.26.5`: the latter can resolve different transitive dependencies. `native-installation.json` contains 17 observed versions and 1,608 package-file hashes. `node.lock.json` fixes the official Node archive/binary; `nvm.lock.json` fixes four NVM support files.

## Build on a Linux ARM64 compute node

Use the patched Python installed by the main README. On a clean checkout, keep caches on SCRATCH (if `.cache/qwen-runtime` already exists, keep its hash-checked contents):

```bash
runtime_python="$SCRATCH/dbt-bench-runtime/timeout-v2/venv/bin/python"
qwen_work="$SCRATCH/dbt-bench-qwen-build"
qwen_cache="$SCRATCH/dbt-bench-qwen-cache"
mkdir -p "$qwen_cache" .cache
if [ ! -e .cache/qwen-runtime ] && [ ! -L .cache/qwen-runtime ]; then
  ln -s "$qwen_cache" .cache/qwen-runtime
fi
PYTHONDONTWRITEBYTECODE=1 "$runtime_python" scripts/freeze_qwen_dependencies.py \
  --output-dir "$qwen_work/installation-v1"
PYTHONDONTWRITEBYTECODE=1 "$runtime_python" scripts/build_qwen_bundle.py \
  --installation "$qwen_work/installation-v1" \
  --output-dir "$qwen_work/bundle-v1"
```

Use new output directories each time. The first command downloads the locked official artifacts, runs two independent installations (the second uses npm offline mode), verifies versions, and compares every declared package file to the portable native receipt. The historical `native_observation` paths in that receipt are provenance, not files required by this replay. With an already populated cache, `freeze_qwen_dependencies.py --offline` can also perform the first install without npm network access. This does not claim network isolation.

The builder verifies those results, creates the native NVM layout, normalizes staging permissions to the reviewed modes (directories and executables 0755, other files 0644, symlinks unchanged), and emits:

- `bundle.json`: local build provenance and checksums;
- `bundle.tar.gz`: the complete native runtime tree;
- `inventory.json`: all 7,615 entries, including paths, file hashes, sizes, modes and symlink targets;
- `install_bundle.py`: the unchanged standalone installer.

It refuses output unless the inventory, archive and installer exactly match the existing hashes in `sources.lock.yaml`. Permissions are checked, not ignored. This fixes the previous dependence on the builder's umask. Two actual rebuilds under umask 077 and 022 produced the original archive and inventory hashes. Only the manifest hash changes with build provenance. See [the CPU verification and identity review](../../docs/qwen-bundle-rebuild.md).

Set `qwen_bundle` in `configs/local.toml` to the absolute path of your new `bundle.json`. `scripts/bench.py prepare` verifies the dependency locks and all three payload hashes, then records the **actual local manifest SHA256** in native Qwen Code kwargs. No modification of the locked Harbor adapter is needed. Do not map a rebuilt manifest to the historical manifest asset ID in `release/assets.json`; the direct `qwen_bundle` setting supplies it.

For the older general task preparation entry point, pass `prepare config --qwen-bundle PATH/bundle.json`. The default-only `check_runtime.py --require-locked-installation` expects the location declared in `sources.lock.yaml`; it is not a validator for an arbitrary output directory. Portable `bench.py prepare/check` validates the configured bundle.

## What remains fixed

The npm libc metadata repair is already included in the committed lock and does not run a new version resolution during replay. Generated `.bin` links and npm's hidden lock metadata are omitted only from the package-layout comparison; the assembled runtime's full inventory, executable permissions and symlink targets are checked exactly. No blanket package-file or archive-hash exemption is introduced.

The installer verifies before installation, refuses a changed destination, and retains the reviewed Apptainer EXDEV fallback. Reconstruction and CLI version checks send no model requests. New local manifest/execution hashes remain distinct from historical ones, while identical payloads receive the same installation-content ID. This is supporting evidence for a future controlled cross-account import, not automatic permission to merge results or count duplicate attempt slots. Measurement trajectories remain prohibited from training use.

## Linux x64

Use `python scripts/build_qwen_platform.py --cache /data/cache/qwen --output /data/qwen-x64` with Python 3.13. Separate locks in `linux-x64/` preserve Qwen 0.26.5, Node 22.23.4 and npm 10.9.9. The builder checks upstream npm integrity, full file inventory and deterministic archive hash. The native adapter checks target host and CLI versions before any attempt. See [H100 setup](../../docs/h100-worker.md). ARM locks and the original builder remain unchanged.
