# Qwen bundle rebuild and result identity — 2026-11-12

## Outcome

The published release contains enough inputs to rebuild Qwen from public upstreams. The original workflow passed the 17-package / 1,608-file comparison, but a restrictive host umask changed 1,248 directory modes and 1,609 file modes in the assembled runtime. File bytes and symlink targets matched; the complete inventory and archive did not. Separately, portable preparation required the old manifest hash, rejecting a new manifest even for the same payload.

The release-only fix normalizes the private staging tree to the reviewed permissions and checks the original inventory, archive and installer hashes before declaring a build usable. Portable configuration reuses `bind_qwen_bundle`'s strict source-lock verifier and writes the new manifest's actual SHA256 into the native agent kwargs. The original lock values, native receipt, installer, native Harbor adapter, benchmark/checker files and active experiment artifacts were not changed.

## Executed CPU evidence

The source baseline was public release commit `5d00e7f1a2d6d2d4781fcfb61c691eb7087fc726`, copied without private caches or task artifacts. A second isolated npm install used the new cache offline. The patched builder was tested under both umask 077 and 022. The resulting complete installation was installed and replayed with the unchanged installer; Node/npm/Qwen versions were executed in a clean shell. All 67 released task configuration templates passed the native Harbor schema and differed only in allowed local paths/job names plus the actual verified manifest binding. Native adapter installation was exercised with real bundle checksums and a recording environment, with no model request.

| Check | Observed result |
|---|---|
| npm dependency versions | 17 match |
| native package-file comparison | 1,608 match |
| assembled inventory | 7,615 entries match, including modes and symlinks |
| Node / npm / Qwen CLI | 22.23.4 / 10.9.9 / 0.26.5 |
| two builder umasks | same original archive, inventory and installer hashes |
| installer replay | unchanged tree accepted; no reinstall |
| task templates / native schema | 67 checked |
| focused unit tests | 26 pass, 1 skipped because the export has no local runtime venv; native adapter separately exercised above |
| GPU runs / model requests / Slurm submissions | 0 / 0 / 0 |

Exact payload hashes:

```text
archive    9eecdb60bdf465e83a588542136231c2f30f68e84ea222176f8ca71d3053a120
inventory  caa7fc4a32ed39fb1281bb4840dfbec8b54479a68c786b2ab8946d66fb292321
installer  0e9ba995bcc1fa5cf3835854d650efd3843c15e4e1107f680600130634ac2a26
```

The tested new manifest hash was `f13a9ce85b4072bd64902a5050b4c5d88df94d4e877189932c922f0abd72059a`; the historical manifest hash remains `459ee1185ff8a73b6637dd5c1486d35a8f2deb13eb5d31eaa1123ab637253552`. Their installation-content ID is derived from the same pinned kind/platform/versions, dependency locks and three payload hashes; the new receipt records it as `e2e26ad0d987290e746c770ca7515fe0b70d342ce41a4fe0123de77df30a11a8`. No raw credential or installation archive is added to Git.

The CPU test's first wrapper assigned the same `/dev/null` to two npm config roles, which npm rejected; separate empty config files corrected that test setup. A later host shell loaded `BASH_ENV` and replaced `NVM_DIR` with the user's pre-existing installation. Removing that host startup hook for the clean-shell check produced the locked versions. These diagnostic failures remain in external evidence and are not counted as successful attempts. They do not change the task shell or native adapter.

## Can the results be merged?

The release now supplies `bench.py export` and owner-side `bench.py import` under policy `distributed-import-v1-user-accepted-amd64-arm64`. It preserves original execution IDs and records a separate acceptance receipt. It never merges a result by rewriting its original identity.

1. Only summaries committed to the experiment owner's remote main are accepted. Fork/PR working trees are not acceptance. A matching worker claim and the declared batch/task/slot assignment are mandatory.
2. Raw archives are downloaded and checked for size/SHA256, safe paths and a complete file inventory. Native results, failures, submissions and trace logs remain unchanged. The summary is independently reconstructed from raw slots; it cannot invent a score.
3. Task version, canonical data/task hash, native tests, qualification binding, model files/revision, native configuration/budgets/retries, full server command and batch seed must match. Normalization is restricted to job/path coordinates and the hash of a verified Qwen manifest. Code files and runtime inventories are checked against the central reviewed release. Coordinate/provenance changes keep distinct execution IDs.
4. ARM Qwen retains the original full payload locks. x64 has separate explicit Node/npm/NVM, package, archive and full-inventory locks. Native code is selected only through platform-specific reviewed bindings. The 2026-11-12 owner decision accepts architecture equivalence without an empirical cross-architecture study; it does not relax task, model, seed or scoring checks.
5. Ordered batch members and 4 slots are checked; duplicate IDs/slots or changed publications fail. A byte-identical repeat import checks retained evidence and is idempotent. A new server RNG stream is not claimed to continue the old one bit for bit.
6. `trials.jsonl` and companion path maps expose local raw trial paths for central extraction/audit. Audit and validity remain pending until the owner evaluates the exact pre-native capture with the qualified checker and adjudicates interruptions. Historical paths are not edited. Missing capture/qualification evidence cannot become a joint success. No imported measurement/trajectory may be used for training.

The original ARM receipt remains `exact_locked_payload_v1`. The x64 bundle's same-platform equivalence also requires full payload equality; matching only npm package versions is insufficient. Code/image identity is sealed during preparation. Legacy pre-export runs without those bindings need an explicit separate owner adapter and are not auto-converted.

## If an archive hash still differs

The supported builder currently requires exact archive equality; both tested rebuilds achieved it. An inventory with equal package bytes but different modes is rejected, not waved through. No transport-hash relaxation is needed for this path.

If a future compression/library change produces a byte-different archive, a separately versioned minimal extension could verify every extracted entry against the pinned **full runtime inventory** (path, kind, file content, size, mode and symlink target), retain the exact installer/Node/NVM/npm locks, and record archive SHA256 only as a transport identity. Archive extraction must retain the present traversal/link checks. Comparing only `native-installation.json`'s 1,608 package files is insufficient: that omits Node/NVM and runtime layout/modes. Such an extension is proposed here, not implemented or used to relabel any historical run.
