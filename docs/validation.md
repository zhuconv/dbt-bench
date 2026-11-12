# 验证操作与证据格式

`validate.py` 对全部候选建账并对指定 task 执行原生 Harbor 调查。每个 contract 都记录实际审查与证据范围。当前非隔离任务已完成原生14项审查，未决规则保留实际 gap；新扩容任务在读取固定材料前保持 pending，不推测表或业务规则。

## 逐题操作

1. `prepare.py tasks` 取得固定材料。阅读 instruction、Dockerfile/setup、project schema/docs、solution、tests。列出确切的输入、提交路径、输出表/列、粒度/键、NULL/类型/精度、合法等价答案；每条关键要求取稳定 ID。
2. 填 contract 的 `reference_kind`（full_gold/sampled_gold/independent_recompute/aggregate_assertions/combined）和 `witness_kind`（built_from_inputs/copy_gold/unknown）。不能根据存在 gold.duckdb 或 oracle 成功推断真实可解性。
3. 在实际 Qwen 工具进程上下文和原生 verifier 入口分别运行 `scripts/context_probe.py` 的内容，显式指定数据库路径。保留 probe 本身、调用命令、日志与 hash。主机 doctor 的信息不能代替这两份证据。
4. 在干净副本创建 agent 产物，由 verifier 实际读取同一个产物，记录文件 hash、行/类型结果与 native transfer mode。需要 separate 时显式列出要传递的源代码、输入和输出；仅能 import 不够。`dbt_bench.environment.assess_contexts` 提供证据审查辅助。
5. 原生 reference/nop 调查通过 Harbor 运行。两次 oracle 必须检查实际输出稳定性；重复 reward=1 不代表输出稳定。保留符合工作流的真实解法；Spider 复制 gold 不认证可解性。
6. 对每条关键规则建立独立语义证据、负例及等价正例（若适用）。错误必须独立于待测 checker 证明；无效变异、执行失败、原数据上等价都不能计入语义检出分母。
7. 填入 checks 与 requirement coverage 的证据，绑定 reviewed_inputs，再更新 ledger。保留 native/audit 独立结论。

## 证据引用与身份

小文件可提交到 `validation/evidence/`；大型数据库和日志放 `artifacts/` 或现有持久挂载。当前实现验证本地/挂载路径引用，不会隐式下载远程 URI。引用是：

```json
{"path": "artifacts/validation/.../result.json", "sha256": "完整64位SHA256"}
```

环境记录保存在题目目录的 `environment.json`：记录构建镜像 digest/ID、backend、数据/依赖清单、两侧 executable/version/路径、profiles、native transfer mode、探测日志和跨上下文读取证据。该文件整体 hash 绑定审查；不能用占位内容代替探测结果。

完成真实调查后取得当前非循环身份：

```bash
uv run --locked python scripts/validate.py --task '<task_key>' --show-review-inputs
```

把输出作为 contract 的 `reviewed_inputs`，只表示审查依据绑定，不会替你填写通过结论。修改合同、案例、数据/环境、verifier、运行配置或实现后重新审查。纯报告变更不改变验证身份。改变后的记录保留在每题 `history/`，不会覆盖旧调查证据。

`checks` 包含 assets、contract、environment_agent、environment_verifier、cross_context、agent_visibility、reference、reference_repeat、nop、equivalent、independent_semantics、negatives、comparator、stability。每项是：

```json
{"status": "pass", "reason": "具体执行和结论范围", "evidence": [{"path": "...", "sha256": "..."}]}
```

可选状态 pass/fail/gap/pending/not_applicable；非 pending 都需要理由和可核对证据。reference、reference_repeat、independent_semantics、negatives、stability 等决定性检查不能用 not_applicable 绕过。

关键要求包括 `id`、`critical`、业务规则和 `coverage.native`/`coverage.audit`。`covered` 需要证据；漏检用 `missed_wrong`，误拒绝用 `rejected_legal`，并附 `case_validity`、`independent_proof` 和 `checker_evidence`。SQL execution failure 不构成语义 verifier 的漏检证据。独立证明和 checker 结果应定位到不同执行步骤，并说明推导；系统检查 hash/引用，语义独立性仍需审查。

已确认 broken 用 diagnoses 数组，指定 kind=broken、target=native 或 audit、reason 和 evidence。没有证据的旧标签不会生效。audit 需有独立 checker_version、checks、requirements、reviewed_inputs；不能因为补充 checker 通过而把 native weak 改为 validated。

## 可执行 case

在 `validation/<source>/<task>/cases/<case>/` 放 case.json、submission.sh、proof.sh。脚本路径只允许目录内相对路径。case.json 的字段：

```json
{
  "kind": "negative",
  "reviewed": true,
  "requirement_ids": ["R-window"],
  "independent_derivation": "解释错误的独立推导；列出输入、期望值和计算方法",
  "submission_script": "submission.sh",
  "proof_script": "proof.sh",
  "run_native_reference": true
}
```

这段格式说明不是任何真实 task 的已审查 case。实际 case 必须从该题材料建立，不能直接把 reviewed=true 当证据。

验证入口在独立副本保留原 `solution/solve.sh` 为 native-solve.sh，按需先运行它，再执行 submission.sh，最后 proof.sh。proof.sh 在 verifier 之前执行；必须写 `$DBT_BENCH_PROOF`，包含 validity、submission_artifacts、input_artifacts、derivation。两个 artifacts 数组都明确列出容器内文件 path 和 sha256；gate 在原生 checker 前重新核对实际文件。负例 validity=business_error，等价正例 legal_equivalent，真实构建见证 built_from_inputs。proof script 应在 SQL 失败、变异无效或原始数据上等价时非零退出，并写清原因。

原生 tests/test.sh 被完整保留为 native-test.sh；验证副本的 gate 只有独立 proof 合格时才调用它。gate 不写 reward，更不会补造原生分数。该路径仅支持 shared verifier，明确拒绝 separate。native 与 audit 若会修改提交，应各自从同一个只读提交快照复制后评估，并记录 hash；当前没有通用的任务输出快照猜测器。

## 已完成的反例与尚未完成的试点

- 直接从旧 strict_check.py 提取纯比较函数，重现 ID=10000/10001、大整数相邻 ID 的误接受，以及按容差排序的误拒绝；新比较器回归已执行。
- 小型日历缺口数据独立计算 28 days 和 28 rows，证明每日结果不同而汇总偏差低于 1%。这是合成反例，**没有运行 ADE 原生 verifier**；计划所述完整 gold 3786/4508/722/646 行统计仍待本机复现。
- Spider 键值错配回归使用列值集合不变的小表，验证新 comparator 拒绝；未把它冒充固定 Spider evaluator 的检出结果。
- DEB Python/path、DuckDB 读取、profile/依赖破坏的证据判断有回归；本机 probe 只说明主机状态，真实容器两侧尚未执行。
- Qwen error-only、空合法输出、多调用 ID、长输出、压缩、重试、异常终止的审计回归已执行。`check_runtime.py` 需要安装的固定 Harbor；当前未执行真实 converter conformance。
- 正常控制：合成完整证据协议和正确表比较均有正向测试。提议的真实控制 `deb__dbt-session-attribution` 尚未取得材料，不宣称其 native verifier 已充分验证。

全量 ledger 汇总所有 135 题和 14 类协议检查，pending 不等于 broken。全量认证尚未完成。
