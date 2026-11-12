# Independent Apptainer timeout runtime

This patch is **not** added to the existing `sources.lock.yaml` patch chain and must not be applied in place. Build an isolated copy with `scripts/prepare_runtime_timeout_v1.py` using the exact preimage hashes in `metadata.json`.

`patch.diff` contains the four source changes. `server-exec.txt` and `client-exec.txt` are the replacement sections included in the metadata. Candidate01 files retain the development counterexample; candidate02 is the final CPU validation candidate.

See `docs/runtime-timeout-v2.md` for behavior, commands, limitations, schema diagnosis, and the unsubmitted GPU validation proposal. No benchmark budgets, native verifiers, model generation settings, historical rewards, or queued/submitted job files are changed.
