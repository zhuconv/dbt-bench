# Task family and split review

All 135 candidates now have an instruction/project family decision in split `v2-family-reviewed-2026-11-05`: **79 groups, 93 train, 42 test**. The [frozen decisions](../catalog/reviews/family-v1/freeze.json) and [split](../catalog/splits.json) bind membership, workflow classification, and historical exposure. This completes the declared family/split review, not full semantic contracts or verifier validation.

The assignment rule is fixed independently of model rewards and verifier outcomes: keep each reviewed group intact; if any member was v3 test, assign the entire group to test; otherwise keep train. Twelve DEB tasks consequently move from train to test. Their prior train exposure remains recorded. This split is not a clean holdout claim for previously trained checkpoints.

| Review scope | Tasks | Groups | Evidence |
|---|---:|---:|---|
| ADE and related Spider projects | 22 | 4 | [Cross-source review](../catalog/reviews/family-v1/cross-source-family-review-v1.json) |
| Remaining Spider projects | 49 | 35 | [Spider review](../catalog/reviews/family-v1/spider-family-review-v1.json) |
| DEB instruction and component families | 64 | 40 | [DEB review](../catalog/reviews/family-v1/deb-family-review-v1.json) |

The 71 ADE/Spider tasks were inspected in fresh read-only views of their current hash-verified SIFs. Public SQL/YAML/Markdown inventories exclude generated dependencies and build output. DEB review binds current image hashes to previously executed native fresh-environment snapshots. All 516 directly cited inputs were hash-checked during consolidation. These inspections and metadata checks are not new benchmark trials.

| Cross-source group | Tasks | Evidence and limits |
|---|---:|---|
| Airbnb | 4 | ADE 005's NPS outputs also occur in 007's initialized schema; Spider shares project components with different daily/monthly and seven-day objectives. |
| Northwind / analytics engineering | 5 | Related customer dimensions and repair tasks overlap. ADE 005/006 inject duplicates during setup, so raw project similarity does not establish equal inputs. |
| F1 | 9 | ADE f1002 and Spider f1001 share four driver-statistics objectives. The f1003 prompt variants have identical initialized model files, as do the f1010 variants. |
| Intercom | 4 | Related thread and conversation objectives; ADE removes local models while Spider retains seven models and requests admin/team metrics. |

The remaining Spider groups record explicit package/component reuse for app-store reporting, QuickBooks, Recharge, Shopify, Tickit, TPC-H, Workday, and Xero. MovieLens `movie_recomm001` and `netflix001` remain together because of reused prompt text; their projects differ, and the known MovieLens instruction mismatch remains broken. Exact initialized SQL-content comparisons found no additional cross-group matches among the 71 inspected tasks; absence of such a match does not prove independent answers or data.

DEB separates task family from warehouse family. All 64 share the retail environment/project, but only documented objective and component overlap forms same-split groups. The larger groups cover GA4 attribution, customer value/retention, fulfillment, product affinity, and product sales scorecards. Supplier payments, supplier scorecards, and three-way matching retain distinct groups because their outputs and central rules differ. The full record explains all 40 groups and boundary decisions. No DEB holdout is described as unseen-warehouse generalization.

The ten tasks moved from v2 train to v3 test are retained in the historical lists and the frozen review. Training use is `unknown`: list membership is evidence of historical selection, not proof that a specific checkpoint saw the task. Unknown warehouse identities for other Spider projects remain explicit.

The original catalog, split, and import manifest are archived under [catalog/history/v1-legacy-provisional](../catalog/history/v1-legacy-provisional). Original review records remain unchanged. Consolidated copies only rebind source references to those byte-identical archives; they do not rewrite review decisions.

Catalog loading rejects a frozen label without matching reviewed decisions, changed review inputs, drift in task family/version, or removed exposure history. An [explicit identity review](../validation/evidence/split-freeze-identity-review-v1.json) checks that all execution-related inputs remain unchanged for the 12 current semantic reviews before rebinding metadata. The other 123 contracts are not promoted. Report policy `training-candidates-v5` records original generation hashes separately from the current selection split; old experiment IDs and native scores are preserved.

The remaining work is task-specific semantic validation, additional real construction witnesses and negative cases, trial-specific environment/trace review, and final repository acceptance. Freezing these memberships does not certify those unfinished checks.
