# 原生阶段一完成报告

2026-11-07，131/131 个非隔离任务的 14 项原生检查全部完成审查；4 个 broken 题继续隔离。冻结版本为 [native-phase1-v14](../catalog/pools/native-phase1-v14/report.md)，保留全部 135 个候选的独立账目。

| 来源 | 候选 | 完成审查 | 隔离 | pass 检查 | fail 检查 | 实际 gap |
|---|---:|---:|---:|---:|---:|---:|
| ade | 16 | 15 | 1 | 152 | 31 | 27 |
| deb | 64 | 62 | 2 | 575 | 241 | 52 |
| spider2 | 55 | 54 | 1 | 450 | 153 | 153 |

活跃任务共 1,834 项检查：1177 pass、425 fail、232 执行后仍无法判定的 gap，pending 为 0。总账中剩余 40 个 pending 均属于四个隔离题，不计作已执行。

原生结论为 **1 validated、130 weak、4 broken、0 pending**。audit 独立结论仍为 1 validated、134 pending；本阶段没有新增 audit。完整语义认证尚未完成，weak 与 gap 都没有被提升为可靠成功样本。

| 交付状态 | 实际值 |
|---|---|
| phase1_complete | true |
| implementation_complete | false |
| validation_complete | false |
| phase2_started | false |
| 可用于后续采样的任务 | 2 |
| 本阶段新增模型请求 | 0 |
| 实现/fixture 回归测试 | 335 项通过 |

可采样任务仍为 `ade__ade-bench-ana-eng005`（native）与 `ade__ade-bench-ana-eng003`（每份提交须通过既定 `customer-output-audit-v2`）。这是任务资格，不是已导出的训练样本。

关键执行结果与限制：

- 指定 16 道公开 SQL 题经 locked Qwen shell 各两次干净构建，32 次原生 reward 均为 1。Greenhouse 两次均缺必需 `locations` 列，因此 reference/reference_repeat 记 fail，构建与分数事实仍保留。
- 五个时钟敏感题在真实时钟下共 10 次 reward=0；固定到 2024-09-08 后共 10 次 reward=1。后者属于独立诊断，未修改镜像。Pendo 的有限原输入重算另外证实原 copy/gold 在真实日期缺 2,370 个 guide/day。
- QuickBooks001 两次真实运行均为 0，存在同日期 running_balance 不稳定；独立原输入检查另发现 3/11 条 payment debit 丢失金额小数。两类问题分别记录。
- Asana/Workday 既有真实运行按要求保留，未重跑参考认证。Workday002 历史 SIF 与当前绑定不同，当前 copy-gold 不替代旧真实解，reference/repeat/stability 保留 gap。
- Activity 两张原生公开 expected fixture 的全部交付值可直接复制；整体 visibility 为 fail，隐藏材料隔离子检查仍 pass。XeroNew001 的 Invoice Line Items 单独纳入 contract，新增证据只覆盖现存 fixture 组件，不填补旧真实构建未保存的该输出。
- 11 个 Spider environment_agent fail 与用户指定清单完全一致，未修环境；其余无完整真实解的任务不以 copy-gold 认证可解性。有限重算只证明已声明的行、键与指标范围。

活跃任务的 reference 检查：{'fail': 44, 'gap': 43, 'pass': 44}。witness_kind 分类：{'unknown': 37, 'copy_gold': 32, 'built_from_inputs': 62}；输入构建类型不代表该候选已完全正确。逐项范围、规则覆盖和实际日志见 [逐题账本](validation-status.md)。

保留的 trial 索引包含 native 4527、额外输入 fixture 108、环境 probe 131 条，包含历史记录，不能当作本阶段模型实验数。历史真实 Qwen smoke 状态为 true，原有 5 个模型 trial 单独保留；本阶段模型请求为零。Recharge001 一条独立有效性不足的旧案例继续保留原始分数，但不计规则覆盖；Workday 的删除标记案例明确单列额外 fixture。

历史保留的 6 个未满计划仍有 11 个无终态槽位（包括一个显式 superseded 计划）；未静默删除或伪造结果。当前 Spider v4 运行计划没有未满槽位，131 题的本阶段检查均有当前有效证据。这些历史计划及各自 manifest 列在机器摘要中。

完整性核对覆盖全部 131 题当前身份和证据哈希；历史 v01–v12 与最终 v13/v14 均通过共用 verify。最终发布曾被“显式 pending=0 与省略零计数”的报告比较拦住；一行比较修复及两个回归测试通过。未发布的失败副本保留在 SCRATCH，恢复 v13 工作账本后用共用 publisher 重新发布末批；没有重跑 benchmark 或修改原生成绩。

下一阶段按既定顺序为 weak 题补独立 audit，优先处理缺口较少的任务，并完成 audit 自己的 14 项检查。当前阶段二尚未开始。

[机器摘要](../validation/phase1-completion.json) · [最终冻结 manifest](../catalog/pools/native-phase1-v14/manifest.json) · [最终完整性证据](../artifacts/validation/phase1-review-v1/phase1-final-integrity-v1.json) · [335 项测试](../artifacts/validation/phase1-review-v1/implementation-tests-20261107-v13.json)
