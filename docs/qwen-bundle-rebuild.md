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

**Not by assigning the historical execution ID, and not automatically with the current tools.** Local paths, allocation and manifest provenance already create a distinct execution identity. A matching Qwen installation-content ID is one necessary equivalence check, not a complete measurement identity or an audit result. `bench.py inspect` still reports `cross_account_merge_supported=false`.

The minimal future import layer should add a companion receipt, without editing old manifests/results, and must:

1. Verify original and imported artifacts, the exact Qwen payload/lock hashes and the fixed Harbor package inventory; do not trust a supplied content ID without verifying its inputs.
2. Match task version, canonical task/data/image identity, native verifier, qualified audit checker, model revision, sampling/thinking/context settings, attempt budgets and retry policy. Normalize only explicitly allowed deployment paths/account coordinates and the verified bundle manifest provenance.
3. Match the assigned batch, ordered members, server seed and four planned attempt slots. Preserve independent execution IDs; reject duplicate slot ownership/results instead of selecting a favorable run. A new server RNG stream is not bitwise continuation of an old one.
4. Audit the exact captured submission, retain native/audit outcomes and external-interruption adjudication, then apply the existing joint-success and four-valid-attempt rules. A bundle equivalence receipt cannot fill missing audit or qualification evidence.
5. Record the equivalence-policy version and all source execution IDs in the import receipt. Preserve the difficulty-only / no-training restriction.

This change supplies the verified `qwen_installation` companion field in newly prepared member identities. It does not implement or preapprove that importer, alter the split rules, or merge any result. Ownership must be coordinated before another account runs an already assigned batch.

## If an archive hash still differs

The supported builder currently requires exact archive equality; both tested rebuilds achieved it. An inventory with equal package bytes but different modes is rejected, not waved through. No transport-hash relaxation is needed for this path.

If a future compression/library change produces a byte-different archive, a separately versioned minimal extension could verify every extracted entry against the pinned **full runtime inventory** (path, kind, file content, size, mode and symlink target), retain the exact installer/Node/NVM/npm locks, and record archive SHA256 only as a transport identity. Archive extraction must retain the present traversal/link checks. Comparing only `native-installation.json`'s 1,608 package files is insufficient: that omits Node/NVM and runtime layout/modes. Such an extension is proposed here, not implemented or used to relabel any historical run.
