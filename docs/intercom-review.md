# Intercom 三题审查与复跑

三题均为 **native weak / audit pending**，完整 witness 为 **unknown**，不纳入采样。结论已进入 [validated-pilot-v11](../catalog/pools/validated-pilot-v11/report.md)；[前一检查点](intercom-progress.md)原样保留。

| 任务 | 原始输入运行 | 额外输入 fixture | 原始语义错误漏检/检出 |
|---|---:|---:|---:|
| intercom001 | 30 | 14 | 2 / 12 |
| intercom002 | 32 | 21 | 4 / 12 |
| intercom003 | 30 | 20 | 2 / 12 |

92次原始运行中，30次正确/等价提交接受，36次错误值、键、关联或缺行被检出，8次完整重复行错误漏检。另有6次缺源文件由 expected-test-count 守卫拒绝、6次缺必需列由比较器结构错误拒绝。6次额外列拒绝单列为策略观察，不计入语义检出率或误拒绝。

每题从干净输入构建两次，与 raw Python 聚合逐字段核对。行列重排、view、source-only、BIGINT ID 各执行两次。source-only 实际删除输出表后由原生 test-setup 从 SQL 重建。删除源文件时已有表仍在，existence 通过，但 equality 未执行，测试数量守卫因而返回0。缺列/额外列触发结构错误；错误文本 less columns 不能代替真实 schema 检查。Intercom003 只创建明确要求的 metrics，没有额外创建 threads。

原始输入有9条 conversation history、2条 active conversation、9条 part；所有 part 都不匹配 conversation。两行 gold 的事件计数为0、事件时间/时长为NULL，不能区分大部分事件算法。

额外 fixture 使用5条 conversation history 与24条 part，独立手算3个当前 conversation 的 expected，并以 Python、原生参考 SQL 和另写 SQL 交叉核对。55次评分中，6个正确控制接受、38个声明规则错误检出、4个重复行错误漏检。7个解释替代方案被拒绝：仅 comment 算 admin response、close 只看 active history、按实际微秒时长计算。这些拒绝不能单独证明原题业务错误。

额外输入和 expected seeds 仅替换隔离副本，原生 SQL checker 保留。fixture reward 不填入原题 native 或 audit。admin assignment/空正文、close 历史、时间锚点、分数秒与 tie 舍入仍未裁定；setup 会删除描述部分规则的项目 YAML。还需审查合成数据领域假设，例如 inactive 版本有不同 created_at。额外列的 include 与 no-extras 指引也仍待解释。

147条 trial 无 Harbor 异常，147次容器启动均退出，无新启动重试。三个早期只读探针的初始化失败和一次离线双重变异错误单列，不计作 benchmark 或模型失败。46个 payload、6个与执行副本一致且 Harbor 可加载的副本、参数/覆盖拒绝，以及7119条关键引用的绑定与 hash 核对通过。每题选 proof、payload、expected seed 各一份副本，共9份，实际验证改动/缺失/恢复的复用行为。执行 task/case 文件、输入、expected、日志、分数和提交前后产物直接绑定 hash；原始证据未修改。共享实现未改，既有285项全套测试未重复运行。

从仓库根目录准备单题的原始输入案例；输出目录必须是新的，默认各两次：

```bash
runtime/.venv/bin/python -B validation/ade/ade-bench-intercom001/prepare_cases.py \
  --output-dir artifacts/validation/intercom001-new \
  --case reference --case source-only --case duplicate-threads
```

Intercom002/003 使用各自目录的同名入口；003 的重复案例名为 `duplicate-conversation_metrics`。省略 `--case` 准备该题全部原始输入案例。

额外输入使用独立入口，默认三题正确控制；禁止混用原始 expected seeds：

```bash
runtime/.venv/bin/python -B artifacts/validation/intercom-event-fixture-preparation-v1/prepare_cases.py \
  --output-dir artifacts/validation/intercom-events-controls-new
runtime/.venv/bin/python -B artifacts/validation/intercom-event-fixture-preparation-v1/prepare_cases.py \
  --output-dir artifacts/validation/intercom-events-cases-new \
  --all-negatives --attempts 1 --concurrent 4
```

在 compute allocation 运行配置，保留原生 budget/shared verifier：

```bash
unset LD_PRELOAD
export PATH="$PWD/runtime/.venv/bin:/opt/apps/tacc-apptainer/1.4.1/bin:$PATH"
export APPTAINER_CACHEDIR="$PWD/.cache/apptainer"
export APPTAINER_TMPDIR="/tmp/dbt-bench-validation-${SLURM_JOB_ID}"
export OMP_NUM_THREADS=8
mkdir -p "$APPTAINER_TMPDIR"
runtime/.venv/bin/harbor run -c artifacts/validation/intercom001-new/config.yaml
```

也可从 login node 用 `DBT_BENCH_VALIDATION_CONFIG` 指定配置并提交 `scripts/slurm/gg-native-validation.sbatch`。这些入口用 Oracle，不调用模型；新结果需重新审查，不能覆盖冻结证据。实际 Qwen 工具上下文、完整可见性/提交收集和补充 audit 仍未认证。

[逐题审查](../validation/evidence/intercom-three-task-review-v1/summary.json)、[147条对账](../artifacts/validation/intercom-three-task-review-v2/reconciliation.json)、[失效检查](../artifacts/validation/intercom-three-task-review-v2/evidence-resume-check.json)、[冻结账目](../catalog/pools/validated-pilot-v11/candidates.jsonl)。
