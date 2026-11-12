# Spider environment repair — 2026-11-06

Spider uses DuckDB **1.2.2** and each pinned task's original `dbt_packages` tree.
Image preparation does not execute `dbt deps`. ADE and DEB dependency versions
are unchanged. Native task files, verifiers, historical images and raw scores
are retained.

The former Spider lock resolved DuckDB 2.0.0, and the former image workflow ran
`dbt deps`. The asana001 source includes `asana_source` 0.8.0, while dependency
resolution replaced it with 0.8.2. Copy-gold reference trials did not exercise
the real dbt build and therefore did not establish compatibility.

The new Python lock changes only DuckDB. The image builder verifies the exact
initial project files, modes and symlinks, the package tree and database
readability. Its [patch metadata](../patches/spider-duckdb122-vendored-v3/metadata.json)
records the explicit environment policy and native Dockerfile hash.

## Asana regression

Two clean Harbor trials used the locked Qwen shell service without model calls.
They added only `asana__team.sql` and `asana__user.sql` from upstream
`fivetran/dbt_asana` v0.8.1, commit
`a9bbaea8650daee6b661b6420d07bbaee6db6e28`, and ran all 40 dbt models.
Both native rewards were 1. The build had no gold access. Subsequent independent
comparison found all columns, keys and values equal to gold: 25 team rows and
20 user rows in each trial. This establishes this build witness and environment
regression, not full verifier validation.

[Build evidence](../artifacts/validation/spider-v3-asana-witness-v1/reconciliation.json)
and [full-column comparison](../artifacts/validation/phase1-review-v1/asana-v3-all-columns.json).

## All 55 unmodified projects

Each project was initialized cleanly and ran `dbt run --profiles-dir /workspace
--no-partial-parse` once through the actual Qwen shell service. Verification was
disabled for these environment-only trials; they have no benchmark reward.
No model sources or vendored package files changed during these checks.

| Actual outcome | Tasks | `environment_agent` |
| --- | ---: | --- |
| Existing project built successfully | 30 | pass, limited to existing project |
| Compilation stopped at missing model/node | 10 | gap, remaining execution not established |
| Other actual execution errors | 15 | fail, per-task logs retained |

Other failures include three YAML parse errors, five incomplete/missing package
sets, absent source tables/macros, unsupported project SQL, and NBA DataFrame
string-type compatibility. Some runtime failures also print missing-model
warnings; those warnings were not used to hide the runtime errors. Fifteen
projects have no vendored package files; this inventory is distinct from
whether their project declares package requirements.

[Per-task review](../artifacts/validation/spider-v3-environment-review-v1/summary.json)
and the canonical `validation/spider2/*/contract.yaml` records contain the
outcomes and evidence. Old runtime checks were archived and invalidated when
the environment changed. The pre-existing static movie_recomm001 contradiction
remains isolated; its environment probe does not reopen semantic investigation.

The other execution errors still need review. Spider reference/nop and full
mechanical reruns remain pending; further real-solution work awaits the user's
next instruction. No automatic dependency download or complete handwritten
solution was introduced to suppress these failures.

## Actual entry points

- `scripts/build_spider_v3.py`: build the common dependency layer, then selected
  exact-vendored task images. Requires the pinned wheel and download manifest in
  `artifacts/images/spider-v3-inputs` and the preserved parent image.
- `scripts/prepare_spider_environment.py`: prepare selected tasks, or all 55 by
  default, as ordinary Harbor jobs with verification disabled.
- `scripts/reconcile_spider_environment.py`: reconcile executed batches and
  publish current environment checks while preserving historical evidence.

Run the prepared configuration with the copied tools directory on `PYTHONPATH`
and `runtime/.venv/bin/harbor run -c <batch>/config.yaml`, as recorded in each
batch manifest. Large task copies, images and outputs live under SCRATCH.

Frozen phase-1 reports archive mutable review files by SHA-256 under
`artifacts/validation/phase1-freeze-objects`. Version 01's external index/tool
references also have an explicit
[archive map](../artifacts/validation/phase1-review-v1/native-phase1-v01-external-reference-archive.json).
Later working-file edits do not replace the frozen bytes.
