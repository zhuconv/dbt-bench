# 维护前执行条件与难度测量链路检查

本次只做短 CPU 检查，工作节点为 `i614-074`。没有提交、取消或修改任何 Slurm
作业，没有模型请求、容器/dbt 重建、真实 split 抽签或冻结。首次检查时，维护时段和
已增加的 TimeMin 来自用户同步；当时的只读 Slurm 结果另存于证据目录。
下述 2026-11-09 用户更新取代首次检查中的 TimeMin 状态与部署待决事项，历史证据保留。

## 2026-11-09 用户更新：保留排队作业的旧 runner

用户决定，由 Claude 转达：8 个 gh 作业（1130679、1130689–1130695）已撤销
TimeMin，当前为 `TimeLimit=2-00:00:00`、`TimeMin=N/A`。因此，先前因可能实际分配
24h 而触发旧 runner 的 48h 校验失败的风险已解除。本次仅记录用户同步，没有另行
查询或操作作业。

账户大小写的新证据同样来自用户同步：`scontrol` 对排队作业显示 `${ALLOCATION_LOWER}`，
但对运行中的中继作业 1132451（同样使用 `--account=${ALLOCATION}`）显示 `${ALLOCATION}`。
旧 runner 在作业运行时检查账户，因此据此预期能通过账户校验。目前没有运行态仍返回
小写的相反证据。首次检查中“排队状态返回小写，所以运行时会被拒绝”的推断不充分，
撤回该启动阻断判断；保留当时的排队观察，不将其当作运行态证据。

新增 [v2 runner](../scripts/run_difficulty_gh_batch_v2.py) 和
[v2 sbatch](../scripts/slurm/gh-difficulty-batch-v2.sbatch) 已通过短测试，但**未部署**。
它们接受同一账户的大小写表示及实际 24–48h，记录实际 TimeLimit/TimeMin，
不改变每个模型 attempt 的原生预算。

按用户决定，v2 暂不部署到这 8 个排队作业，留给以后的补跑和新提交；现有作业继续
引用旧 runner 和 preflight。以后采用 v2 时需要建立新的运行身份与 preflight，显式记录
`allocation_policy={minimum_walltime_sec:86400, maximum_walltime_sec:172800}`，
绑定新 runner/sbatch。本次只更新报告，未改代码、已封存配置、原始证据或 Slurm 作业。

## 中断记录与对账

旧版本会在 TERM 后停止自己启动的 Harbor/模型子进程并尝试保存成员和批次结果，
但没有准确的信号时间，且清理/读文件异常可能使主记录未写入。旧对账也只读取
`result.json`，没有把外部分配中止可靠地关联至具体 attempt。

新版本收到 `--signal=B:TERM@180` 时先原子写入并 fsync `interruption.json`，记录
信号、epoch、本地 UTC offset、作业/实验身份、正在处理的成员和未启动成员，再做
清理。主结果在清理前后分别保存；重复 TERM 或清理失败不会覆盖首个中断原因。
初始化校验阶段也受此记录保护。真实 SIGKILL、不可写文件系统等仍可能使部分材料缺失，
不能据此补造 reward。

[v6 对账](../scripts/reconcile_difficulty_v6.py) 保留 native reward、异常和 audit 原值，
在 companion 字段区分：

- 信号前已完成或已完成评分的 trial：保留，包括模型耗尽原生预算的失败，不因后来
  外部中断而重跑。
- 有因果证据的在途外部中断：不计入有效 attempt，输出带父 trial 引用的补跑清单。
- 未启动的成员/slot：列出待执行项，没有原生 0 分、没有伪造 attempt。
- 缺失/损坏结果、只有日志或旧版本缺乏准确边界：保留证据。无法确定 slot 顺序时
  将可读 trial 暂存为未绑定记录，不挤占前面缺失的编号、不自动重跑已完成 trial。

Chicago 无时区的 Harbor 时间不会被当作 UTC。缺 reward 的早期终结记录仍属于早期
终结，不能仅因没有分数就归因于后来的外部中断。模型自己的原生预算耗尽沿用原 split
口径；本次没有改规则。

对账只输出恢复建议，不提交补跑任务。部分成员需要新的剩余 slot 配置及明确父子
关联；不能直接重启整批或让原生四次重跑覆盖已完成的结果。身份有歧义时先核对。

## 已有数据链路验证

重新执行了 8 次 ADE 提交对应的冻结 checker，输入来自已经保存并核对 hash 的
实际 audit 请求、投影和重建证据；8/8 与原结果一致。没有重跑 adapter、原生测试、
dbt 构建或模型，不计为新增 attempts：

| 任务 | 原生通过次数 | 同时通过 native/audit 的 k | 档位 |
|---|---:|---:|---|
| ana-eng005 | 4 | 4 | 4 |
| f1003 | 1 | 0 | 0 |

随后用相同的原始收据经过新对账，读取完整 66 题群体，进入
[v3 草案入口](../scripts/propose_difficulty_split_v3.py) 的 `--dry-run` 分支。
结果正确显示 2 题就绪、64 题未就绪、`would_allow_draw=false`。
对 RNG 和正式抽签函数设置“一调用就报错”的保护后，这条链路仍成功；没有生成
family 分配、`proposal.json` 或冻结结果。另有独立的完整四次 fixture 验证，即使
全部就绪，dry-run 也不会抽签。

证据根目录：

```text
$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/maintenance-readiness-v1/
```

其中 `report.json` 汇总本次工作，`pipeline/summary.json` 关联真实 audit 重放、对账
及 `split-preflight/preflight.json`；`protected-files.json` 核对现有 72 份 preflight
引用和 split 规则未变。针对本次行为的 45 项短测试通过。

可重复的无抽签入口（输出必须用新目录）：

```bash
export PYTHONPATH=src:scripts PYTHONDONTWRITEBYTECODE=1
runtime/.venv/bin/python scripts/review_difficulty_readiness_v1.py \
  --prior "$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/reconciliation-v06.json" \
  --replay "$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/pilot-checker-replay-review-v1/handoff.json" \
  --pool "$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/population-v1.json" \
  --output "$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/<新的审查目录>"
```

Login 节点仅用于 `timeout 60 ssh login1 '短只读命令' < /dev/null` 等短操作，
不使用 `BatchMode=yes`，不在 login 节点运行 agent。本次所有检查结束后不留下中继
节点上的长时间进程。
