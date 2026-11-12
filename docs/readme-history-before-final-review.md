# README 历史快照

保存最终收尾前的历史进度叙述，原有数量与 pending 标签属于各自执行时点。当前状态请读仓库 README 与最新冻结池。

# dbt-bench

固定 Harbor + Qwen Code 的 DuckDB benchmark 工具仓库。当前覆盖清单为 **135 个候选**：ADE 16、Spider2-DBT 55、data-eng-bench 64。任务运行、并发、attempt 和 retry 交给 Harbor；这里负责来源准备、验证证据、结果对账和训练候选索引。

**原生阶段一审查已完成，完整实现验收与语义认证尚未完成。** [native-phase1-v14 冻结报告](../catalog/pools/native-phase1-v14/report.md)覆盖 131/131 个非隔离任务的全部 14 项检查，并保留 4 个 broken 隔离题及全部 135 题账目。活跃检查为 1177 pass、425 fail、232 实际 gap、0 pending；原生结论为 1 validated、130 weak、4 broken。原生分数和 artifacts 保留，阶段一没有模型请求。阶段二独立 audit 已开始，见[阶段二进度](../docs/phase2-progress.md)。[audit-phase2-v13](../catalog/pools/audit-phase2-v13/report.md) 已累计冻结 120/129 题新增 audit 审查；全部候选的 audit 为 25 validated、110 pending，协议级 train 任务池为 17 题。通过 audit 的 test 任务仍按原 split 排除。Xero001 真实时钟见证与 Repeat Purchase 修正见证均为 audit1/native0，不能通过 audit 覆盖原生0而成为训练候选；每条 trace 仍须满足完整候选策略。v01 发布失效及修复证据保留于[恢复记录](../artifacts/validation/phase2-review-v1/publication-recovery-v1/completion.json)。详见 [阶段一完成报告](../docs/phase1-completion.md)、[机器状态](../validation/delivery-status.json)和[逐题账本](../docs/validation-status.md)。

**Spider 已完成当前 v4 环境下的 54 题原生审查。** DuckDB 1.2.2、冻结包和 nba001 的 pandas 2.2.2 保持不变，11 题环境 fail 如实保留。指定 16 题经 locked Qwen shell 各两次干净输入构建，32 次原生 reward 均为 1，逐列重复比较一致；lever001 不补文件直接运行。五个日期敏感题在真实时钟下均为 0，限定构建进程使用 2024-09-08 时钟后均为 1；quickbooks001 两次为 0，输出不稳定。54 题初始数据库探针全部完成。公开 SQL/gold 一致不算独立语义证据，copy-gold 不认证真实解；有限原始输入重算与逐规则案例独立记录。已有 Asana/Workday 参考运行保留，按要求不新增重复认证；能否绑定当前身份逐题记录，必要的新变异案例仍在干净副本建立各自基线。[恢复范围、证据和命令](../docs/spider-resume-v4.md)、[环境版本历史](../docs/spider-environment-v4.md)。

Harbor 0.25.0 / Qwen Code 0.26.5 已锁定，135 个候选的固定资源与 Apptainer 镜像均已取得。此前 Qwen3.6-35B-A3B 已在三来源执行真实 smoke；这些历史结果按各自环境身份保存，不自动迁移到新版本。家族 split 为 93 train / 42 test，既有 1 条训练候选。[分组和历史暴露](../docs/family-review.md)、[历史运行结果](../docs/run-results.md)、[旧 v14 采样池](../catalog/pools/validated-pilot-v14/manifest.json)仍保留。

以下逐题摘要记录各自执行时的版本和范围；当前状态以版本化冻结报告及机器账本为准。

ADE ana-eng005 已通过声明的 14 项原生验证协议，进入独立的 [native-control-v1 冻结池](../catalog/pools/native-control-v1/manifest.json)。累计保留 19 个案例和 6 个 oracle/nop 控制运行；当前审查的 4 个参考案例、4 种合法等价形式通过，10 个独立错误被拒绝。其修复任务保留现有九列接口，新增字段案例按实际交付指引另作证明和重跑；旧判断与原始分数保留。该任务尚无真实模型 trial，不能据此生成训练候选。[逐题范围与复跑命令](../validation/ade/ade-bench-ana-eng005/README.md)、[冻结报告](../catalog/pools/native-control-v1/report.md)。

ADE f1003-hard 新增 24 次独立案例运行：6 次正确/等价提交接受，12 次错误拒绝，6 次错误漏检；另外保留先前 2 次错误接受和 2 次准备失败。其 native 判为 weak、audit pending，未进入采样池。全量仍缺 118 个真实构建见证、1633 项 native 协议检查。[该任务审查与复跑](../validation/ade/ade-bench-f1003-hard/README.md)。

ADE f1011 新增 18 次运行：8 次接口错误漏检、8 次独立错误拒绝、2 次原生参考 fixture 接受。其 native 判为 weak；选项 B 仍待核实，参考字符串未计作真实诊断见证。[逐题范围与复跑](../validation/ade/ade-bench-f1011/README.md)。

ADE ana-eng004 完成 26 次追加验证，确认重复行漏检及等价仓库表示被拒绝，native 判为 weak。上游产品过滤造成 16 条库存缺产品详情，完整见证仍待确认；不纳入采样。[逐题范围与复跑](../validation/ade/ade-bench-ana-eng004/README.md)。

ADE ana-eng006 完成 30 次逐题验证：fact_inventory 的行数保护检出重复，但 obt_product_inventory 的显式唯一性存在漏检。另有等价表示拒绝，native 为 weak；完整产品域语义待确认，未增加采样任务。[三个模型的审查与复跑](../validation/ade/ade-bench-ana-eng006/README.md)。

ADE f1002 的 4 次复现确认：省略 YAML 必需的 podium 明细列会得满分，加入独立重算正确的 p1/p2/p3 却被比较器拒绝。该明确题意/评分冲突记为 native broken；原生文件与分数保留。[冲突证据及复跑](../validation/ade/ade-bench-f1002/README.md)。

ADE f1009 完成 28 次年龄任务验证，取得两次真实构建见证，并确认空结果、缺组、重复、额外列和缺模型源码均可获原生满分，判为 weak。所有日期与时区按实际执行记录；独立补充 audit 尚未认证。[年龄任务证据及复跑](../validation/ade/ade-bench-f1009/README.md)。





ADE f1010 与 medium 各完成 30 次原始输入评分案例，均确认提示分支、重复行和整数类型漏检，native weak。基础版增加完整构建见证；medium 进站语义仍待裁决。另有 8 次本地案例脚本失败保留 null reward。[基础版](../validation/ade/ade-bench-f1010/README.md)、[medium](../validation/ade/ade-bench-f1010-medium/README.md)。

ADE airbnb007 完成 58 次六模型评分：48 次独立错误检出 26、漏掉 22，另保留 2 次评分前变异脚本失败。其 native 为 weak；无评论房源范围及增量新输入等仍待确认，完整见证 unknown。[逐题范围和复跑](../validation/ade/ade-bench-airbnb007/README.md)。

Intercom 三题完成92次原始输入评分和55次额外输入 fixture，均确认重复行漏检，native weak。事件解释及实际 Qwen 协议仍未完成。ADE16题已有裁决但不等于全部协议完成，采样任务仍为2。[范围与复跑](../docs/intercom-review.md)。

Daily Order完成32次原始数据验证，取得完整构建见证；18次业务错误检出8、漏掉10，另8次接口/工作流错误全部漏检，native weak。该题属于test split，audit仍pending。[逐题证据与复跑](../validation/deb/dbt-daily-order-summary/README.md)。

Weekly Sales完成56次原始数据验证，取得三层模型的完整构建见证；40次业务错误检出20、漏掉20，10次类型/交付错误检出4、漏掉6，native weak / audit pending。原生满分不足以纳入训练候选。[逐题证据与复跑](../validation/deb/dbt-weekly-sales-growth/README.md)。

Channel Revenue的v2完成44次验证：30次业务/时间错误检出10、漏检20，10次交付错误检出8、漏检2。native weak / audit pending；渠道键与缺失日期政策未解决，完整见证仍unknown。[范围与复跑](../validation/deb/dbt-retail-task-01-channel-revenue/README.md)。

## 安装与本地检查

当前共享实现的[干净导出验收](../artifacts/verification/clean-export-phase2-v1/completion-v2.json)已通过离线锁定安装及388项测试（387通过、1项旧仓库缺失跳过）。缺少任务资产时135题全部pending、采样资格0；本项不代替完整发布包或逐题audit认证。

模型、依赖缓存、虚拟环境和 SIF 现已迁至 `$SCRATCH/dbt-bench-storage`，原 Work 路径保留链接；运行命令继续使用原路径。任务、原始 rollout 和部分验证证据保留在 Work；八个已完成验证目录及新增逐题运行直接存于 Scratch。迁移校验及具体路径见[本机存储布局](../docs/storage.md)。


DEB 的 64 个任务现均绑定固定 Harbor server 依赖的镜像。最近 62 题的环境更新已完成 248 次 reference/nop 和 30 次语义案例回归；每题环境记录仍明确区分原生运行证据与待完成的实际 Qwen 上下文认证。[本轮范围与逐题证据](../artifacts/validation/deb-server-freeze-v1/review-summary.json)。

ADE 最新 trial 的 19 次主请求和 4 次 memory 子代理请求现已逐条核验，客户端实际请求上下文完整；原生 ATIF 漏掉 memory catalog；单独版本的离线转换已保留它及全部原始事件，派生 ATIF 的内容保真为 complete。原生记录器的 SDK 重试漏记也已通过实际 HTTP fixture 复现，其他轨迹不自动获得认证。[范围、证据与可执行命令](../docs/trace-fidelity.md)。

结算日期 FX 题已完成九次实际案例：六个独立错误中原生检出两个、漏掉四个，现为 weak。原始粒度与参考/grader 粒度、DECIMAL 与 DOUBLE 类型仍有待澄清，未认证完整可解性。[逐题结论与复跑命令](../validation/deb/dbt-exchange-rate-settlement-date/README.md)。该轮实现测试 285 项通过；此前 193 项版本的干净导出安装后 192 项通过、1 项因缺旧仓库跳过。

DQ macro enforcement 题完成 11 次评分案例，8 个独立错误中原生检出 2 个、漏掉 6 个，现为 weak；另保留 2 次独立证明工具在评分前中止的 null reward 运行。完整 DQ 语义和实际 Qwen 上下文仍未认证。[逐题范围与复跑命令](../validation/deb/dbt-dq-macro-enforcement/README.md)。

Session attribution 又完成两次原始数据参考和 19 次额外输入 fixture：16 个独立错误中原生检出 14 个、漏掉 2 个，另误拒绝一个可空额外列的合法答案。原始数据仅有单触点归因，不能用其通过证明多触点规则；新增 fixture 分数单列，audit 仍 pending。[语义覆盖与复跑命令](../validation/deb/dbt-session-attribution/README.md)。

Session 的实际 Qwen pipe/PTY、原生 verifier 和提交产物兼容性也已核对。 初始文件及 dbt 缓存的声明范围可见性检查也已通过；不作通用信息流或网络隔离保证。保留源码从初始数据重建成功，但扩大到新增 staging 模型的构建失败单列保留；该题仍为 native weak / audit pending。[执行证据与诊断命令](../validation/deb/dbt-session-attribution/README.md)。

Session 又完成八次原始数据工作流验证：两个重复构建正例通过，六个错误中原生检出 schema 错误、漏掉其余五个。源码缺失、view 物化、新增 staging、查询降序和重复运行追加行均有独立执行证据；仍未认证 audit。[范围与复跑命令](../validation/deb/dbt-session-attribution/README.md)。

Session 的实验性独立 checker 已复查 29 份保留案例，并完成七份提交的评分前后只读采集：两次完整参考通过、两次 nop 拒绝、两次普通 oracle 和实际 Qwen 提交重放保持 unknown。后者数据检查通过，但缺完整工作流证据；audit_reward 仍为 null。当前四项 audit 检查通过、十项 pending，native weak / audit pending 不变。启动与配额失败均保留，未增加训练候选。[范围、证据与命令](../validation/deb/dbt-session-attribution/audit_v2/README.md)。

Spider workday002 的模型提交已从干净输入重建；此前完成 12 次语义案例，七个独立错误中原生检出两个、漏掉五个。新增两次正确控制和三个删除关系负例均构建全部 50 个模型，三个错误全部被原生漏检。实际 Qwen pipe/PTY、原生 verifier、623 个文件的提交重放及声明范围内的隐藏材料检查已完成；原生仍 weak，audit 与空聚合表示仍待完成。实际 trace 还缺少最后一个 memory 工具的返回正文，继续标为 incomplete。[逐题证据和复跑命令](../validation/spider2/workday002/README.md)。

Workday 又完成四个输出值正确的工作流负例，原生全部漏检：仅执行 11/50 个模型、删掉目标模型源码、绕过 dbt 直接写表、输出 view。两次参考与两个合法表示控制均通过；另保留一次案例构造错误导致的 null reward，没有把它计作模型失败或漏检。补充 audit 尚未认证。[工作流审查](../validation/evidence/workday-workflow-review-v1/summary.json)。

Workday 的实验性补充 checker 已独立审计 25 份保留案例产物：检出 10 个数据错误和 4 个工作流错误，4 个完整正确案例通过，7 个正确案例因缺工作流采集证据保持 unknown；原模型的空字符串语义仍待裁决，audit=null。新版又完成评分前后只读采集、两次真实构建、两次 copy-gold 和两次 nop 控制，以及一次原模型提交重放；新增 6 个验证 trial 和 1 次诊断重验，没有新增模型调用。五项 audit 协议检查通过、九项待完成，整体仍 pending。[可执行入口与范围](../validation/spider2/workday002/audit_v2/README.md)。

Workday 的实际工作流已从原始 call ID、工具结果和 dbt 日志核对：完整 50-model 构建后修改目标叶模型并单独重建，最后运行测试。最终 test artifact 不再被当作此前没有构建的证据。固定材料未规定空 VARCHAR 是否等价于空聚合，保持 unknown；整体 audit 和采样资格不提升。[观察入口与范围](../validation/spider2/workday002/workflow_observation_v1/README.md)。

辅助工具仅使用 Python 标准库，要求 Python 3.11+；本次使用 Python 3.13.11。根目录 `uv.lock` 是实际生成并验证过的工具环境锁，不包含 Harbor runtime。

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --locked
uv run --locked python scripts/test.py
uv run --locked python scripts/doctor.py
uv run --locked python scripts/validate.py
uv run --locked python scripts/report.py
```

已有 Python/uv 缓存时，上述命令可加 `--offline`。无需模型、Docker 或旧仓库即可运行核心测试和读取现有清单。`scripts/test.py` 保存每项测试日志和代码 hash；可选的旧 comparator 复现测试在 `../training` 不存在时跳过，其余新比较器测试独立运行。

阶段一合并由 root 执行：`scripts/stage_phase1_reviews.py` 用重复的 `--task` 参数选择已审查任务，以 `--output` 指定新的暂存目录；它核对当前身份和实际证据，不接受旧机械模板。随后 `scripts/phase1_publish.py --mechanical <暂存目录>` 每 10 题发布一版；`--verify <冻结目录>` 核对已发布快照。三个入口均可用 `runtime/.venv/bin/python <脚本> --help` 查看参数。案例执行和对账分别共用 `prepare_mechanical_cases.py`、`reconcile_mechanical_cases.py`，运行仍交给 `harbor run -c`。

Harbor 运行环境单独放在 `runtime/`，固定 **Harbor 0.25.0**，需要 Python 3.12–3.13。[runtime/uv.lock](../runtime/uv.lock) 已实际生成并安装，解析时核对了已固定的 Harbor wheel SHA-256。安装后应用已记录的 Apptainer 环境、Qwen 安装和 converter 补丁：

```bash
uv sync --project runtime --locked
runtime/.venv/bin/python scripts/apply_runtime_patches.py
runtime/.venv/bin/python scripts/check_runtime.py
```

ARM64 Apptainer 模型运行还需要固定的 Qwen 安装包。在已分配的 compute node 准备一次：

```bash
runtime/.venv/bin/python scripts/freeze_qwen_dependencies.py \
  --output-dir artifacts/execution/qwen-install-prepared
runtime/.venv/bin/python scripts/build_qwen_bundle.py \
  --installation artifacts/execution/qwen-install-prepared \
  --output-dir .cache/qwen-runtime/locked-v1
runtime/.venv/bin/python scripts/check_runtime.py --require-locked-installation
```

已有 bundle 时运行最后一条核验即可。准备程序保留独立输出，不覆盖旧证据；`prepare config --backend apptainer` 会核对来源锁中的 archive/inventory/installer hash 并绑定 manifest，缺失或变化时失败。[安装范围与证据](../runtime/qwen-code/README.md)。

后续安装使用 `uv sync --project runtime --locked`，然后重新执行幂等补丁入口。容器后端可选 Docker，或 TACC Apptainer 配合完整 task SIF；另需 Git LFS、数据库、依赖包和 Qwen npm 包。Qwen 固定 **0.26.5**，已从安装的源码确认 Harbor 原生 `agents[].kwargs.version` 机制。`check_runtime.py` 的 error-only 回归现已通过；它只证明该 fixture 保留错误文本和 call ID。完整事件回归使用 `runtime/.venv/bin/python scripts/check_converter.py --output-dir artifacts/converter/<new-name>`，明确区分预期完整和已知不完整的事件；不认证实际模型上下文。

运行补丁以原子替换写入，避免修改 uv 硬链接共享的缓存文件。锁定的小型回归记录在 `validation/evidence/runtime-patch-assets/` 按内容 hash 保留相同副本，干净安装不依赖大型运行 artifacts；原路径存在但 hash 不符时仍会失败。最近一次[干净导出检查](../validation/evidence/clean-export-lifecycle-v2/summary.json)实际完成锁定安装、三处文件补丁、重复应用、原生 schema/converter 和测试。缺少 task assets 时，`validate` 保持 pending，`report` 因证据缺失/过期返回 1 并输出零训练候选；这不能当作任务已通过。

TACC 上也可以在已分配的 compute node 普通终端执行固定准备入口：

```bash
.venv/bin/python scripts/bootstrap_runtime.py
```

它先确认实际执行条件，再生成缺失的 runtime 锁、核对已固定的 Harbor wheel hash、以 `--locked` 安装，并获取 ADE airbnb005、Spider workday002、DEB session-attribution 三个试点。已有 runtime 锁不会被自动重新解析。每次运行在 `artifacts/bootstrap/<timestamp>/` 保留步骤日志和 `summary.json`；失败不会被记成完成。该入口不构建镜像、不运行模型或 benchmark。下一步需要审查下载的原生构建材料并准备完整的 aarch64 任务镜像。

## 清单与来源准备

已提交的 `catalog/legacy/` 保留原始清单的内容，`catalog/import.json` 保存旧文件原始字节 hash、实际 Git revision 和数量对账。旧代码仍在 `../training`，没有修改。需要重新导入时：

```bash
uv run --locked python scripts/prepare.py import-legacy --legacy ../training
```

重复执行保持已有准备和审查字段；输入版本或文件 hash 改变时拒绝覆盖现有 catalog，应创建新版本。不会读取旧 `validated_tasks.json` 作为认证证据。

从 [sources.lock.yaml](../sources.lock.yaml) 的精确 commit 获取任务。Git 使用 sparse checkout；HF 的按需 Git blob 下载不兼容时，使用同一 commit 的 raw 地址并核验 Git blob hash，LFS 资产按锁定 pointer 的 SHA-256 和大小校验：

```bash
uv run --locked python scripts/prepare.py tasks --fetch
# 或逐个准备试点
uv run --locked python scripts/prepare.py tasks --fetch \
  --task ade__ade-bench-airbnb005 \
  --task spider2__workday002 \
  --task deb__dbt-session-attribution
```

也可以使用事先下载的、干净且 revision 完全匹配的 checkout：

```bash
uv run --locked python scripts/prepare.py tasks \
  --source-root ade=/path/to/ade-harbor-datasets \
  --source-root spider2=/path/to/hf-harbor-datasets \
  --source-root deb=/path/to/data-eng-bench
```

原始任务保存在 `.cache/raw/<hash>/`，运行副本在 `tasks/<task_key>/`，文件和 Dockerfile 清单在 `catalog/prepared/`。准备保留原生 Dockerfile、预算、project 布局和 verifier。缺来源、脏 checkout、commit 不符、未解析的 LFS pointer、逃逸 symlink、资产校验错误或补丁上下文不匹配均明确失败；独立任务继续收集结果。失败清单在 `artifacts/prepare/result.json`。

`prepared` 表示取得并核对任务源码，不表示镜像已构建、外部下载已固定或任务已认证。原生 Dockerfile 继续执行初始化；不把旧 TACC 目录复制逻辑移入默认流程。外部数据库和 dbt packages 的实际 hash、构建镜像 ID、初始化效果和 agent 可见内容必须在后续环境证据中记录。

## 验证

TACC 先执行 `module load tacc-apptainer/1.4.1`，按 [Apptainer 说明](../docs/apptainer.md) 为每题绑定完整 SIF，然后给 validate/config 添加 `--backend apptainer`。该选项映射到 Harbor 原生 `singularity` 后端；默认 Docker 路径仍用于参考配置。

全量账本更新不会默认运行容器：

```bash
uv run --locked python scripts/validate.py
```

每题有 `validation/<source>/<task>/contract.yaml` 和 `record.json`。尚未审阅的 contract 明确标识 unknown 和待复现风险；已审阅的要求与实际证据逐项绑定，没有扫描 test 文件猜输出表。`validation/risk-register.json` 覆盖计划列出的 ADE、Spider、DEB 和旧 strict/trace 问题。

取得任务与运行条件后，由 Harbor 在隔离任务副本上执行两次 oracle 和两次 nop：

```bash
export PATH="$PWD/runtime/.venv/bin:$PATH"
runtime/.venv/bin/python scripts/validate.py --execute --task ade__ade-bench-airbnb005
runtime/.venv/bin/python scripts/validate.py --execute --task spider2__workday002
runtime/.venv/bin/python scripts/validate.py --execute --task deb__dbt-session-attribution
# 明确要求全量原生验证时才使用：
runtime/.venv/bin/python scripts/validate.py --execute --resume
```

执行副本、配置、Harbor 原始结果和日志保存在 `artifacts/validation/`，不会覆盖正式任务或旧 trial。oracle/nop 的 reward 只是调查材料，**不会自动把正例、语义、稳定性或任务结论改成通过**。Spider copy-gold 只能记为答案 fixture；需要真实 dbt 构建见证。

需要把一组已绑定镜像的任务放入同一原生 Harbor job 时，可先生成明确清单。输出目录必须是新的；它保存原始 task 副本、配置和预定的每题 2 oracle + 2 nop，执行与并发仍由 Harbor 负责：

```bash
runtime/.venv/bin/python scripts/prepare_reference_batch.py --backend apptainer \
  --task spider2__netflix001 --task spider2__shopify001 \
  --output-dir artifacts/validation/my-reference-batch
runtime/.venv/bin/harbor run -c artifacts/validation/my-reference-batch/config.yaml
runtime/.venv/bin/python scripts/reconcile_validation_batch.py \
  --manifest artifacts/validation/my-reference-batch/manifest.json \
  --output artifacts/validation/my-reference-batch/reconciliation.json
```

逐题审查方法、case 格式、证据绑定和环境检查见 [验证协议实施说明](../docs/validation.md)。已审查的 case 通过下列命令执行；负例独立证明必须发生在原生 verifier 之前：

```bash
runtime/.venv/bin/python scripts/validate.py --execute \
  --task '<task_key>' --case 'validation/<source>/<task>/cases/<case>/case.json'
```

原生 shared/separate 模式不改写。自定义 case 的 proof gate 当前仅支持 shared；separate 的产物传递尚未实现，会明确拒绝，不声称 fresh verifier 继承了 agent 文件系统。

## Harbor + Qwen 正式运行或小规模 smoke

Vista 单节点 GPU 调试另见 [gh-dev smoke](../docs/gh-dev-smoke.md)：固定 Qwen3.6-35B-A3B、独立 vLLM/CUDA 锁和两小时提交脚本。最近完成的作业 **1123746** 已释放 allocation，总占用 4 分 18 秒；ADE 客户维度模型 trial 约 50 秒，19 次 assistant 事件、24 个工具调用，原生和补充 audit 均为 1。此前 DEB 运行及启动失败均保留。最新 ADE 的派生 ATIF 和实际请求上下文已核验；按冻结 split 重新选样后有 1 条候选，原实验 ID 和分数保持不变。Spider workday002 作业 **1124050** 已完成调试并释放分配：attempt-2 的模型 trial 成功、native=1；父批次仍保留首次启动失败的 FAILED/1:0。其 trace 和业务验收仍不合格，不能据 reward 进入训练候选。[运行证据](../validation/evidence/customer-environment-review-v1/model-summary.json)。默认 smoke 为 DEB session-attribution；可在 login node 明确选择已审查的 ADE 任务：

```bash
sbatch --parsable --export=ALL,DBT_BENCH_SMOKE_TASK=ade__ade-bench-ana-eng003 \
  scripts/slurm/gh-dev-smoke.sbatch
```

选择其他任务前需要完整、当前的 submission contract；缺失或过期时在模型加载前失败，不自动切换题目。

当前只读检查发现，历史ana-eng003、session-attribution和workday002三份提交采集声明的contract hash已过期，直接重跑相应gh-dev smoke会在模型加载前拒绝。历史trial的采集声明副本仍保留；新版本声明与采集路径复核是尚未完成的实现验收项。[实际检查](../artifacts/verification/submission-declaration-review-v1/review.json)。

已支持显式选择版本化声明，保留历史声明。先检查声明与当前任务、合同及路径审查证据是否一致；下面的命令只检查声明，不启动模型或采集文件：

```bash
runtime/.venv/bin/python scripts/submission_declarations.py \
  --task ade__ade-bench-ana-eng005 \
  --spec configs/submissions/ade__ade-bench-ana-eng005.json \
  --output artifacts/declaration-check.json
```

新版本通过逐题路径复核后，可用 `DBT_BENCH_SUBMISSION_SPEC=<声明路径>` 为 gh-dev smoke 显式选择，或在 `prepare_reference_batch.py --capture-submission` 后加 `--submission-spec <声明路径>`。声明校验通过不代表实际提交已经完整采集；每个 trial 仍须检查 Harbor artifact manifest 和文件 hash。该入口已有当前 ana-eng005 声明校验和过期合同拒绝测试，尚未替代上述三份过期声明。

先生成模型、endpoint、部署标识和 task 路径已解析的配置。`configs/{ade,spider,deb}.yaml` 是来源模板，模型参数通过该命令填入，**不要直接把未填模型的模板当实验配置**。

```bash
export OPENAI_API_KEY='your-key'
export DBT_BENCH_RUN_DIR="$SCRATCH/dbt-bench-storage/artifacts/runs/ade-smoke"
runtime/.venv/bin/python scripts/prepare_model_run.py \
  --source ade --task ade__ade-bench-ana-eng005 \
  --model openai/your-model \
  --endpoint http://your-model-host:8000/v1 \
  --deployment checkpoint-or-deployment-revision \
  --backend apptainer --attempts 1 --concurrent 1 \
  --submission-spec configs/submissions/ade__ade-bench-ana-eng005.json \
  --output-dir "$DBT_BENCH_RUN_DIR"
runtime/.venv/bin/harbor run -c "$DBT_BENCH_RUN_DIR/config.yaml"
```

Spider 使用 `--source spider2`，DEB 使用 `--source deb`，通过重复 `--task` 明确选择任务。提交采集声明只支持一题；更换任务时必须同时选择其经过复核的声明，或省略声明并接受尚未具备完整提交采集的状态。实际 endpoint 必须能从 task 容器访问。Docker 使用 `--backend docker`；TACC 上仍须在 compute allocation 内执行 Harbor。

`prepare_model_run.py` 只生成配置，Harbor 负责运行。目录名作为明确的 job name，最终配置和提交声明一起参与实验 hash；已有目录拒绝覆盖。旧 `prepare.py config` 存在计算 hash 后修改 job name 的身份缺陷，不能直接作为新正式运行入口；新入口保留其原始生成文件，另外写出自洽的 `config.yaml` 和 `manifest.json`，历史实验记录保持原样。

配置与 companion manifest 保存实验 ID、原始/适配任务来源、补丁、catalog/split/validation hash、客户端配置和预定 task/attempt。没有强行覆盖原生预算、内存或 separate verifier。服务端采样参数、thinking/context limit 和实际环境版本尚未知时保留 unknown，运行后补充证据，不把默认值猜成实际值。API key 不写入 companion manifest。

## 报告与训练仓库交接

读取真实 trial 的 native reward-validity 和 ATIF schema 需要固定 Harbor。此时使用 `runtime/.venv/bin/python scripts/report.py`；轻量环境缺 Harbor 会保留 unknown 并导出零候选，不代表模型失败。

无 job 时仍能输出清单与验证覆盖：

```bash
uv run --locked python scripts/report.py --output reports/catalog
```

有运行结果时必须提供预先生成的 experiment manifest。Harbor 的本地 UUID/目录名不能可靠推导 attempt/retry，需要明确的 companion bindings；格式见 [结果交接说明](../docs/reporting.md)。

```bash
runtime/.venv/bin/python scripts/report.py \
  --job "$DBT_BENCH_RUN_DIR/jobs/ade-smoke" \
  --experiment "$DBT_BENCH_RUN_DIR/manifest.json" \
  --bindings "$DBT_BENCH_RUN_DIR/bindings.jsonl" \
  --output reports/ade-smoke
```

输出 `trials.jsonl`、`summary.json`、`training-candidates.jsonl`。report 只读原始 reward、异常、日志、trace 和重试结果；audit 未运行为 null，原因不明保留待审。未知/重复/缺失 task-attempt 与 stale validation 会报错，不清理掉这些记录后重算通过率。

训练候选策略 `training-candidates-v5` 要求 reviewed/frozen train split、匹配实验身份、有效环境、有效原生成绩、可信验收、ATIF 结构与内容完整，以及经过 hash 对账的提交快照。原生 weak 还要求已验证 audit 对同一提交快照通过。严格 SFT 可重建状态另外要求完整的实际请求上下文证据；不会用历史消息拼接或 `tools=[]` 填补上下文。

当前 gh-dev smoke 为 DEB session-attribution 和 ADE ana-eng003 收集完整 `/app`，分别绑定已审查的 [DEB 声明](../configs/submissions/deb__dbt-session-attribution.json)与 [ADE 声明](../configs/submissions/ade__ade-bench-ana-eng003.json)。DEB 早期仅收集原项目和数据库目录，漏掉模型另建项目的记录原样保留。两题最新模型提交均已在干净环境重验；ADE 同一快照还由已验证 audit 评分。其他任务的收集合同尚待审查。[ADE 收集及重验](../validation/evidence/customer-environment-review-v1/summary.json)。

## 范围

不包含训练、tokenizer/chat template、LoRA、DAgger、自蒸馏、模型部署、自建 agent loop 或 dbt 专用 prompt/tool。TACC 复用 Harbor 原生后端，增加 SIF 来源绑定与身份校验，端到端验证仍 pending。没有继承旧 strict 作为通用正确性标准，也没有自动套用旧评分修复。版本、补丁与 split 理由见 [decisions.md](../docs/decisions.md)。

本仓库的 `.yaml` 文件采用 JSON-compatible YAML，以便辅助工具零第三方依赖；手工编辑时保持 JSON 语法。原生 `task.toml` 用标准库 TOML 解析，Harbor 原始数据模型在安装 runtime 后直接复用。
