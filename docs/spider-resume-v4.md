# Spider v4 原生审查恢复

2026-11-07 用户解除暂停。54 个非隔离任务在当前镜像上逐题审查；movie_recomm001 继续隔离，11 个已确认环境 fail 不修。所有新增案例、selftest 和批量日志写入 SCRATCH。没有模型请求，没有改变镜像、原生 verifier 或原始 reward，也没有开始阶段二 audit。

公开 SQL 线索先读取 README，再按文件 hash 保存到 `artifacts/validation/spider-public-sql-leads-20261107/source/`；[provenance](../artifacts/validation/spider-public-sql-leads-20261107/provenance.json) 保留原路径、实际文件及 fakeclock 编译证据。原作者报告不是 Harbor 运行证据。

| 当前环境实际执行 | 结果与范围 | 原始对账 |
|---|---|---|
| 指定 16 题，各两次干净输入构建 | 32 次 reward=1；16 题全部声明输出逐列、按精确类型及行关联重复一致。lever001 安装文件数为零，仅 dbt run。 | [public-witness](../artifacts/validation/native-spider-v4-public-witness-v1/reconciliation.json) |
| pendo001、shopify001、xero001、xero_new001/002，真实时钟，各两次 | 10 次 reward=0。xero_new 两题构建失败和缺失输出保留，不能称为成功构建后的比较失败。 | [real-clock](../artifacts/validation/native-spider-v4-clock-real-and-ties-v1/reconciliation.json) |
| 同五题，2024-09-08 构建时钟，各两次 | 10 次 reward=1，五题输出稳定。LD_PRELOAD 仅传给构建子进程，原生 verifier 使用真实时钟。镜像未改。 | [fixed-clock](../artifacts/validation/native-spider-v4-clock-fixed-v1/reconciliation.json) |
| quickbooks001，真实时钟两次 | 两次 reward=0，输出不稳定；同日期 running_balance 顺序诊断与原输入有限重算另存逐题 review。 | [real-clock 同批](../artifacts/validation/native-spider-v4-clock-real-and-ties-v1/reconciliation.json) |
| 54 题 locked-shell 初始数据库只读探针 | 54/54 执行，无异常，数据库 hash 均未改变。仅采集关系、列和视图 SQL；还需与任务声明及初始文件系统扫描逐题核对，表名不自动证明泄漏。 | [逐题 metadata 对账](${ARTIFACT_ROOT}/validation/spider-v4-initial-db-review-v1/summary.json) |

16 题为 app_reporting001/002、apple_store001、google_play001、greenhouse001、hubspot001、intercom001、jira001、lever001、marketo001、quickbooks002、recharge001/002、shopify002、twilio001、workday001。Asana001 和 Workday002 已有参考运行按用户要求保留，不新增重复的参考认证批次；必要的新案例在干净副本建立自己的提交基线。旧证据是否适用当前身份仍需核对，不能因旧 reward=1 自动迁移。

参考构建批次删除验证副本内原始 copy-gold solution，安装声明的公开 SQL/YAML；记录冻结包前后 hash、实际 dbt 命令、输出与原生 test.sh 观察。成功构建只说明真实输入可以产生已观察的输出；有限独立语义检查只覆盖实际执行的原始行数、键和指标，不认证完整业务语义。

相对于 copy-gold 输出重排行只构成 reference-relative 案例，未建立完整合法基线时 equivalent 保留有执行依据的 gap。负例先依据原输入、明确公式或交付约定证明错误，再调用未修改的原生 verifier。不同字段组合、模型交付和独立业务规则分别留 coverage，不把所有叶子列穷举成案例。

以下为需要新运行时的真实入口；已存在且身份一致的批次直接复用。输出目录必须不存在。在具有 Apptainer 的 compute allocation 上沿用 README 的运行环境：

```bash
export PYTHONPATH=src:scripts
export PYTHONDONTWRITEBYTECODE=1
SPIDER_BATCH="$SCRATCH/dbt-bench-storage/artifacts/validation/<new-witness-batch>"
runtime/.venv/bin/python scripts/prepare_spider_witness_batch.py \
  --base-batch artifacts/validation/native-mechanical-spider-v4 \
  --leads artifacts/validation/spider-public-sql-leads-20261107/source \
  --task lever001 --mode real-clock --output "$SPIDER_BATCH"
PYTHONPATH="$SPIDER_BATCH/tools" runtime/.venv/bin/harbor run -c "$SPIDER_BATCH/config.yaml"
runtime/.venv/bin/python scripts/reconcile_mechanical_batch.py \
  --batch "$SPIDER_BATCH" --output "$SPIDER_BATCH/reconciliation.json"
```

多个任务重复 `--task`。日期诊断使用 `--mode fixed-clock --clock-shim artifacts/validation/spider-public-sql-leads-20261107/fakeclock.so`；它是独立诊断运行，不能替代真实时钟成绩。`prepare_initial_database_batch.py --include-spider`、`reconcile_initial_database_batch.py`、`review_spider_mechanical.py` 共用批量工具生成执行证据和待人工核对的机械基线，不自动修改 ledger。

当前阶段一按每 10 题冻结，末批不足 10 题另行合并。最新版见 [README](../README.md) 和 [机器状态](../validation/delivery-status.json)。335 项实现回归测试通过，属于实现/fixture 测试，不计入真实 benchmark 次数；[测试记录](../artifacts/validation/phase1-review-v1/implementation-tests-20261107-v13.json)。

阶段一已完成：见 [最终完成报告](phase1-completion.md)。当前身份与证据核对通过，11 个原环境 fail 保留；阶段二尚未开始。
