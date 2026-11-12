# Intercom 逐题验证进度（尚未发布为新任务池）

本轮完成三个任务的 **62 次原始输入 Harbor/Apptainer 验证**，没有模型请求。全部 trial 无异常、原生 reward 和日志保留，62 次容器启动均已退出。当前正式冻结版本仍是 [validated-pilot-v10](../catalog/pools/validated-pilot-v10/report.md)，可采样任务仍为 2；以下新增证据尚未合入 canonical contract、ledger 或新冻结版本。

| 任务 | 接受正确/等价提交 | 检出错误 | 漏掉错误 | 合计 |
|---|---:|---:|---:|---:|
| intercom001 | 6 | 12 | 2 | 20 |
| intercom002 | 6 | 12 | 4 | 22 |
| intercom003 | 6 | 12 | 2 | 20 |
| 合计 | 18 | 36 | 8 | 62 |

每题有两次从干净输入构建的控制、两次 view 等价提交、两次行列重排等价提交。Intercom003 只创建要求的 metrics 模型；没有为通过 test-setup 的额外选择而创建 threads 模型。原生 verifier 接受这些提交。

原生 set equality 漏掉完整重复行。Intercom002 分别测试 threads 和 metrics 重复，其他两题测试各自要求的输出。重复行在评分前已由独立 raw Python 聚合和保留重复次数的比较证明错误；评分后再次读取输出，确认原生 rebuild 没有消除该错误。

缺行、错 ID、创建时间与 ID 错配、计数加一、计数变 NULL、虚构首次联系时间，每题各重复两次，共 36 次均被 equality 测试拒绝，existence 测试仍通过。键值错配案例保留了 ID 集合和创建时间集合，只改变对应关系。这些证据足以支持三题原生 verifier 为 weak 的观察结论，不证明其全部业务规则已经验证。

输入审查发现：原始数据库有 9 条 conversation history（仅 2 条 active），9 条 conversation part 全部与 conversation ID 不匹配。因此原始正确输出只有两行，事件计数为零、事件时间为 NULL。agent 阶段捕获的两张 raw 表与固定下载快照完全一致。

另外构造了可手算的小数据，并用纯 Python 枚举、原生参考 SQL、实际包版本保留的 compiled staging SQL 和独立编写的 SQL 交叉检查。每题的 10 种事件变异在原始输入上仅 2 种可区分，在额外输入上全部可区分。**这些是离线 SQL 检查，未运行原生 verifier，不计入 benchmark trial 或 native 检出数。**

尚未解决的业务问题包括：admin response 是否包含带正文的 assignment、close 是否包括 inactive history，以及分数秒使用实际时长还是跨越的整秒边界。额外数据能区分这些规则，但原始零事件 gold 不能解决歧义。参考兼容的构建仅作为有声明范围的见证，三个任务的完整语义见证仍未认证。

审批认证已恢复。最初三个直接镜像探针遗漏了镜像声明的 fakeroot/临时可写初始化，因权限/只读目录失败；失败日志保留。按既有镜像初始化方式重做后全部成功，这不是新的 benchmark 环境故障。另一次离线变异准备错误把键值交换在 threads 和依赖它的 metrics 上各做了一次，抵消了 metrics 的错误；断言在 Harbor 运行前拦住，V3 只变异一次。该准备失败不计入 trial、retry 或模型失败。

证据入口：

- [机器可读 checkpoint](../artifacts/validation/intercom-three-task-review-v1/checkpoint.json)
- [62 次运行对账](../artifacts/validation/intercom-three-task-review-v1/reconciliation.json)及[容器清理检查](../artifacts/validation/intercom-three-task-review-v1/cleanup.json)
- [累计 1465 个原生/额外输入验证 trial 索引](../artifacts/validation/intercom-three-task-review-v1/retained-trial-index.json)：1426 native、39 既有额外输入 fixture；本轮离线检查不在其中
- [离线事件规则检查](../artifacts/validation/intercom-cases-preparation-v1/offline-event-checks/result.json)和[原始输入负例证明](../artifacts/validation/intercom-cases-preparation-v3/mutation-check.json)
- [三个 contract 草稿](../artifacts/validation/intercom-three-task-review-v1/contract-drafts/)；尚未替代 canonical contract

本轮实际使用的准备命令如下。输出目录必须是新的路径；下面记录的目录已执行并保留，不能覆盖。

```bash
runtime/.venv/bin/python -B artifacts/validation/intercom-cases-preparation-v1/prepare_cases.py \
  --output-dir artifacts/validation/intercom-input-build-pilot-v1
runtime/.venv/bin/python -B artifacts/validation/intercom-cases-preparation-v1/prepare_cases.py \
  --output-dir artifacts/validation/intercom-output-cases-v1 \
  --case view --case row-column-order --case duplicate-threads \
  --case duplicate-conversation_metrics --concurrent 4
runtime/.venv/bin/python -B artifacts/validation/intercom-cases-preparation-v3/prepare_cases.py \
  --output-dir artifacts/validation/intercom-original-negatives-v3 --concurrent 4
```

每个输出目录都有 Harbor 已加载验证的 `config.yaml`，在 compute allocation 上按 [运行文档](../README.md)的 Apptainer 环境执行 `harbor run -c <config.yaml>`。本轮没有另写 agent loop、scheduler，也没有改原生 verifier 或 reward。

下一步是在独立、明确版本化的额外输入任务副本中执行事件案例，保留独立推导的 expected 和原生 SQL checker 身份；补齐 source-only/缺源文件、列和类型案例，再进行逐题裁决、证据 hash/过期检查和新冻结版本发布。完整实现及全量验证均未完成。
