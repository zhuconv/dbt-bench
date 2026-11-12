# Spider 六题环境兼容修复

本文记录环境修复时的历史范围。2026-11-07 用户已解除审查暂停；当前真实构建、有限独立重算和逐题案例见 [Spider v4 审查恢复](spider-resume-v4.md)，不再沿用本文历史 pending 作为最新状态。

本轮按用户指定仅补五题缺少的已冻结 dbt packages，并为 nba001 固定 pandas 2.2.2。DuckDB 保持 1.2.2，ADE、DEB 的镜像和版本不变。没有执行 dbt deps、重新解析依赖、修改原生 verifier、补写完整解或独立语义推导。

| 任务 | 修复 | 实际 Qwen shell `dbt run` |
|---|---|---|
| netflix001 | 从旧 frozen-deps SIF 补 dbt_utils | gap：缺 int_netflix_original_programs 节点 |
| scd001 | 从旧 frozen-deps SIF 补 dbt_utils | pass：6 个模型成功 |
| shopify_holistic_reporting001 | 从旧 frozen-deps SIF 补 8 个包 | fail：metafield 的 COALESCE 类型冲突；transaction 解析错误 |
| zuora001 | 从旧 frozen-deps SIF 补 4 个包 | fail：缺 zuora_payment_method_data 输入表 |
| inzight001 | 仅补 dbt_duckdb_utils，保留原有 3 包 | gap：缺 be_holidays 节点 |
| nba001 | Python 3.11/aarch64 pandas 2.2.2；显式固定 tzdata 2024.1 | pass：38 个模型成功；原始日志中的 commit 诊断信息保留 |

包源为旧镜像内 `/opt/dbt-bench-initial/dbt_packages` 的文件。提取归档、逐文件 hash、前后绑定、构建定义、完整项目初始化核对及数据库访问探针均保留在 SCRATCH。NBA 使用校验 SHA256 的精确 release wheel，以 `--no-index --no-deps` 安装；新增 tzdata 为 pandas 2.2.2 的依赖。其他 Python 依赖不重装。

首次构建因临时目录未创建而失败；第二次 inzight 的检查发现构建阶段继承的 workspace 中缺新增包的 54 个文件，原有文件无变化。第三次将同一份新增包同步至初始化目录和已初始化 workspace 后通过完整文件核对。失败记录均保留，没有将它们计作环境通过。

六题的 environment_agent 已发布为 2 pass、2 fail、2 gap；其余 129 条 canonical record 未修改。另 9 个已知 Spider fail 保持原判，不修 SQL、reference 或数据。六题的 reference 和 independent_semantics 仍为 pending；成功运行已有模型不认证完整解。当前 55 题环境账目为 32 pass、12 gap、11 fail。

可执行入口：

```bash
PYTHONPATH=src:scripts runtime/.venv/bin/python scripts/build_spider_v4.py \
  --output "$SCRATCH/dbt-bench-storage/artifacts/images/<new-build>" \
  --inputs "$SCRATCH/dbt-bench-storage/artifacts/images/spider-v4-inputs" \
  --task spider2__netflix001

PYTHONPATH=src:scripts runtime/.venv/bin/python scripts/prepare_spider_environment.py \
  --output "$SCRATCH/dbt-bench-storage/artifacts/validation/<new-probe>" \
  --task spider2__netflix001
```

然后通过原生 `harbor run -c <new-probe>/config.yaml` 运行；需要将该批次 `tools/` 加入 PYTHONPATH，沿用 README 的 Apptainer 环境。`reconcile_spider_repairs.py --batch ... --output ... --adjudications ... --publish` 要求失败/gap 具有对应实际 result 的 hash 和人工审查理由，不会将缺失结果自动转为 gap。已有 batch 不重复运行；新输出目录必须不存在。

持久证据根：`artifacts/validation/spider-v4-six-environment-review/`；镜像证据为 SCRATCH 下 `artifacts/images/spider-v4-six-build2/` 和 `spider-v4-inzight-build3/`。随后按当前绑定准备 54 个非隔离题的 reference/nop 与机械批次；movie_recomm001 保持 broken 隔离。

这两个批次现已实际完成并对账：108 oracle 全为原生1，108 nop全为0，108机械trial无异常。52题声明输出在两次干净初始化间逐列一致；airbnb001缺`daily_agg_reviews`、airbnb002缺`src_reviews`，copy-gold没有生成这两项声明输出，完整比较因此无法成立。缺失要求与观察错误全部保留，未缩窄输出清单。机械结果只是fixture输出/上下文观察，不自动成为真实构建见证。机器汇总为`artifacts/validation/phase1-review-v1/spider-v4-execution-summary.json`。
