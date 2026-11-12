# 当前运行与测试结果

**audit-phase2-v14 已冻结并复核。** 135题全部有记录；131个非隔离题的原生14项、129题新增audit14审查均完成。4个broken题继续隔离。完成审查包含有执行证据的fail/gap，不代表通过认证。

| 项目 | 当前结果 |
|---|---|
| 原生结论 | 1 validated / 130 weak / 4 broken / 0 pending |
| Audit结论 | 25 validated / 110 pending |
| 原生活跃14项 | 1177 pass / 425 fail / 232 gap / 0 pending |
| 新增audit审查 | 129/129；另保留1题既有validated audit |
| 协议资格 | 全split 26题，train 17题 |
| 正式采集声明 | 17题中1题有效、1题过期、15题尚未提供 |
| 实际训练候选 / 严格上下文候选 | 1 / 1；来自既有ADE trial |
| implementation_complete / validation_complete | false / false |

Audit的110个pending结论包括105个已审查但尚未认证的weak题、4个隔离题及1个不需要新增audit的原生控制题。40个尚未执行的原生检查只属于4个隔离题。逐项pass只覆盖声明范围，未决关键规则仍在每题记录中。

[冻结manifest](../catalog/pools/audit-phase2-v14/manifest.json)、[135题CSV](../artifacts/validation/phase2-review-v1/final-delivery-v1/tasks.csv)、[逐题证据索引](../artifacts/validation/phase2-review-v1/final-delivery-v1/tasks.jsonl)、[机器摘要](../artifacts/validation/phase2-review-v1/final-delivery-v1/summary.json)、[全部冻结证据复核](../artifacts/validation/phase2-review-v1/published-evidence-v14.json)。

现有历史catalog的quarantined标签为135；它不是当前4个broken隔离数，也不是采样资格分母。新报告分别保留这些口径。17题协议资格不自动认证提交产物、模型trace或训练样本。

当前工作区413项共享实现/fixture测试全部通过。全新共享源码导出离线安装固定依赖、运行补丁幂等及Harbor conformance通过；同套413项中412通过，1项因未携带旧training仓库而跳过。任务私有audit的自测与真实运行证据分别保存在每题记录，不算在413项内。

[实现测试](../artifacts/validation/phase2-review-v1/implementation-tests-v15.json)、[干净导出验收](../artifacts/verification/clean-export-phase2-v2/summary.json)。该导出不是包含全部外部证据的完整可搬迁发布包。

原生/验证终止trial索引共5111条且ID、原始result hash全部核对：此前native4527、阶段二重建/工作流345、额外输入fixture108、环境探针131。345条来自90个明确登记批次：0 Harbor异常、14 producer错误、30构建失败，全部保留；这些不是模型成功率。历史5个模型trial和15次诊断重验单列，未结束/中断计划保留在旧索引。

[完整终止索引核对](../artifacts/validation/phase2-review-v1/final-delivery-v1/retained-trial-review.json)、[阶段二实际运行](../artifacts/validation/phase2-review-v1/execution-summary-v45.json)。阶段一与阶段二没有发模型请求。

| 历史模型作业 | Task | 原生reward | 镜像与当前相同 |
|---|---|---|---|
| 1123746 | ade__ade-bench-ana-eng003 | {'reward': 1.0} | True |
| 1123437-attempt-4 | deb__dbt-session-attribution | {'reward': 1.0} | False |
| 1123630-attempt-1 | deb__dbt-session-attribution | {'reward': 1.0} | True |
| 1123630-attempt-2 | deb__dbt-session-attribution | {'reward': 1.0} | True |
| 1124050-attempt-2 | spider2__workday002 | {'reward': 1.0} | False |

五次均为实际Qwen Code模型trial，模型revision、Harbor/Qwen版本及镜像身份见[smoke索引](../artifacts/validation/phase2-review-v1/deb_operations/delivery-review-v1/historical-smokes-v1.json)。镜像相同也不表示完整实验身份相同；旧Spider smoke不能冒充当前v4上的模型运行。父Slurm作业失败与后续attempt成功分别保留。

[当前ADE候选重选样报告](../reports/audit-phase2-v14-customer/summary.json)按原实验及原绑定只读重建，未重跑模型。其派生ATIF和客户端上下文完整；原生ATIF仍incomplete，其他trace不自动认证。

完整实现验收仍有以下明确缺口：

- 17 train protocol-eligible tasks have 1 current checked-in capture declaration, 1 stale declaration and 15 absent declarations; additional per-trial collection/audit bindings still require review. The typed file/directory mechanism does not certify undeclared task paths.
- Fixed SIF/runtime inventories are available, but source rebuild/dependency closure is incomplete. Airbnb005 has no preinstalled isolated Harbor server; no new floating bootstrap was invoked and its environment audit remains gap. Current images were not repaired.
- The clean offline acceptance covers shared source, locks and standalone fixtures, not a complete portable release containing every private audit and referenced external evidence object. Frozen evidence currently depends on the existing TACC WORK/SCRATCH paths.
- Trace/context certification is per trial. The retained ADE derived trajectory is complete; unsupported retry/compression/termination/unknown-event cases remain incomplete/unknown. No new model requests were made to extend this scope.

11个Spider环境fail按要求保留，未修镜像；真实解、日期、排序和业务政策缺口见逐题记录。报告依赖现有TACC WORK/SCRATCH证据及绝对路径；源码checkout不包含这些大型artifacts。路径映射和文件hash在机器摘要中，旧run-results原文已归档于本次交付的before目录。

只读复现当前ADE报告：

```bash
PYTHONPATH=src:scripts runtime/.venv/bin/python scripts/report.py \
  --job jobs/ade-qwen-gh-dev-1123746 \
  --experiment artifacts/slurm/1123746/harbor.manifest.json \
  --bindings artifacts/validation/customer-output-policy-v2/bindings-v2.jsonl \
  --output "$SCRATCH/dbt-bench-model-report-new"
```
