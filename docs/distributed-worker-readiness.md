# Distributed worker readiness — 2026-11-12

The release implements task export, accepted-main import, multi-architecture Docker recipes and x64 runtime locks. This is CPU preparation evidence; amd64 images have not been built/pushed here and no H100 service was started. The owner's architecture-equivalence decision is preserved without a cross-architecture study.

| Area | Implemented and checked | Remaining |
|---|---|---|
| Export | 4 slot summaries; native score/exception/time/config facts; immutable submission hashes; full member/raw trace archive; exact identity proofs | New worker run needed for production export; old pre-export executions are not relabeled |
| Import | Owner main/claim only; download/size/SHA256; safe extraction; task/model/budget/seed/member-order/code/Qwen/platform checks; duplicate rejection; audit-ready trial paths | Actual merged worker archive and owner central audit evidence |
| Images | 33 bindings, 5 exact software groups, shared Docker recipe and separate initial task layer; ADE/DEB/Spider build-context checks. Update: all 33 amd64 images built, reference-smoked and published by the `build-images` workflow; digests in `images/platform-images.json` | — |
| x64 runtime | vLLM 0.19.0 lock; 17 package / 7,615 entry Qwen bundle reproduced twice; minimal native adapter platform patch. Update: x64 Qwen CLI runs inside every amd64 image; the x86 inference lock installs and imports (`check-x64-inference`) | H100 startup on target hardware |

56 focused CPU tests ran: 55 passed and 1 skipped because the legacy helper assumes a local runtime venv inside the export. The new native platform adapter was separately exercised against the actual timeout-fixed Harbor runtime with a recording environment; both manifest platforms and all rejection guards passed, with no model request. The new raw parser also read all 4 historical repeat-purchase trials and verified their pre-native captures; that diagnostic did not import, re-score or replace any historical slot. The x64 inference lock passes an offline lock check, and all shared dependency versions match ARM. Cross-assembling ARM with the new bundle assembler reproduced the original archive, inventory and installer hashes exactly.

Task build inputs include the hash-checked Release task archives. The published original SIF is also needed for initial databases and completed setup snapshots that task archives omit. Matching project files are taken directly from the Release tree; generated/changed initial files and frozen packages come from its bound SIF. Nothing executes from the original ARM image during context preparation. See [image recipes](../images/README.md).

The existing server command remains BF16, 131072 context and one concurrent attempt. [H100 deployment](h100-worker.md) describes memory sizing without claiming a GPU fit test. The portable runner still requires Slurm and Apptainer. Unregistered amd64 images are rejected at preparation. Missing captures/audit conclusions remain unknown; native reward alone never becomes joint success. Measurements and trajectories are prohibited from training.

No release commit, registry push, Slurm operation or GPU execution occurred. The active repository's sealed files and the maintained AGENTS/ledger/worker/check workflow were left unchanged. See [machine-readable checks](../release/distributed-worker-checks.json) and [complete changed-file list](../release/distributed-worker-changes.json). The evidence references use the configured artifact root; large logs, data and bundles are external.
