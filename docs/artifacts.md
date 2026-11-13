# External artifacts

The repository contains source, locks, recipes, checker code and small manifests. It excludes task payloads, jobs, database files, archives, SIFs, model weights and full evidence. `release/assets.json` binds 171 immutable objects to 67 candidate task versions. Its existing object IDs and content hashes are unchanged.

## Hosting status

The 33 images are published in `ghcr.io/zhuconv/dbt-bench-images`, one tag per SIF: `v1-linux-arm64-<full SIF SHA256>`. Their 33 amd64 rebuilds are in the same package as `v1-linux-amd64-<SIF SHA256>` (see "amd64 reconstruction" below). The 67 task archives and the qualification index archive are attachments to the `assets-v1-20261112` GitHub Release. File names, sizes and hashes are in `release/upload-manifest.json`; the review behind the publication is in [asset-publication.md](asset-publication.md). The Qwen Code bundle is not redistributed (see below).

Never substitute a newly resolved image, dbt package or task version for an absent object.

## Verify an image

```bash
image_sha256='<selected image sha256 from release/assets.json>'
image_path="$SCRATCH/dbt-bench-assets/image-${image_sha256}.sif"
mkdir -p "$(dirname "$image_path")"
apptainer pull "$image_path" "oras://ghcr.io/zhuconv/dbt-bench-images:v1-linux-arm64-${image_sha256}"
printf '%s  %s\n' "$image_sha256" "$image_path" | sha256sum --check --strict
```

The hash is over the complete SIF file, not an OCI manifest. Keep both identities after publication. Tags are immutable by project policy, not by a registry guarantee; always verify the SHA256 even when a tag looks correct.

## Verify a Release attachment and its contents

Select `filename` and `sha256` from `release/upload-manifest.json`:

```bash
filename='<selected attachment filename>'
archive_sha256='<selected attachment sha256>'
curl --fail --location --output "$filename" \
  "https://github.com/zhuconv/dbt-bench/releases/download/assets-v1-20261112/$filename"
printf '%s  %s\n' "$archive_sha256" "$filename" | sha256sum --check --strict
mkdir -p extracted
tar --no-same-owner -xzf "$filename" -C extracted
```

Use an empty extraction directory per task. Task archives contain a `task/` directory, retain executable bits and symlinks, and were checked against the original tree hash. Verify after extracting:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python - <<'PYCODE'
from dbt_bench.io import tree_hash
expected = '<task sha256 from release/assets.json>'
assert tree_hash('extracted/task') == expected, 'Task tree changed'
print('Task tree verified')
PYCODE
```

The Qwen Code bundle is not redistributed, because it carries third-party binaries under LGPL terms (Bun's statically linked JavaScriptCore, and sharp/libvips). Build it on a Linux ARM64 compute node from the locked npm, Node and NVM sources using [these commands](../runtime/qwen-code/README.md). Every package file must match `runtime/qwen-code/native-installation.json`. With the reviewed permissions restored, the complete archive, inventory and installer must also match the original hashes. The local manifest has a new provenance hash, bound through `qwen_bundle` in local config; it does not replace the historical manifest asset ID. Preparation now accepts this verified manifest while preserving a distinct execution identity. See [the rebuild and identity review](qwen-bundle-rebuild.md). The qualification attachment contains `records/<asset-id>.json`; each must match its own original hash. **It is only an index archive**: contracts, expected outputs, input snapshots and receipts referenced by those records are not enclosed. Independently replaying all audits still requires those exact evidence dependencies.

Fill a private JSON `asset_map` with asset IDs mapped to verified local files or task directories; point `configs/local.toml` at it. Do not commit local paths or credentials. Task relocation changes only the reviewed image coordinate and records the new identity separately.

## Rebuild versus exact replay

The fixed upstream task snapshots in `sources.lock.yaml` can be retrieved with `scripts/prepare.py`. Public upstream content plus the checked-in patches and recipes explains task construction, but does not recreate every prepared variant or historical build byte automatically. In particular, mutable apt resolution, build timestamps and frozen dbt packages prevent a claim of bit-identical SIF reconstruction. Use the exact SIF and task objects for this measurement identity. A rebuild has a new identity and needs qualification.

The public model stays at the fixed HF revision in `inference/qwen3.6-35b-a3b.json`; `scripts/prefetch_smoke_model.py` and `release/model-files.json` verify it. Model weights are not part of this upload plan. The download does not send model requests.

Task solutions/gold/tests are public upstream materials but must stay outside the agent-visible workspace during evaluation. Do not mount a complete task archive into the agent. Images, task archives and qualification records contain historical build paths. They were published unchanged on purpose: a sanitized copy would be a new artifact, never a silent replacement of the original hashes.

## amd64 reconstruction

`images/` provides a shared Docker base recipe and architecture-independent task layer. Base manifests for amd64 and arm64 come from the same pinned upstream index. See [image build inputs and CPU smoke](../images/README.md).

The 33 amd64 images were built on 2026-11-12 by the `build-images` GitHub Actions workflow ([run 43359761397](https://github.com/zhuconv/dbt-bench/actions/runs/43359761397)). Each passed the CPU reference smoke and a Qwen Code CLI check inside the image. They are in the same package, tagged `v1-linux-amd64-<SIF SHA256>`. `images/platform-images.json` gives each one's asset ID, SIF SHA256, size, registry digest and build record. A SIF built this way is a new artifact with its own hash, not a byte copy of the arm64 one. To pull one by hand on an amd64 host:

```bash
tag='<tag from images/platform-images.json>'
sif_sha256='<its sif_sha256>'
image_path="$SCRATCH/dbt-bench-assets/image-amd64-${sif_sha256}.sif"
apptainer pull "$image_path" "oras://ghcr.io/zhuconv/dbt-bench-images:${tag}"
printf '%s  %s\n' "$sif_sha256" "$image_path" | sha256sum --check --strict
```

`python scripts/worker.py assets <batch> --asset-map <asset_map> --dest <dir>` does this for every task of a batch, and adds the task trees. Original ARM SIFs are still needed as read-only sources of initial databases/setup snapshots absent from the task archives. Qwen x64 is reconstructed locally with `scripts/build_qwen_platform.py`, never downloaded from our registry.
