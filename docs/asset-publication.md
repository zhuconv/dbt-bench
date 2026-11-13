# External asset publication review — 2026-11-12

Status: **prepared, not uploaded**. No source commit, image push, Release upload, active-repository change or Slurm operation was made for this review. This is a redistribution assessment of the inspected snapshots, not a new license for this project.

## Inventory and reproducibility

| Objects in assets.json | Count | Original bytes | Hosting/rebuild decision |
|---|---:|---:|---|
| SIF images | 33 | 7,164,071,936 (6.67 GiB) | Host the exact files for this measurement identity: 15 ADE, 16 Spider, 2 shared DEB images. Recipes explain construction but do not promise bit-identical reconstruction. |
| Prepared task trees | 67 | 1,317,113,247 (1.23 GiB), 8,388 files | Host the reviewed variants for exact replay. Upstream snapshots are retrievable, but upstream + current prepare alone does not reconstruct every local instruction/environment variant. |
| Qualification JSON records | 67 | 19,236,264 (18.35 MiB) | These are indexes, **not self-contained evidence packages**. Exact audit replay additionally needs their referenced contracts, expected outputs, snapshots and receipts. |
| Qwen manifest/archive/installer/inventory | 4 | 168,180,852 (160.39 MiB) | Host the exact reviewed bundle together. Public Node/npm/Qwen sources and locks can rebuild software; archive-byte equality must not be assumed. |

The transport plan has **102 payloads**: 33 SIFs and 69 Release attachments. The 67 task tarballs total 217,326,141 bytes; the Qwen transport archive is 165,999,938 bytes; the qualification index archive is 1,979,483 bytes. Total upload payload: **7,549,377,498 bytes (7.03 GiB)**, excluding future license/source/evidence closure attachments. Every attachment is below GitHub's 2 GiB per-file limit; 69 is below its 1,000-asset limit. [GitHub Release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

Task archives use deterministic member order, normalized owner/timestamps, and retain executable bits/symlinks. Every archived member was read back and matched to the original `dbt_bench.io.tree_hash`. Original task/SIF/Qwen/qualification bytes and hashes were not changed. Generic upstream task files, public Python/npm packages and model weights need not be mirrored merely to recover them: keep fixed revisions and hashes. The exact reviewed derived objects and any evidence unavailable upstream need hosting or another accessible immutable store.

## Read-only scan findings

All 33 SIF files were hash-checked, their filesystem partitions extracted into temporary copies, and **884,959 regular-file instances** scanned. All extraction operations succeeded. All 67 task trees, 67 qualification records and 4 Qwen bundle files were scanned too. Metadata descriptors were scanned separately from filesystem contents. No original image was mounted writable or rebuilt.

**No confirmed private deployment credential was found.** This is a static pattern review, not a guarantee of absence or a credential validity probe. Candidate locations include library PEM parsers, SSH algorithm identifiers, AWS examples, npm documentation and upstream package examples. The Qwen Code subtree matches all **1,390 files** in the official npm 0.26.5 tarball after its locked SHA512 integrity check. All 31 credential-assignment candidates in task trees are `env_var(...)` references. No matched values are printed in the reports.

Publication-relevant locations:

- **31 of 33 SIFs**: `.singularity.d/Singularity`, `.singularity.d/bootstrap_history/Apptainer1`, `.singularity.d/labels.json`, and SIF metadata descriptors contain owner build coordinates. Exact per-image lines/offsets are in `release/asset-scan-summary.json`. The two DEB images have no matches to the owner-coordinate pattern.
- **All 67 task trees**: `task.toml` contains historical deployment paths (typical locations: lines 19/34, 25/40, or 45/52; per-object locations are in the scan manifest).
- **All 67 qualification records**: JSON evidence references contain historical personal filesystem coordinates. An unchanged index archive would publish those paths and still would not include the evidence files.
- Qwen bundle: no owner-coordinate matches. Dependency documentation and license/author metadata contain third-party names and email addresses; retain required attribution.
- Database string-column scan: **42 unique databases** were opened read-only in the matching image's DuckDB runtime. There were **160 table/column email-pattern locations**, including `main.employees.email_address` and Airbnb listing-name columns. No owner-coordinate or provider-token pattern matched a database string column. Values are intentionally omitted; provenance and data licensing still need review. The scan does not decide whether a dataset address is real or synthetic.

The regular-file scan includes binary byte patterns; the database scan covers base-table VARCHAR/CHAR/TEXT/JSON columns. It does not certify arbitrary BLOB contents, encrypted data, unrecognized token formats, or every semantic form of personal information. Email matches are capped to legal-size candidate lengths in the later image scan to avoid pathological matching on long binary strings. Location reports cap repeated matches per file/category; aggregate counts are retained.

### Hidden tests and answer material

All **730 task-file candidates** (solutions, gold, expected fixtures and test scripts) match the pinned public upstream bytes. No unmatched local hidden grader material was found among those candidates. Image candidates are exclusively dependency integration fixtures under `dbt_packages/`; no task gold/solution/test payload was found by this check inside the SIFs. These public benchmark materials are required for verification and are not confidential solely because they are hidden from the evaluated agent. Maintain the separate agent/verifier visibility boundary: do not bind a complete task archive into an agent workspace. Qualification indexes reference audit evidence that has not been packaged or cleared for publication.

## License assessment

| Component | Fixed-source conclusion | Redistribution requirements / remaining work |
|---|---|---|
| ADE-bench | Apache-2.0 at `c305600e…`; Harbor adapter is Apache-2.0 | Retain license, notices and change notices. Upstream public database availability does not by itself settle every underlying third-party dataset's terms. |
| Spider2 / Spider2-DBT | MIT at `cafb8673…` | Retain copyright/permission notice. Review the individual project/data provenance for the 16 selected tasks; repository MIT is not a substitute for each third-party data grant. |
| DEB | Apache-2.0 at `a3278ad1…` | Preserve LICENSE and NOTICE. Its NOTICE expressly covers the tasks, models, verifiers and synthetic retail warehouse as Snowflake original works; it says no real customer data is included. |
| Harbor adapter / dbt-core / dbt-duckdb / common Fivetran and dbt-utils packages | Apache-2.0 in upstream license or installed metadata | Keep notices and versioned modification disclosures; bundled dependencies retain their own terms. |
| DuckDB | MIT | Retain license/copyright notices. |
| Python | PSF and bundled third-party terms | Preserve notices; inventory the image's standard-library and dependency licenses. |
| Node.js / npm / pandas / NumPy | MIT, Artistic-2.0, BSD and bundled third-party terms | Retain applicable notices. Inspect the actual installed package metadata rather than labeling an entire runtime MIT. |
| Qwen Code 0.26.5 | Apache-2.0 | Qwen's license alone does not cover all bundle binaries. Keep upstream and local installer notices. |
| Bundled Bun 1.4.4 | Bun MIT; statically linked JavaScriptCore/WebKit LGPL | Resolve the pinned corresponding source and relinking materials for the actual binary before publishing the bundle. |
| Bundled sharp/libvips | Apache-2.0 plus LGPL-3.0-or-later in package metadata | Resolve exact binary-to-source mapping, license texts and LGPL obligations, including the WASM build. |
| Debian base: bash, coreutils, asciinema, glibc and others | GPL/LGPL and other per-package licenses | Redistribution is allowed under their terms, but package notices alone are not enough where corresponding-source obligations apply. Preserve exact source-package/version mapping and make the required source/material available alongside the binaries. |
| This project's new patches, checkers and installer | Project license selection remains pending | No license is assigned by this review; resolve publication terms for the local contributions. |

Primary fixed references: [ADE license](https://raw.githubusercontent.com/dbt-labs/ade-bench/c305600ebf17e7e7c4550bd401b6ddb530beb7c8/LICENSE), [Spider license](https://raw.githubusercontent.com/xlang-ai/Spider2/cafb867313aab4e674652054198f383cf4018943/LICENSE), [DEB license](https://raw.githubusercontent.com/Snowflake-Labs/data-eng-bench/a3278ad102829a6084dde086244a0ef665a8011c/LICENSE), [DEB NOTICE](https://raw.githubusercontent.com/Snowflake-Labs/data-eng-bench/a3278ad102829a6084dde086244a0ef665a8011c/NOTICE), [Qwen license](https://raw.githubusercontent.com/QwenLM/qwen-code/v0.26.5/LICENSE), [Bun notices and relinking discussion](https://raw.githubusercontent.com/oven-sh/bun/bun-v1.4.4/LICENSE.md), [GPL source distribution guidance](https://www.gnu.org/licenses/gpl-faq.html#DistributeWithSourceOnInternet).

The inspected Harbor-datasets ADE snapshot has no root license file, and the fixed HF dataset README supplies no license declaration. The upstream task and Harbor adapter licenses establish useful provenance, but dataset-level and underlying data rights are still listed as unresolved. Neither “public download” nor “GitHub repository is public” establishes an unrestricted redistribution grant. License copies and source URLs are retained under `release/third-party-notices/`; `release/software-inventory.json` is an observed package inventory, not a complete legal SBOM. One illustrative libvips license was fetched; its version must still be matched to the exact packaged binary. This review therefore does **not** certify all payloads ready for publication.

## Upload plan and gates

1. Resolve each object's listed privacy/license holds in `release/upload-manifest.json`. Decide whether historical build paths may be public. If redaction is needed, make new derivative objects with new hashes and qualify them; never alter sealed assets. Decide how to publish the dependency-closed qualification evidence, with a separate sanitized index if needed.
2. Use **one** GHCR package: `ghcr.io/zhuconv/dbt-bench-images`. Tag rule: `v1-linux-arm64-<full SIF SHA256>`. No `latest`, no reused tags. The full file digest is retained even if an OCI digest is later added.
3. Once authorized, authenticate outside scripts using an appropriate package-write credential; never put its value in a command file or repository. The prepared push command for each row is `apptainer push <verified-local-SIF> oras://ghcr.io/zhuconv/dbt-bench-images:<tag>`.
4. First pushes default to private. Link the package to `zhuconv/dbt-bench`, explicitly set package visibility to public, then verify an unauthenticated pull and the SIF SHA256. Record the OCI manifest digest separately. Public repository visibility alone is insufficient. [GitHub registry access and visibility](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry), [Apptainer ORAS transport](https://apptainer.org/docs/user/1.4/registry.html).
5. Upload the 69 verified non-image archives as GitHub Release attachments under `assets-v1-20261112`, together with this inventory, checksums and required notices/source materials. Do not use `--clobber` to replace old evidence. Verify anonymous downloads and archive hashes, then extracted object hashes.
6. Mark each URI available only after those checks. Nothing in this preparation changed remote visibility, created a release or uploaded bytes.

`release/upload-manifest.json` and `.tsv` contain filenames, tags, hashes, sizes and holds; `release/payload-files.sha256` contains payload checksums. Exact local source paths and the two guarded upload command files stay in the private preparation workspace, not the repository. The command files deliberately exit before upload until the review holds are resolved. Pull and verification commands are in [asset acquisition](artifacts.md).

## Publication decisions and outcome (2026-11-12)

The repository owner approved three decisions on 2026-11-12. Every hold in `release/upload-manifest.json` now records which one resolved it.

1. **Historical build paths are published unchanged.** Images, task archives and qualification records keep their embedded paths, so the published bytes equal the measured ones.
2. **Project license: Apache-2.0** (`LICENSE`, `NOTICE`).
3. **Third-party terms.** Upstream licenses and notices stay in `release/third-party-notices/`, and `NOTICE` reproduces the data-eng-bench NOTICE. All 33 images are Debian 13. `os-sources-unique.tsv` lists the 126 source packages behind the installed packages, with snapshot.debian.org links, and `os-source-packages.tsv` lists 4,718 packages per image. The Qwen Code bundle is not redistributed, because it carries LGPL binaries; build it from the locks in `runtime/qwen-code/`. Upstream already publishes the ADE-bench and Spider2 data, so the residual dataset-provenance risk is accepted.

Outcome:
- All 33 SIFs (6.67 GiB) were pushed to `ghcr.io/zhuconv/dbt-bench-images`, one tag each.
- The 67 task archives and the qualification index archive (0.2 GiB compressed) were uploaded to the `assets-v1-20261112` Release.
- Every object's SHA256 was checked against the manifest before its upload.
