# 阶段四验证、难度测量与新 split

本轮按用户的新授权执行。阶段四可审查原 test 侧任务；模型请求仅用于第二步规定的
Qwen3.6-35B-A3B 难度测量，不执行训练。阶段三 `train-phase3-v06`、旧 split、
旧原生分数及证据保持不变。新 split 先生成草案，必须在用户明确确认后才能冻结。

阶段四现已冻结 `test-phase4-v04`：37题正式审查、3题排除，新增20道合格候选；
与阶段三46题合成的固定测量群体为66题（ADE15、Spider16、DEB35）。实际群体
与版本在 `$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/population-v1.json`。
66题生产版本/镜像/原生测试/schema的静态核验全部通过，证据为同目录
`production-preflight-v1.json`；本项不计作模型测量。

阶段四输入为 `${SCRATCH_ROOT}/claude-two-version-pilot/results_test.json`。
40 题恰为原 42 个 DEB test 扣除 fix-marketing-attribution 和
product-return-rate-analysis 两个 native broken 题。27 agree 与 10 patched_pass
各做两次真实干净构建、当前 native14 和 audit14；2 gold_error 与
cross-sell-insights 的 degenerate_empty 只核对并保留排除记录。
磁盘上的 10 个题面变体目录实际为小写 `_v2`，按真实文件路径与 hash 登记。

并行分工为阶段四公共管线与四题族审查、客户/归因题审查、其余业务题审查；root
负责合并、GPU 测量和 split。所有新案例与大型证据在 Scratch。阶段四根目录：

```text
${ARTIFACT_ROOT}/validation/phase4-review-v1/
```

公共阶段四入口为 `phase4_prepare.py`、`phase4_review.py`、`phase4_account.py`、
`phase4_publish.py`、`phase4_report.py`；root 独占 publish 的 stage/freeze/report，
与批次 account 的 state 更新顺序执行。正式池保留旧 split=test，但单独标记
`eligible_for_measurement`，不提前生成训练集。生产题面版本位于
`catalog/phase4-task-versions.json`。

难度与抽签规则已在模型测量、真实抽签之前登记：

- [难度测量规则](../configs/difficulty-v1/measurement-rules.json)：固定模型 revision、
  Harbor 0.25.0 / Qwen Code 0.26.5、生成参数、每题四个独立 attempt、禁止训练。
- [当前 split 规则 v2](../configs/difficulty-v1/split-rules-v2.json)：随机种子 **2026110801**，
  family-V1、候选数超过 4 的 family 强制 train、10,000 次抽签和精确目标函数。
  [原 v1](../configs/difficulty-v1/split-rules.json)保留不改，供已提交实验的hash引用；
  [版本选择文件](../configs/difficulty-v1/split-rules-current.json)指向当前报告规则。

每题同时记录原生通过次数与 native＋已验证 audit 双通过次数，后者用于难度档位。
0/4 的题仍可留在候选池；不能根据模型成败删题。缺失证据、外部中断与普通任务预算
终止分开记录；不得将未知结果填成 0，也不得替换行为失败来提高分数。

v2明确每题须有4次有效attempt。已证实并非模型造成的初始环境故障、外部作业时限
中断不计入，须补跑相应slot并保留原trial、原因证据和替代关系。模型自行耗尽原生
时间/turn/token预算仍按实际native/audit结果计入，不补跑；模型破坏依赖也不能自动
归为基础设施故障。缺评分证据时先恢复验收或审查，不能捏造分数。

每题报告原生通过次数、两项都通过的次数k；分层仅按k取0、1–3、4档。audit确已执行
却无法判定时，保留null并单列unknown，不计为成功；尚未执行audit仍为pending，不能
冒充unknown进入草案。对原生通过且audit未知的u次，可能的k范围是[k,k+u]；草案列出
全部未知项及原因，并突出可能跨档的题。原生未通过的unknown不会提高k。未知audit
本身不替换有效模型attempt；优先对同一提交调查/重验。草案的名义档位仍仅由已确认k决定。

默认 `scripts/report.py` 已切换为 `training-candidates-v6-purpose-exclusions`。
即使历史train题的native/audit均通过、trace/context完整，只要实验用途是难度测量
或带 `training_prohibited=true`，导出资格仍为false。旧v5实现和原报告保留；
[新增过滤规则及回归](../patches/training-purpose-exclusion-v6/metadata.json)独立记录。

GPU 遵循 `../../CLAUDE.md`：Vista、`${ALLOCATION}`。用户现已明确授权将剩余测量
迁移到 **8 个同时提交的 gh 作业，每个申请 48 小时**，解决 gh-dev 每人只运行一个
作业、187份小作业反复排队的问题。依据与取消记录见
[调度修订](../configs/difficulty-v1/gh-batching-amendment-v1.json)，旧规则原文保留。
每批仅加载一次固定 vLLM，随后顺序调用单题原生 Harbor，每题4次独立干净运行，
并发1、无自动重试、原生预算不变。GET `/v1/models` 是唯一模型就绪探针。

剩余64题/256次分为8批；每批agent+verifier原生预算合计40.11–41.56小时，余量
6.44–7.89小时供模型启动、环境准备和采集。gh的QOS上限48小时；所有setup/collect
超时上限同时耗尽需536.62小时，超过8×48小时。因此余量是运行规划，不是全阶段
最坏时限保证；外部分配中断单列待审，不能填成模型0分，也不缩短原预算。
新批次按预登记方法固定共享服务seed，明确改变未启动配置的调度/seed身份；已完成
8次的原seed、结果与证据全部保留。原生任务与checker不变。

当前批次计划位于
`$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/gh-batches-v2/`。
`prepare_difficulty_gh_batches_v2.py`生成有限清单，`seal_difficulty_gh_batches.py`
封存代码/配置，`submit_difficulty_gh_batches_v3.py`只执行有限的Slurm提交，不实现调度或重试。
提交器识别TACC欢迎文字后的唯一job ID，并通过Slurm的comment/账户/分区/资源核对身份；
已经成功提交的批次保留且不再提交。前两版解析失败记录仍在原目录。
每批Slurm日志、模型日志、各题原始Harbor jobs均在Scratch；编译cache也按批隔离。

8批已提交为 **1130679、1130689–1130695**，每批attempt数依次为
24、28、28、36、36、36、36、32，总计256次。提交后核对均为Priority排队、
无作业依赖、无自动requeue；这不是已完成测量。当前队列记录见批次目录
`queue-verification-v1.json`，未来状态以Slurm和实际trial为准。

```bash
# 从login node提交一个新的、已经seal的计划；已提交计划会拒绝重复执行。
PYTHONPATH=src:scripts PYTHONDONTWRITEBYTECODE=1 \
  runtime/.venv/bin/python scripts/submit_difficulty_gh_batches_v3.py --plan <新的计划目录>

squeue -j 1130679,1130689,1130690,1130691,1130692,1130693,1130694,1130695
```

以下为先前gh-dev运行的历史入口，已启动的两题证据继续使用；后续采用上述gh批次：

```bash
export PYTHONPATH=src:scripts
export PYTHONDONTWRITEBYTECODE=1

# 当前入口支持阶段三冻结的46题；仅准备配置，不调用模型。
runtime/.venv/bin/python scripts/prepare_difficulty_run.py \
  --source ade --task ade__ade-bench-ana-eng005 \
  --attempt 1 --attempt 2 --attempt 3 --attempt 4 \
  --output "$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/<新的运行名>"

# 核对并封存配置；仍不调用模型。
runtime/.venv/bin/python scripts/seal_difficulty_preflight.py --run <已准备目录>

# 在login node提交已准备目录。
sbatch --parsable scripts/slurm/gh-dev-difficulty.sbatch <已准备目录>
```

`verifier.collect` 在模型 agent 结束、原生验收开始之前保存合同声明的数据库与 WAL、
有大小限制的项目源文件与 dbt 元数据；原生 Qwen adapter、任务说明、原生 tests 不改。
只读采集不调用 SQL，也不复制整个 `/app`。模型提交的 audit 在独立副本执行；
需要重建的 dbt 工作流使用已捕获源码，不能信任候选自己写的 manifest，不能将重建
成功冒充原模型执行过某个明确要求的步骤。

首个作业 **1130223** 为 ADE ana-eng005 的 attempt 1–4；其状态和所有模型证据在
`$SCRATCH/dbt-bench-storage/artifacts/difficulty/qwen36-v1/ade-ana005-a1-4/`。
作业 **1130233** 为 f1003 的 attempt 1–4，**1130274** 为 DEB coupon 的
attempt 1。Spider workday002 的 attempt 1–2 位于
`spider-workday002-a1-2/`，现已提交为 **1130410**；首次因
`QOSMaxSubmitJobPerUserLimit` 被 Slurm 拒绝的记录仍保留在
`submission-rejection-v1.json`。实际查询的 `qdevelopment` 限制为
每用户最多 3 个已提交作业、同时运行 1 个作业，最长 2 小时。此处为历史调度；
后续按用户明确授权迁移至gh。
这两个ADE作业现均已完成，最终8份实际提交audit经第五版对账通过：ana-eng005
原生4/4、共同通过4/4；f1003原生1/4、共同通过0/4。后者原生通过的提交有4行答案，
完整合同要求3行，audit保留0分。另有一次源码未在model-paths内交付所需模型；
其审计重建补回原生已有的锁定`dbt deps`步骤后，按同一冻结checker判0，未重跑模型。
完整原始trial与最终收据选择见 `reconciliation-v05.json`。
作业 **1130656** 原计划对airbnb005执行attempt 1–4。
**1130274、1130410、1130656均在PENDING状态取消，未执行的slot并入gh批次**。
实时进度以
[difficulty-status](../validation/difficulty-status.json)及Slurm为准，不能由准备目录数量推断完成数。
独立 CPU oracle fixture 已执行采集链路检查，完整保留数据库与315个源码/元数据文件，
数据库采集前后 hash 一致。该 fixture 没有模型请求，不计作难度运行。
后续干净重建与冻结 audit 实测：ana-eng005 的 fixture 为 native1/audit1；
f1003 的原生 oracle fixture 为 native1/audit0，原因是答案有4行而已审合同要求3行。
两者均在独立目录保存，没有替换正式候选解或改变原生结果。

`reconcile_difficulty.py` 对账已提交的阶段三模型运行；其输出将未运行、缺失 audit、
异常待审与实际失败分开。`reconcile_difficulty_v2.py` 同时支持最终阶段四扩展池，
加 `--pool` 时核对完整候选群体，仍不调度或重试模型。
阶段四全部审查完成后，使用以下第二版入口；已提交的第一版代码与配置保持不变：

```bash
runtime/.venv/bin/python scripts/build_difficulty_pool.py \
  --phase4-manifest <最终test-phase4冻结manifest.json> --output <新population.json>
runtime/.venv/bin/python scripts/prepare_difficulty_run_v2.py \
  --pool <population.json> --source deb --task <task_key> \
  --attempt 1 --output <新的SCRATCH运行目录>
runtime/.venv/bin/python scripts/seal_difficulty_preflight_v2.py --run <运行目录>
sbatch --parsable scripts/slurm/gh-dev-difficulty-v2.sbatch <运行目录>
```

第二版明确允许通过审查的原 test 题进入难度测量，生产任务只使用已登记的新题面版本。
`prepare_difficulty_audit_v2.py` 生成实际提交的独立源码重建；不同题的 audit 仍须
使用各自已认证 checker 的接口和合同，不以一次重建或原生1分替代完整验收。

后续准备入口 `prepare_difficulty_run_v3.py` 使用可明确记录路径不存在的新采集器；
loyalty另存题面提及的第二个项目目录作等价性调查，不能把该路径自动认证为正确。
`prepare_difficulty_plan.py` 可一次准备全部剩余attempt配置，复用显式列出的已准备
运行并拒绝重复slot；它不提交或运行作业。原187份配置按ADE最多4次、Spider最多2次、
DEB每组1次保存为历史。新gh计划引用其中已封存的生产任务，单题统一4次，
配置仅修改attempt分组及输出位置，原生预算保持不变。

`reconcile_difficulty_v5.py` 保留Harbor原始分数及typed error policy。已正常验收的
AgentTimeout不一概作无效；外部/未知异常仍待裁定。普通交付失败只有实际完整
capture、明确合同规则和已执行独立控制才能取得audit0；原checker结果与新失败
适配器结果分开绑定，缺失日志不能假装失败。任何oracle/nop校准都显式标
`fixture_only=true`，不能计入四次模型尝试。

第五版对账会核对收据内部的原trial、采集、准备配置、实际Qwen adapter、源码重建、
checker输入和交付规则。只改收据表层的fixture标签或trial ID不能把CPU校准包装成
模型结果。历史第四版的这一反例和第五版拒绝该反例的实际记录分别保留在
`operations-review-v1/source-binding-counterexample.json` 与
`receipt-binding-v5-conformance-v1.json`。

DEB按逐题交付要求选择评分输入：由原生verifier构建所交dbt源码的题，对完全相同
的源码做独立干净重建后评分；要求已物化输出且原生只读数据库的题，评分原始提交
数据库。原始库、WAL和源码均保留，不把缺少pre-native结果表一概判0。
明确要求的backend检查、profile设置、参考模型先构建等原动作单独核对，重建不能
替代这些动作。新增逐题delivery合同和“只交源码、不执行agent dbt”的实际正控制
保存在各DEB适配证据目录；旧原生结果和旧适配证据不改。

基础工具环境保持标准库依赖；需要解析实际 dbt YAML 的重建/审计测试使用已锁定的
Harbor runtime。基础环境会明确跳过这些运行时测试，完整检查应运行：

```bash
PYTHONPATH=src:scripts PYTHONDONTWRITEBYTECODE=1 \
  runtime/.venv/bin/python -m unittest discover -s tests
```

当前 split 草案入口为 `scripts/propose_difficulty_split_v2.py`，原入口保留作历史。
尚未进行真实候选抽签。新入口要求阶段四审查完成、每个候选四次有效执行齐全、
audit均实际执行且版本一致；接收仍引用旧规则的原始测量报告，按v2重新核算计数，
不修改原始报告。另输出`task-measurement-counts.jsonl`和`audit-uncertainty.jsonl`。
先按排序后的无候选
family 用同一预登记随机数流各抽一次 25% test，然后对有候选 family 抽 10,000 次，
强制 train 的 family 不消耗抽签；以精确分数计算三个维度各类别
`abs(test候选数 / 该类别总候选数 - 1/4)` 之和，取首次达到最低值的分配。
分母不是 test 内部的类别构成。只在候选题上优化，但所有非候选题跟随所属 family。
工具仅写独立草案与旧 split 快照，没有冻结入口。

最终汇报必须包含候选池、来源/workflow/难度分层、全部 family 分配、新旧 split
差异和仍不可用任务。**DEB 的 train 和 test 使用同一个零售数据库，评估的是熟悉
数据上的新任务能力，不是对未见数据库的泛化。**
