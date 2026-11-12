# Experiment protocol — 2026-11-12

This document describes the prescribed difficulty experiment. Deployment coordinates may vary through local configuration; protocol content may not. Every execution has a resolved config and content-hash identity. No training is performed in this repository.

## 1. Sources, task construction and candidates

The catalog starts from the union of the legacy train/test v3 lists, 135 unique `(source,task)` candidates: ADE 16, Spider2-DBT 55, DEB 64. Preserve all IDs, historical splits/exposure and excluded tasks. ADE uses the Harbor dataset at `37db108843a49bb31a592e37a75e2c40dc3f9749`, with upstream `c305600ebf17e7e7c4550bd401b6ddb530beb7c8`. Spider uses HF Harbor dataset `82d1fb0c144d28b1fd9852006cee0a39e74bd4a8`, upstream `cafb867313aab4e674652054198f383cf4018943` and adapter `fd9af60ae6b804dc76c282b172663e56c0811231`. DEB uses `a3278ad102829a6084dde086244a0ef665a8011c`. Exact source and dependency locks accompany the code.

The current measurement population is 67 tasks (ADE 15, Spider 16, DEB 36). Modified instructions use their reviewed version IDs; accepted TRY_CAST variants exclude orders with null order time. Original instructions, versions and records remain historical. Gold-error, degenerate-empty and native-broken tasks are excluded. Candidate qualification is separate from success on a model attempt. A copy-gold oracle is not a build-from-inputs solvability witness.

## 2. Environment and task isolation

Docker is the reference environment definition. The prescribed deployment uses hash-locked Apptainer images on Vista aarch64, retaining native initialization and hidden-material boundaries. DEB and the repaired Spider environment use DuckDB 1.2.2; Spider retains frozen task dbt packages instead of resolving newer dependencies. ADE versions remain task-specific. Image recipes are not a claim that fresh network builds reproduce the exact SIF bytes.

Each attempt starts clean. Native shared-verifier mode and native budgets remain unchanged. Gold, reference solutions and hidden tests are not supplied during the agent phase. Capture source and declared output databases before native verification; independently evaluate audits against that same captured submission. Checker execution that writes data uses a clean copy. Agent-caused dependency damage is a model outcome; verified initial environment/transport faults are recorded separately.

## 3. Model and inference configuration

Model: `Qwen/Qwen3.6-35B-A3B@995ad96eacd98c81ed38be0c5b274b04031597b0`. Harbor 0.25.0, Qwen Code 0.26.5, vLLM 0.19.0, BF16, context 131072, maximum output 32768, temperature 0.6, top_p 0.95, top_k 20, min_p 0, presence_penalty 0, repetition_penalty 1. Thinking is enabled. The tool parser is `qwen3_coder`, reasoning parser `qwen3`; exact arguments and client configuration are in the inference files. One active sequence and one Harbor trial at a time per allocation.

The independent timeout patch removes the unintended600s transport deadline. Genuine native phase deadlines retain their original values. Communication/cleanup failure is not reported as model budget exhaustion. Cancellation must stop owned processes before stable submission collection; unconfirmed cleanup invalidates grading. Runtime package inventory and patch pre/postimages are fixed.

## 4. Four attempts and scheduling

Each task needs four **valid** independent clean attempts. One model service is loaded per batch and reused across sequential native Harbor jobs. No extra model smoke generations, custom agent loop, automatic model retries or training requests. The four attempts retain their native agent/verifier/setup/resource budgets. The 24h regrouping uses at most three tasks per job; conservative phase-cap sums force many smaller batches.

Server seeds are declared before execution. New regrouped batches use the existing deterministic hash rule over the scheduling amendment, population, ordered membership and batch index; resulting seed values are preserved in `release/batches.json`. One continuously advancing model-server RNG stream is used across attempts. New grouping or recovery is a new execution identity and is not bitwise continuation of an interrupted stream.

Verified infrastructure/transport failures and allocation interruptions do not count toward four valid attempts and require explicit replacement lineage. Model exhaustion of the native budget counts according to its actual scores and is not rerun. Completed results survive interruption. Unstarted slots remain unstarted. A recovery starts from clean input and preserves all prior records.

## 5. Native and audit success, validity and difficulty

An attempt succeeds only when the native source-specific success rule **and** the qualified audit both accept the **same submission**. Preserve native reward maps and raw exceptions without replacement. Record native-pass count separately from joint-pass count `k`. Audit unknown or unexecuted is not success; record unknown separately. Among four valid attempts, difficulty bins are `k=0`, `k=1–3`, and `k=4`. Native-only successes do not determine difficulty.

If audit remains undecidable, disclose the possible range `[k,k+unknown]` and whether it crosses bin boundaries in the split draft. Do not label pending evidence as an executed gap, silently discard unknowns, or choose the best of duplicate attempts. Zero successes does not itself remove a qualified task from the population.

## 6. Task audit protocol

Native and supplemental audit each retain 14 checks: assets, contract, reference, reference_repeat, nop, stability, environment_verifier, environment_agent, cross_context, agent_visibility, equivalent, independent_semantics, negatives and comparator. Declare requirements and coverage per task. Run two clean reference builds, inspect outputs and stable columns, actual Qwen shell and verifier execution contexts, initial visibility and cross-context compatibility. Use independent raw-input recomputation or explicitly independent blind-build evidence for semantic checks; shared gold/SQL lineage alone is insufficient.

Negative validity is established independently before invoking the checker. At least one effective negative per key rule is sufficient; execution errors, equivalent mutations and genuine business errors remain distinct. Key alignment, exact integers and large integers, duplicates, missing rows/columns, per-column tolerance, NULL/type rules and legal equivalent answers are explicitly tested. Native weak does not become validated merely because audit fills a gap. Full evidence, helper versions, exact contracts and mutations remain externally hash-addressed. Localization does not renew old qualification automatically.

## 7. Family split and predeclared random rule

Use family-V1 as the indivisible unit, including noncandidates. Any family with more than 4 candidate task IDs is forced to train. In the current grouping this includes `deb-customer-value-retention` and `shared-f1-project`. Stratify candidate tasks by source (ADE/Spider/DEB), workflow and difficulty.

Workflow normalization lowercases, trims and replaces spaces with underscores. `new_project` and `new_project_from_reference_models` form new-project. `diagnose*`, `modify_existing_model*`, `repair_grain`, `build_models_and_repair_grain`, and `rebuild_attribution_model` form repair/diagnosis; everything else is add/complete models.

Seed **2026110801** was recorded before drawing. Use Python `random.Random(seed)`. First assign sorted families with no candidate by probability 0.25 to test. Then evaluate exactly 10000 assignments of sorted candidate-containing families, using probability 0.25 for test and consuming no draw for forced-train families. For every observed category in each of the three dimensions sum `abs(test candidate count in category / all candidate count in category - 1/4)` using exact rational arithmetic. Choose the earliest minimum, with no second objective or seed search. Noncandidates follow their family. Keep old splits byte-identical; report the draft, category counts, family assignments and old/new differences, then wait for explicit user confirmation before freezing. This release draws no split.

## 8. Reproduction, artifacts and limits

Install both locked runtimes, acquire exact assets, fill local coordinates, prepare an assigned batch, verify hashes/native schema, and run the printed Slurm test-only command before submission. Preserve task/image/runtime/model/checker identities, seed and member order. Retain Qwen raw messages/tool outputs, Harbor results and captures. ATIF schema validity, content fidelity and actual model-context capture are separate claims; compression or missing context must remain visible.

Portable preparation gives a new execution identity containing deployment/relocation metadata. Replacing only allowed coordinates must preserve task semantics; altered SQL, prompts, data, checker or model arguments requires a different experiment and relevant revalidation. Cross-account result import is deferred; ownership and immutable artifact sharing must be arranged before others execute batches.

**DEB train and test use the same retail database. The evaluation measures new tasks on familiar data, not generalization to unseen databases.** Shared source projects, historical exposure, accepted instruction repairs, time-dependent gold, ordering/rounding, incomplete traces and architecture-specific dependencies limit interpretation. SIFs and full evidence are not publicly hosted yet. A completed job is not a certified joint success.

## Measurement data prohibition

Difficulty results are used only for stratification. **Neither measurement trajectories nor their derived samples may be used for any model training**, including SFT, distillation, preference optimization or selecting training demonstrations. This restriction also applies to failures, test traces and recovery attempts.
