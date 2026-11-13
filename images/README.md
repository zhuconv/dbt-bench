# Multi-architecture image recipes

The owner accepts linux/amd64 (H100) and linux/arm64 (GH200) measurement results as equivalent. This is a protocol decision dated 2026-11-12, not evidence from a cross-architecture study. Task/checker/model/budget/seed checks still apply.

`Dockerfile.base` is shared between both architectures. `upstreams.lock.json` selects each architecture's manifest digest from the same fixed upstream index (Python 3.11.18 slim trixie or the DEB base). Live `latest` tags are never build inputs. The 33 published SIFs form 5 software groups; `locks/software-groups.json` and the per-group apt and pip requirements preserve their versions. PyPI wheels are installed with `--no-deps --require-hashes`; no dependency resolution or unpinned dbt deps runs occur. Node/npm and yq architecture artifacts have separate upstream hashes. Debian binary names normalize only the architecture suffix. A missing exact binary version is a build failure, never permission to substitute a different version.

Debian occasionally rebuilds a package on one architecture only (a binNMU, version suffix `+bN`). `locks/<group>/apt-amd64.txt` therefore pins, for amd64, the binary built from the **same Debian source version** as each arm64 pin. `tools/make_arch_apt_lock.py` derives it from the same snapshot indexes, and `locks/apt-amd64-map.json` lists every difference. Today there is one: `grep` 3.11-4+b1 on arm64 is `grep` 3.11-4 on amd64, both from source `grep` 3.11-4. The installed inventory must still match the pinned list exactly.

`Dockerfile.task` adds the architecture-independent initial task filesystem. Inputs are the exact task tree from GitHub Release `assets-v1-20261112` **and its published original SIF**. The task archives alone omit some initial databases and completed setup snapshots. The original SIF is therefore a required public reconstruction input. It is read with `unsquashfs`; no ARM programs execute on x86. Only `/app`, `/workspace`, `/scripts`, `/entrypoint.sh` and `/opt/dbt-bench-initial` enter the task layer. Hidden `tests/` and `solution/` from the task archive are never copied into an agent image. ELF payloads in the selected task layer are rejected. Frozen dbt packages and initial data are preserved; setup is not rerun with online dependency resolution.

Download and verify task archives/SIFs as in [artifacts](../docs/artifacts.md), then:

```bash
python scripts/prepare_image_context.py \
  --task deb__dbt-session-attribution --platform linux/amd64 \
  --task-tree /data/tasks/deb__dbt-session-attribution \
  --original-sif /data/images/original-arm64.sif \
  --output /data/build/session-amd64
cd /data/build/session-amd64
sh build-commands.sh
```

This emits local Docker build commands only. The generated combined `Dockerfile` builds both layers in one BuildKit graph, so the final stage does not need to pull a locally loaded intermediate tag. The separate base/task Dockerfiles are also retained for CI that publishes the base by digest. It never builds or pushes automatically. `--platform linux/arm64` uses the same recipe and original task payload with the other pinned base digest. Requires Docker BuildKit, Apptainer for SIF metadata, and squashfs-tools. The image-build CI/publishing workflow is maintained by the experiment owner.

Convert the locally built OCI image to a SIF on an amd64 CPU host, and run a reference smoke (no GPU/model endpoint):

```bash
apptainer build session-amd64.sif docker-daemon://dbt-bench-task:deb__dbt-session-attribution-amd64
sha256sum session-amd64.sif
python scripts/smoke_image.py --task deb__dbt-session-attribution \
  --task-tree /data/tasks/deb__dbt-session-attribution --sif session-amd64.sif \
  --sif-sha256 <the-verified-SIF-SHA256> --output /data/smoke/session-amd64
```

Use the patched Harbor runtime Python. The smoke runs native oracle/reference plus verifier once, preserves rewards and exceptions, and asserts native reward 1. Spider copy-gold oracle smoke does not certify a genuine reference build. Prefer the DEB task above for the initial control.

After building and publishing, the owner must fill the exact SIF SHA256, OCI/ORAS digest and URI in `platform-images.json`, set its entry to `published`, and put its `asset_id` in the worker's local asset map. Preparation rejects entries still marked `build_required`. ARM measurement bindings remain the published originals in `release/assets.json`; the new Docker recipes do not silently replace those SIFs. Neither Docker builds nor amd64 native reference smokes were executed on the ARM development node.

In practice the `build-images` GitHub Actions workflow (`.github/workflows/build-images.yml`) runs these steps on x86 runners for every image: it fetches and verifies the task archive and the original SIF, prepares the context, builds for linux/amd64, converts to a SIF, runs `smoke_image.py`, and installs the x64 Qwen Code bundle inside the image to check `node`, `npm` and `qwen`. It publishes nothing. The owner pushes the passing SIFs to GHCR and records them in `platform-images.json`; all 33 were published this way on 2026-11-12.
