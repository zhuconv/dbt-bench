# Prepared Qwen installation locks

These locks preserve Qwen Code 0.26.5, Node 22.23.4 and npm 10.9.9 on Linux
ARM64/glibc. The installer-only Harbor patch has passed actual setup and replay
in ADE, Spider and DEB containers and is activated in `sources.lock.yaml`.
`prepare config --backend apptainer` requires the fixed bundle and records its
manifest hash in native agent kwargs. Docker's native online installation is
still separately recorded as not certified; this ARM64 bundle is not an x86
installation.

`package-lock.json` fixes every npm tarball URL and integrity value.
`node.lock.json` fixes the Node archive and binary hashes. The binary hash
matches the actual native adapter installation. `native-installation.json`
contains 17 observed package versions and 1,608 package-file hashes, so replay
does not need the original large artifacts directory.

On an allocated Linux ARM64 compute node, prepare a new install check:

```bash
runtime/.venv/bin/python scripts/freeze_qwen_dependencies.py \
  --output-dir artifacts/execution/qwen-lock-check-new
```

The command uses the committed locks, downloads missing pinned artifacts, and
installs into two distinct directories. The second installation uses npm's
offline mode. With an already populated cache, both can be offline:

```bash
runtime/.venv/bin/python scripts/freeze_qwen_dependencies.py --offline \
  --output-dir artifacts/execution/qwen-lock-offline-new
```

Both complete inventories must match, and all declared package bytes must
match the portable native receipt. Generated `.bin` links and npm's hidden
lock metadata are excluded from the comparison across different installation
layouts; CLI execution is checked separately. This is not a claim of full
network isolation or actual model/tool execution. Existing output directories
and locks are never silently replaced.

npm 10.9.9 omitted `libc` conditions from its initial lock. That made `npm ci`
install two musl packages absent from the native glibc installation. The
[recorded metadata repair](../../patches/npm-10-lock-libc-metadata-v1/metadata.json)
restores four exact upstream package fields under before/after hash checks.
It changes no package versions or package code. The first failed comparison
is retained, and subsequent independent offline installations match the
native versions and package bytes. A regenerated lock with a different
preimage is rejected and needs a new review.

Build the fixed NVM/Node/Qwen tree from a successful installation check:

```bash
runtime/.venv/bin/python scripts/build_qwen_bundle.py \
  --installation artifacts/execution/qwen-lock-check-new \
  --output-dir .cache/qwen-runtime/locked-v1
runtime/.venv/bin/python scripts/check_runtime.py --require-locked-installation
```

The builder verifies the complete prepared module inventory and its canonical
package bytes against the native receipt. It emits a deterministic archive,
exact inventory, standalone installer and checksum-bound `bundle.json`.
`nvm.lock.json` pins the native-observed NVM script and support files. No npm
resolution or NVM download runs inside the task. Two independent builds produced
the same archive and inventory; the native tree contains 7,615 entries.

The builder refuses to replace an existing output directory. An existing cache
is checked by `check_runtime.py --require-locked-installation` and each
`prepare config` invocation. For a different prepared location, pass
`prepare config --qwen-bundle PATH/bundle.json`; archive, inventory and installer
hashes must still match the source lock. A changed local manifest cannot
authorize different installation bytes.

`scripts/check_qwen_setup.py` accepts `--candidate-adapter`,
`--candidate-sha256`, `--installation-bundle` and `--bundle-sha256` alongside
its task/database/output arguments. Candidate options are for isolated patch
development; normal probes now use the installed native class and just the
bundle arguments. Evidence and archived probe scripts are retained in the
[three-source review](../../validation/evidence/qwen-bundle-candidate-review-v1.json).

The installer uses Python extraction to avoid ADE's observed `xz` sandbox
failure. It normally renames a validated staging tree; on the observed
Apptainer `EXDEV` error only, it copies to a newly created destination and
rechecks every entry. It refuses to merge an existing changed tree, cleans its
own failed copy, and verifies replay after NVM has actually been sourced.
This fallback is not an atomic rename. All three initial databases remained
unchanged, and all four probe containers (including the first failed ADE
attempt) were confirmed stopped.

Remaining work: freeze remaining Harbor server dependencies and validate actual model/tool
execution under that new identity. Setup probes contain no model requests and
do not certify benchmark semantics or training eligibility.
