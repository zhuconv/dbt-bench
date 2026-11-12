# 实施决策

本文保留按实施阶段形成的决策与历史观察；当前状态见[最终报告](run-results.md)。最终冻结audit-phase2-v14完成129/129新增审查；实现验收与完整语义认证仍为false，具体范围以最终机器摘要为准。

## 输入与实验身份

实际旧仓库是 `../training`，HEAD 为 `0f80b11a1a2dfc1b53c5106bf34a83f5a4a23098`，导入时 worktree 干净。与计划 ZIP comment 相同；没有收到 ZIP，因此不声称重新校验了 ZIP SHA-256。两份 v3 清单并集为 135；保留旧排除的 product-return-rate-analysis，未继承旧 `ok` 或排除结论。

选 Harbor 0.25.0 / Qwen Code 0.26.5，减少与已知问题的版本差异，但**不是历史实验逐字复现**。初期通过 [Harbor v0.25.0 job schema](https://github.com/harbor-framework/harbor/blob/v0.25.0/src/harbor/models/job/config.py)、[trial schema](https://github.com/harbor-framework/harbor/blob/v0.25.0/src/harbor/models/trial/config.py) 和 [PyPI 元数据](https://pypi.org/pypi/harbor/0.25.0/json) 核实配置及 wheel hash。随后已安装这个固定 wheel，从实际源码核实 Qwen version pin、计算 adapter hash，并复现 converter 丢失 error-only 文本；main 源码没有被当成固定版本认证。

根环境只用标准库，因此根 `uv.lock` 可实际离线生成、同步。Harbor 在独立 runtime 项目中固定直接版本；`runtime/uv.lock` 已生成并安装，wheel hash 与预先固定值一致。Qwen Code 0.26.5 npm tarball 已核对 registry SHA-512 integrity，并记录 tarball SHA-256；实际安装传递依赖仍待审查。

任务来源采用计划给出的六个固定快照，见 `sources.lock.yaml`。Git revision、原始任务 hash、适配 hash、文件权限/相对路径/symlink 目标均进入身份。运行任务的 hash 不兼用 Harbor checksum；两种算法不能假装相同。

## 后端与复用边界

Docker 保留为参考后端；TACC 执行选择 Apptainer，通过 Harbor 0.25.0 原生 singularity adapter。实际查到 `tacc-apptainer/1.4.1`，其版本输出为 `apptainer version 1.4.1-1.el9`，本机 aarch64。最初 doctor 只看 PATH 漏掉未加载 module 的安装，现已修正并保留初次探测记录。

Apptainer 接入使用完整任务 SIF 及 build/visibility 证据；在独立任务副本只改 environment.docker_image，保留原生 Dockerfile、测试、预算和工作流。不能仅拉取 Dockerfile 的 FROM 基础镜像就宣称完成 COPY/RUN/setup。当前不复制旧 tacc_env.py 的 root/su 或 timeout 修复，待固定版本实际复现需要时再应用最小补丁。后端、SIF 和运行副本进入独立实验/验证身份。

普通和 fakeroot 的真实容器启动已在 compute node 通过。默认工具沙箱仍拒绝 socket；用户调整 permission mode 后，经授权的非沙箱调用已完成依赖安装、来源下载、镜像构建及真实 Harbor 运行。详见 [Apptainer 说明](apptainer.md)。

Harbor 承担运行、并发、attempt、重试、Oracle/Nop 与 Qwen adapter。验证入口只创建隔离副本和原生配置、调用 `harbor run -c`。没有复制旧 driver/proxy/四工具 harness。默认 retry=0；所有失败保留。后续需要基础设施 retry 时应显式修改实验配置并记录异常白名单，不能凭缺 reward 猜测重试资格。

## 补丁与评分

原生 task 内容补丁列表仍为空。真实 Harbor 环境检查复现后，增加了 `harbor-025-apptainer-exec-v1`：保留镜像 PATH，并在容器已经是 UID 0 时省略重复的 `su root`。补丁核对 Harbor 版本、修改前后完整文件 SHA-256 和唯一文本上下文，原文件保存在 `.cache/runtime-originals/`。另在 DEB 的 Apptainer definition 中执行原生 DuckDB entrypoint，避免登录 shell 等待初始化 marker。未修改评分、预算或参考解法；这些是独立记录的环境版本，不宣称与未修复的旧运行逐字等价。

旧修复没有整包移植。当前 DEB 批量原生参考验证已再次观察到 marketing attribution 的 staging schema 不一致，以及 return-rate 的业务断言失败；详细根因与任务裁决还需逐项核实。

补丁 API 使用完整 upstream task hash、逐文件 before/after SHA-256、替换文件、类型、理由和真实回归证据；原始快照永远保留。评分/任务变体要新版本；默认 native prepare 拒绝把这两类补丁直接加入原题。它们应在单独 catalog 版本或 audit 副本运行，报告不能冒充原生成绩。环境/参考修复仍需逐题复核。

## 证据与结论

认证由逐题 contract 的要求覆盖、证据绑定和执行结果决定，目录存在、schema 合法和 oracle passes 均不自动升级结论。reviewed_inputs 绑定 task、来源、案例、环境/数据、checker、配置及实现；日志缺失/变更会使证据不可复用。报告格式变化不属于执行身份，修改 report.py 不要求重跑模型。

独立反例先证明业务错误，再看 verifier。通用比较器对精确字段不转 float；数值容差逐列声明；无键时做保留重复次数的二分图匹配。大规模无键容差比较超过显式上限就报错，请为任务提供键或专用比较逻辑，不悄悄退化成排序 zip。

trace 审计复用 Harbor converter；独立补丁 `harbor-025-qwen-error-v1` 仅补回 error-only 工具结果，保留空 output 和 call ID 行为。未知事件、压缩/重试元信息、非字符串复杂结果、未映射多 session 均保守标为缺口。固定版原生 converter 的 error-only 丢失已复现并修复。真实 converter 的 10 种 fixture 均达到声明的预期，其中 output 与 error 同时存在、重试、压缩和异常终止仍预期标记不完整。该结果不代表全部转换或模型上下文无损。

## Split

保留 v3 train=105/test=30 的分配，版本为 `v1-legacy-provisional`，不会导出训练候选。10 个 v2 train → v3 test 的暴露历史完整保留。ADE f1003/f1003-hard、f1010/f1010-medium 已放同组，依据是计划明确指出的 variants，后续仍要审查 setup/target。

跨 ADE/Spider 的 Airbnb/F1/Intercom 项目关系和 DEB task family/retail warehouse 关系待资产到位审查。未把所有 DEB 视为重复组，也未把同仓库 holdout 宣称为 unseen-warehouse。没有用文件名或历史通过率编造 workflow/difficulty，难度为 unmeasured。

## 当前限制

工作区 `.git` 是只读空挂载，不是已初始化的 Git repository。本次交付文件树，没有执行 git init/commit，也没有改动旧仓库。源代码文件可直接复制到正常 checkout 后提交。

## ARM64 环境准备进展

全部 135 个原生快照已校验取得。DEB 62 个 FROM-only/WORKDIR-only 任务复用经审阅的完整基础镜像；session-attribution 保留额外 pandas/numpy 安装；consolidate 单独复制并校验三份输入 CSV。后者 tests/private_data 与公开 CSV 完全相同，报告不会将其称为独立隐藏数据。ADE 保留原生 dbt/DuckDB 精确版本，Node/yq 使用固定 ARM64 官方资产。Spider 使用符合原生约束且有 ARM64 wheel 的 dbt-core 1.10.22 / dbt-duckdb 1.10.0；安装已锁定的 Harbor server venv，避免启动时下载。

每个镜像的 Dockerfile、definition、实际依赖、初始化、可见内容与差异均在 `artifacts/images/*-provenance.json` 记录。原生 Docker 与 Apptainer 的逐字等价检查尚未执行；镜像可运行不等于业务验收已通过。

## ROI audit 与 Spider 构建见证（2026-11-04 晚间）

ROI 的完整原始五表已用独立 Python/Decimal 重算，并实际执行正确、错误和等价案例。native 整数归一化会截去小数，且没有检查要求的 YAML 是否存在；二者分别有执行成功的业务错误证明，原生 reward 均为 1。因此新增独立 audit，原生分数始终保留。Audit 使用 Harbor 原生 collect hook 在 agent 结束后、shared verifier 前做只读导出，随后在 host 上评估带 hash 的同一提交快照；没有新增 agent loop 或请求代理。

正确提交的 dbt manifest 会去掉 SQL 文件首尾空白，首次 audit 误拒绝已保留为开发诊断，并增加合法表示回归。当前 audit 接受两个正确构建及合法重排/额外列，拒绝两个 nop 和全部八个已执行的原数据负例；另在明确标注的三行小仓库中执行真实 dbt，检查 tie、除零与 Unknown/NULL。小仓库关闭原始数据的 native grader，reward 保持 null。Audit 总体仍 pending，不能把这些已通过的检查升级为全协议认证。

Spider workday002 的 witness 在启动前移除了所有 native solution gold 和 copy-gold 脚本，只保留独立新增的 dbt 模型与验证用构建脚本。两次独立初始化都跑完 50 个模型并得到 native=1；独立原始输入重算与 11 列结果一致。Profile 与 family links 在固定数据上没有匹配，不能据此认证有匹配关系时的聚合语义。先前的列独立比较漏洞仍使 native 为 weak。

扩大 Spider 环境前逐个核对了 55 份相同 native Dockerfile：39 题有 packages.yml，需要单独固定 dbt deps 的解析结果；16 题没有该文件，可完整复用固定软件层并换入各自项目，其中包括已运行的 workday002。不能把“没有 package-lock.yml”误解成“不需要 dbt packages”。没有 packages.yml 的新构建器也拒绝接管其他任务，避免静默跳过原生依赖步骤。

Apptainer localimage 会把新的 %environment 追加到继承的 90-environment.sh。activity001 第一版因此将 packages 复制两次，逐文件检查发现 445 而非 239 个文件；失败 SIF 未注册。第二版仅替换已固定父镜像中明确的 workspace-copy loop，保留 Docker 环境设置，完整替换旧 workspace 和 initial copies，逐文件核对 hash/mode/symlink 后再做真实 Harbor 默认/login/root 上下文检查。该试点通过后才扩展其余同类任务；这仍只证明构建和环境，不证明业务答案正确。

## 全量镜像与显式 setup 修复

135 个候选均已有完整镜像及实际 Harbor 环境检查。Spider 的 39 个 packages.yml 在隔离项目中执行原生 dbt deps，保留生成的 package-lock、逐文件清单、下载包 archive/hash 和原生日志；只有声明的包、lock、log 路径及严格识别的新增 dbt telemetry UUID 可以变化。后续构建从冻结后的项目复制，不重新解析依赖。原有 16 个无 packages.yml 的任务保留其原生 vendored 包。两条路径均完整删除父镜像项目，避免跨题残留。

ADE 新软件层保留 dbt-core 1.10.11 / dbt-duckdb 1.9.3 / DuckDB 1.3.0，并固定隔离的 Harbor server venv。F1、analytics_engineering、Intercom 数据按原生 Drive file ID 获取，记录观测 SHA256；没有独立发布的 checksum，不能把观测 hash 宣称为第三方真实性证明。

f1009、f1010、f1010-medium、f1011 的原生 setup 实测因未执行 dbt deps 而返回 2。原始 Dockerfile 后接清理命令会掩盖此错误。保留四份失败记录后，新增 `ade-native-setup-dbt-deps-v1` 环境补丁，只在镜像构建副本的 dbt run 前加入 dbt deps。补丁逐题核对完整 task hash、setup 文件前后 hash 和唯一上下文；原始任务、verifier、参考解法及预算未覆盖。新 SIF/provenance 标识修复后的实验环境，不能冒充原初始化逐字复现。回归结果单列在补丁目录中，不反向修改补丁元数据或原失败日志。

可见性检查对 airbnb007 的 schema.yml、ana-eng005 的待修复 fact_inventory.sql，以及 f1011 的待诊断 analysis__lap_times.sql 发现 solution/setup 的相同副本。逐个阅读 native setup、比对确切源文件/hash 后确认它们是原生明确公开的题目输入，单列可见性审查；没有按目录名普遍放行 solution 文件。其余隐藏测试与答案资产仍不进入初始 agent 环境。

ADE f1003 增加无参考资产的真实诊断构建见证：从七个 raw 表独立用 Python join/count 推导八个 top-N 截断边界，按当前数据生成答案 model。两次正确构建通过，三个不存在的模型名也因 native LIKE 子串匹配而通过；因此只认证这个漏检证据，原生结论为 weak。其他任务的复制答案、聚合断言或全表比较仍需各自审查。

## Harbor Apptainer 生命周期修复

实际发现 353 个已结束环境遗留可访问的 HTTP 服务。Harbor 0.25.0 原实现只终止启动器，随后按它的 PID 查找子进程；Apptainer runtime parent 已被重新托管，因而未被清理。确认同一 UID、孤儿 parent、本仓库 SIF 文件描述符和已删除 staging 后，通过系统 Python 的 pidfd 精确清理，逐进程记录保存在诊断 artifacts 中。

`harbor-025-apptainer-lifecycle-v1` 为每次启动创建独立 session/process group，统一处理正常停止、资源替换、启动失败重试和内存终止。只发送到该容器拥有的进程组；独立 peer 容器存活检查避免误杀其他运行。三来源环境共完成 27 项实际容器检查，包含启动器先被 SIGKILL、后台子进程和三次失败启动。36 次 native reference/nop 与五个原数据语义负例结束后，宿主机未留下 Harbor 服务。[回归证据](../validation/evidence/harbor-lifecycle-regression-v1.json)。这不声称提供网络隔离；外层 Harbor 自身被 SIGKILL 时仍依赖 Slurm 等外部生命周期管理。

运行补丁安装器支持同一文件的有序 exact-hash 补丁链，可从原始、已应用部分补丁、全部完成三种状态继续，并在任何未知内容或断裂链上停止。使用保存的真实 upstream 文件重放全部补丁后，所得文件与实际安装内容一致，第二次应用没有变化。

新的 source lock 为 `7a6921cf74adf166d7ef817e7d7ab1e4f249242ee938d886cb1c4fc6ee67e59b`；旧版本 `cd863f9d2404af000e6ef6e3ba963a9029f5326568b66705ea60947e35e48da5` 保存在 catalog/lock-history。旧 540 次全量 reference/nop 属于先前运行版本。五个 weak 已在新版本各自重现；三个 broken 的 task/data/verifier 字节保持一致，并结合新 reference 结果复核。其他协议 pass 不自动沿用，旧 contract 和全部原始证据保留。后续报告必须分别说明历史覆盖与当前运行身份下的覆盖。

干净导出检查另发现 uv 使用硬链接共享安装文件与包缓存，旧的就地写入补丁因此会污染缓存，使新安装直接出现已补丁内容。安装器改为同目录临时文件写入、fsync、保留权限后原子替换；仅清理本仓库 uv 缓存中的 Harbor 包，再从锁定 wheel 重新取得原始内容。硬链接单元回归证明缓存及另一环境保持不变；实际干净安装逐文件核对三个缓存源仍为原始 SHA，安装文件为期望补丁 SHA 且 inode 不同。该安装器修复没有改变固定补丁后的 Harbor 源码或评分逻辑。

安装器允许在原运行 artifact 不存在时读取已提交的同 hash 小型回归副本，并在输出中记录解析后的路径；若原文件存在但被改动，不能用副本掩盖。干净导出缺少 task assets 时，报告正确拒绝 135 条缺证据记录、返回 1 且导出零候选。最初验证脚本将这视为普通命令失败，原输出保留；复核明确它是预期的 fail-closed 行为，没有修改生产报告代码以获得零退出码。

DEB `dbt-consolidate` 的合同将数据正确性与明确的工作流要求分别记录。原始三份 CSV 的完整独立重算与两次实际 dbt 构建一致，但 5 个数据负例只检出 4 个，7 个工作流负例均未检出。额外的 Decimal 等价案例揭示原生比较器把数字字符串化后的误拒绝。这些观察不改变原生评分；该题裁决为 weak，独立重算只作为证据，不冒充已认证的 audit。缺少实际 Qwen 上下文检查和其他规则负例仍为 pending。[逐项证据](../validation/evidence/deb-consolidate-review/case-summary.json)。

`dbt-test-orders-filter` 的原解法只保留五列，遗漏源订单表的 105 个列，原生仍给 1。新增的完整列见证与独立计算逐键匹配 1,950 行 × 110 列；金额错配、错误日期/状态及三位小数仍被原生接受。第一版案例的元数据查询漏匹配大写 `ORDERS`，造成数值按字符串比较；修正为大小写无关查询并强制类型完整后，14 个案例全部重跑。旧错误、null reward 和已运行案例保留，不计作模型 retry。[修正证据](../validation/evidence/orders-filter-review/case-tooling-correction.json)。其他 reporting flags 的业务含义仍未补造。

ADE `ana-eng003` 的固定公共输入是 30 行、29 个客户 ID，重复的 ID 1 两行完全相同。真实 dbt-utils 1.3.2 equality 使用集合差，接受两个各含一条重复输出的模型；它们违反明确的主键唯一性要求。两次真实构建与独立原始输入推导及固定 gold 一致，其他四个错误案例被拒绝，但该题仍为 weak，不能充当已充分验证的正常控制任务。[案例证据](../validation/evidence/customer-dimension-review/case-summary.json)。

该题补充 audit 保留原生 `test-setup` 构建责任：只提交正确 SQL、未提前生成输出表的案例必须接受。验证副本在 native verifier 前只读采集提交源码，在原生 `dbt run` 后、隐藏 seeds/tests 前采集实际输出；两个阶段的原始输入和提交源码清单 hash 必须一致，且记录新的成功构建 invocation。audit 只读该固定顺序产生的产物，独立按键比较，不修改原生 reward。17 个实际案例及四个 oracle/nop 控制验证了此顺序；audit 检出八个错误案例并接受九个正确/等价案例，原生漏掉两个重复案例、因比较器列集合检查误拒绝额外列。实际 Qwen 上下文与完整依赖传递尚未审查，audit 仍为 pending。[audit 证据](../validation/evidence/customer-audit-review-v1/summary.json)。

## Qwen 安装依赖与 GPU 启动修复（2026-11-05）

原生 Qwen adapter 在 DEB 和 Spider 的实际安装解析为 Node 22.23.4 / npm 10.9.9 / Qwen Code 0.26.5。冻结 npm tarball integrity 和 Node archive/binary hash 后，两个独立离线安装的 17 个包及 1,608 个规范化包文件与原生安装一致。npm 10.9.9 生成 lock 时丢失 libc 条件，第一版因此多装两个 musl 包；仅依据原包 metadata、在精确前后 hash 下补回四个 libc 字段，原失败及回归分别保留。该阶段的 lock 尚未接入正式 Harbor 安装路径；后续激活及 ADE 解压兼容修复见下方“固定安装与提交证据”。[锁与范围](../runtime/qwen-code/README.md)、[审查证据](../validation/evidence/qwen-installation-review-v1.json)。

GPU 启动配置显式选择 GNU host compiler、inference venv 的 ninja 和本机 CUDA 12.5 toolkit；PyTorch 仍为锁定的 cu128 wheel。实际 CUDA kernel 编译/链接/运行提供基础兼容证据，实际模型生成另行验证。FlashInfer 0.6.6 的 JitSpec 在 build.ninja 已存在时不会因 CUDA_HOME 改变而重写，所以缓存按配置与 runtime lock hash 隔离，旧缓存和失败日志不删除。模型前预检查直接编译/加载 GDN 扩展；这些均为推理服务兼容修复，不修改 task/native verifier 或原生分数。[上游固定版本的缓存逻辑](https://github.com/flashinfer-ai/flashinfer/blob/v0.6.6/flashinfer/jit/core.py)、[本次错误证据](../validation/evidence/gh-dev-1123437-attempt-2-stale-jit-failure.json)。

真实 smoke 中发现 JobConfig schema 接受任意 agent 名称，但实际 AgentFactory 才解析注册名。三份模型配置从 qwen-code 修正为 Harbor 0.25.0 的 qwen-coder，check_runtime 现在加载真实 class 并确认它就是原生 QwenCode。GPU 启动前执行该 conformance，单元回归实际调用 pinned factory 验证正确模板和旧错误名称。没有改动 task、verifier、核心 validation identity 或原生 adapter 源码。

1123437-attempt-4 已完成真实模型任务，原生 reward=1；任务本身的 native weak 裁决保持，audit 未执行。此次默认日志导出未保存提交源码/数据库，因此下一批生成候选前必须配置明确的快照收集。原始请求的 40 份日志包括 5 份自动 memory 子代理请求，35 个主 assistant 事件不能简单与全部请求逐个配对。当前保真审计器保守标记未覆盖的系统事件及 unknown context，未据成功 reward 导出训练样本。[真实 smoke](../validation/evidence/gh-dev-1123437-real-smoke-v1/summary.json)。

## 固定安装与提交证据（2026-11-05）

`harbor-025-qwen-locked-install-v1` 已进入来源锁。仅替换显式选择 bundle 时的安装路径，保留原生 agent loop、工具和 converter；实际三来源 setup/replay 已通过。prepare 的 Apptainer 配置必须使用与来源锁一致的 bundle，Docker 原生在线安装则明确保持未认证状态。旧安装身份的 Qwen smoke 不能自动成为新身份的模型验证。[激活证据](../validation/evidence/qwen-activation-submission-review-v1.json)。

版本切换没有自动 resume。保存旧合同、记录和代码后，逐项核对 11 个已审查任务的任务/数据/案例/verifier/环境绑定，并对变更函数做 AST 范围检查。只重新绑定未受影响的语义证据，新的 Qwen 环境、可见性及跨上下文检查继续 pending；原生 weak/broken 的业务反例没有被安装修复抹去。[复核范围](../validation/evidence/qwen-runtime-unaffected-review-v1.json)。

提交保存复用 Harbor 的 pre-verifier artifact 收集。DEB session-attribution 的固定环境以 DBT_PROJECT_DIR_DUCKDB 指向 /app/dbt_models_duckdb，旧合同误用了说明中的 /app/dbt_transforms；首批失败完整保留，路径修正单列。四次实际收集和两次 fresh-environment 原生 verifier 重验已确认可传递性及该 checker 的只读行为。训练候选策略 v2 强制核对原生 artifact 声明、保存的文件清单和 hash，不以一次 reward=1 或一个自报 complete 字段作为提交完整性证明。该证据尚未覆盖新身份下的实际模型提交。

## DEB 服务依赖和实际提交路径（2026-11-05）

DEB session-attribution 的 v3 SIF 在 v2 原生任务环境上预装 hash-locked Harbor server venv 和 asciinema 2.4.0-1。9,825 个初始工作区文件、初始 dbt 包和任务 Python 包版本比较一致；实际 Harbor 上两次 reference=1、两次 nop=0，独立证明的重复渠道业务错误仍被 native 接受。原生说明和 verifier 均未改动，旧镜像及运行记录保留。[环境审查](../validation/evidence/deb-session-server-review-v1.json)。

真实 Qwen attempt-1 按说明在 `/app/dbt_transforms` 新建项目，并修改 `/app/dbt_models_snowflake`。只导出初始化时的 `/app/dbt_models_duckdb` 与数据库目录虽然通过了文件收集检查，却漏掉实际模型源码；数据库仍可独立重验。此 trial 的报告 companion 明确不认证完整 submission，不从工具文本伪造最终文件。[实际缺口](../validation/evidence/gh-dev-1123630-submission-scope-gap.json)。

此题后续采用 `session-workspace-v2`，在 verifier 前收集完整 `/app`。它包含 task 声明的原生与提示路径、源码、数据库及 WAL；`/tests`、`/solution` 在该范围之外。新声明产生新的实验身份。后续检查须同时审查实际工作流与快照范围，单独的 artifact `complete` 只说明所声明目录收集成功。

DEB 其余服务层固定后，61 个仅依赖原生基础镜像的任务复用 base-v4，consolidate 则从自身 v2 镜像构建 v3，保留原生 `COPY data /app/data`。全部 64 个 DEB 绑定现有固定的独立 Harbor server venv。切换前后核对初始文件、任务 Python 包与 dbt packages，之后执行受影响的 248 次 reference/nop 和 30 次语义案例。旧镜像、合同及原始分数保留，未完成检查不自动变成 pass。[本轮审查](../artifacts/validation/deb-server-freeze-v1/review-summary.json)。

consolidate 的三个测试 private CSV 与原生公开输入字节完全相同。初次严格隐藏文件扫描的失败记录保留；仅在同时核实原生 Dockerfile、公开源路径、目的路径和双方 hash 后，把这三个既定公开文件记录为例外。例外不接受任意同名文件或变化后的隐藏答案，也不证明任务使用了独立隐藏数据集。[显式检查入口](../scripts/check_deb_server_layer.py)、[回归](../tests/test_deb_server_layer.py)。

## ADE 客户维度 audit 与逐题成功定义（2026-11-05）

客户维度任务的 native 仍为 weak：它接受重复键且拒绝合法额外列。独立 audit 在固定输入和声明的合同上完成 14 项协议检查，结论为 validated；没有用 audit 成功改写 native 结论或原始 reward。真实 Qwen 作业 1123746 的完整 `/app` 提交在干净环境按原生 `test-setup.sh` 重建，通过 native 与 audit。环境证据包含原生 Qwen ShellExecutionService 的 pipe/PTY 两种路径、实际模型工具调用、Python/dbt/DuckDB 路径、隐藏材料检查及重验前后的文件清单。[审查及范围](../validation/evidence/customer-environment-review-v1/summary.json)。

该任务允许仅提交模型源码，由 native verifier 构建。audit 因此读取原生构建后、隐藏 seeds/tests 安装前的产物，并核实源码和固定输入未变。首次重验递归删除 overlay 下层目录失败，原错误保留；后续只删除快照外文件/链接并保留空目录，随后按内容、mode、symlink 完整核对工作区。这是提交传递兼容处理，不更改 task 评分。工作区以外的依赖修改不能由这项证据自动认证。

报告过滤规则升级为 `training-candidates-v3`。逐题合同中经审查的 native success 定义仅在合同 hash、当前 validation identity 和 review 均匹配时使用；未审查的来源规则不会因此全部变成可信。新回归覆盖过期合同和逐题作用域。该模型 trial 的 environment、identity、submission、same-submission audit 均核验成功，仍因 split 未冻结、trace 内容不完整被排除；实际上下文捕获仍为 unknown。[报告](../reports/customer-qwen-1123746-v1/summary.json)、[169 项测试](../validation/evidence/customer-task-report-tests-v1.json)。


## 实际请求上下文与原生 ATIF 分开认证（2026-11-05）

Qwen 原生 logger 使用生成器提交给 SDK 的实际 request body；通过固定原生代码和本机 HTTP fixture 核对，流式/非流式/错误输入一致。SDK 内部 retry 则可产生两个 HTTP attempt、一份 interaction，因此上下文认证必须结合完整服务生命周期的请求数及 session telemetry。本次 ADE 23 个任务请求与 1 个协议探测均可对账，没有用文件数量单独证明完整性。

19 个主请求与四个 memory 子代理请求分别绑定，子代理上下文使用自己的原始记录，不由主历史推导。原生 memory catalog 参与实际输入却未进入 ATIF；本次只认证客户端实际请求级上下文，保持 ATIF 内容 incomplete。保留一次工具截断、三次工具错误以及全部旧证据，不将截断前内容补造出来。此改动只增加只读审查与 companion，没有改变 task、运行参数、原生分数或 verifier 身份。[范围及失败回归](trace-fidelity.md)。


## 派生 ATIF 与验证身份范围（2026-11-05）

原生 ATIF 不改写。离线转换先用固定 Harbor converter 重新生成并精确匹配原生轨迹，再将实际 memory catalog 映射为 user context，完整原始事件及索引映射附在派生产物中。报告重新计算并核对转换，版本或内容不匹配即拒绝。ADE 1123746 的派生内容和实际请求上下文均通过，但 split 未冻结，训练候选仍为零。18 项 converter fixture、4 项读取检查和 183 项实现测试通过。[转换审查](../validation/evidence/trace-memory-conversion-review-v1.json)。

纯报告与 trace 转换代码不参与 verifier 执行身份；它们分别以报告策略版本和 converter 实现 hash 固定。此次范围调整在归档原合同、记录及代码后进行 AST/import 审查，只重新绑定 11 个原本有效且其余身份完全相同的已审查任务；未审查、过期或缺证据项没有被提升。native 仍为 8 weak、3 broken、124 pending，audit 仍为 1 validated、134 pending。[身份复核](../validation/evidence/trace-reporting-scope-review-v1.json)。

Spider workday002 收集完整 `/workspace`，保留项目、profile 和结果数据库，隐藏 `/tests` 与 `/solution` 不在此范围。四次 reference/nop 实际收集均为 364 个文件，reference 两次为 1、nop 两次为 0，所属容器进程组和端口均已关闭。这里的 copy-gold oracle 只验证收集和评分路径；真实 dbt 构建见证单列，实际 Qwen 工作流在作业 1124050 结束后另行审查。[收集证据](../artifacts/validation/spider-capture-reference-v1/capture-review.json)。


## FX 案例与参考解释的边界（2026-11-05）

结算日期 FX 题的原生结论调整为 weak，依据是四个真实执行的独立反例：重复分组、删除既有列、错误 NULL 金额和修改物化类型。另两个负例被正确检出，正向参考与排序案例单列。原生 verifier、任务文本及原始 reward 均未改动。[证据](../validation/evidence/fx-settlement-review-v1/summary.json)。

保留原始粒度的要求与四键参考/grader 的关系尚未澄清；参考的 DOUBLE 也未满足对 DECIMAL 类型的明确认证。因此记录“从输入构建发生过”，但 witness_kind 仍为 unknown，reference 与独立语义协议项仍 pending。两次稳定输出、成功 reward 或独立 helper 均不替代完整可解性或 audit 认证。初始只读探测误用登录 shell 导致初始化等待，已停止该探测并保留日志；之后直接只读导出初始公开内容，正式九个案例均通过 Harbor 的干净可写环境执行。

## 跨来源项目家族审查（2026-11-05）

对 ADE 16 题和相关 Spider 6 题重新核对 SIF hash，并只读导出初始化后的公开项目文件。除 Airbnb、F1、Intercom 外，Northwind / analytics-engineering 也存在跨来源项目关系。原始项目相同不能替代 setup 后比较：ADE Intercom 的模型已全部移除，部分 Northwind 任务注入了重复数据；Spider 则保留不同的初始模型。两组 F1 prompt variants 的初始化模型文件分别一致，必须同组。

本轮形成四个保守同 split 项目分组建议，22 题目前均为 train。没有据此宣称答案泄漏或数据库等价，也没有改变 catalog、split 或验证结论。其余 113 题的家族和历史暴露审查仍待完成；冻结版本需在完整审查后生成。[范围与后续工作](family-review.md)、[机器可读证据](../validation/evidence/cross-source-family-review-v1.json)。

## 完整家族审查与 split 冻结（2026-11-05）

135 题的家族审查现已完成，采用 `v2-family-reviewed-2026-11-05`：79 组、93 train、42 test。ADE/Spider 的 71 题使用当前镜像的初始化项目检查；DEB 的 64 题按任务目标与组件复用归成 40 组，共享 retail warehouse 单独记录。题意层面的家族分类不替代逐题业务 contract 或 verifier 认证。[完整范围](family-review.md)。

分配规则不使用模型成绩或 verifier 结论：组内任一成员属于旧 v3 test，整组保留 test；其余组为 train。12 个旧 train 因此移到 test；其历史 train 暴露以及原有十个 v2 train → v3 test 记录均保留，不宣传为旧 checkpoint 的干净 holdout。未知 warehouse 字段没有按任务名称自动补造。[冻结决策](../catalog/reviews/family-v1/freeze.json)。

原始 catalog/split/import 已归档，三份家族审查的整合副本仅将来源指向字节相同的归档。读取 frozen split 时验证审查 hash、逐题元数据、历史暴露、分组与分配的一致性，不信任单个状态标签。更新前后只允许家族/workflow/split 元数据变化；12 份当前有效语义审查逐项核对其余执行输入完全不变后显式重绑定，123 份其他合同不被认证。[身份复核](../validation/evidence/split-freeze-identity-review-v1.json)。

报告策略升级为 `training-candidates-v5`，分别记录生成时 catalog/split hash 和当前选样的 catalog/split/version。旧 experiment ID、原始 native reward、原生 ATIF、模型请求和提交快照均保留。新选样不伪装成在新 split 上重新运行的实验。193 项测试通过，其中新增检查覆盖伪造 frozen 标签、篡改分组/历史、重新计算 split hash 绕过审查，以及旧生成身份在重新选样时被保留。


## DQ 审查范围与数值语义（2026-11-05）

DQ macro enforcement 的原生评分未改动；六类独立成立的错误被原生接受，故原生结论 weak。15 个字段的独立重算及完整输出稳定性只覆盖已声明部分，不代表所有 DQ flag 已通过语义审查。原生 26 个测试通过不能代替每项业务要求的负例。

独立实现最初使用纯 Decimal 的除法/舍入，与锁定 DuckDB 1.2.2 对任务明确 SQL 表达式执行 DOUBLE 除法的结果不同。先保留两次评分前 null 结果，再检查固定版本 C++ 源码与实际数值；新案例仅对该表达式显式实现二进制数值语义，其余精确字段和键不浮点化。此 helper 尚未认证为补充 audit。[范围与证据](../validation/deb/dbt-dq-macro-enforcement/README.md)。冻结 audit-pilot-v1 保留当时完整账本快照，本次 DQ 更新不改变其中唯一合格任务或放宽选样规则。


## Session 额外输入与原始 benchmark 分开计分（2026-11-05）

原始 session 数据没有多触点归因路径，因此增加独立的小输入关系来检查衰减、位置和加成等规则。额外 fixture 在干净原生任务副本中替换两张明确声明的输入关系，再以实际 dbt 重建目标模型；原 staging 源码与正式任务保留。原生 grader 本身不改动，其 fixture 分数不并入原始 benchmark 分母。19 次新增 fixture 与两次原始参考在 manifest、proof 和累计索引中分别标记。

原生 0.05 数值容差漏掉了错误的 conversion bonus；全列 NULL 检查又拒绝了合法的可空额外列。任务保持 weak，独立计算 helper 只证明声明输入上的案例有效性，并未升级为可信 audit。实际上下文和未明确的边界语义仍需完成。[完整范围](../validation/deb/dbt-session-attribution/README.md)。

## Session 源码重建与扩大构建范围分开记录（2026-11-05）

实际模型只构建 `attribution_report`。从干净原生输入恢复六个模型/配置源码及 dbt 的 `.user.yml` 后，同样选择该模型，成功复现 native=1，输出与独立重算一致；未传入 agent 数据库或旧 target。额外要求构建其新增 staging ancestors 时出现 catalog 路径错误，保留该诊断 native=0，不覆盖实际 trial，也不据此把原任务判为 broken。

生产 Qwen shell service 的 pipe/PTY、原生 verifier 和完整工作区诊断重验共同支持三项执行环境检查。新增环境证据只改变该题 `environment_data` 与 contract；其他执行输入和 134 条记录逐项保持不变。完整隐藏资产清单、项目路径及工作流合规仍待审查，native weak / audit pending 不变。未来提交声明更新为当前 contract；已完成 trial 的声明和 experiment 身份不修改。[审查](../validation/evidence/session-environment-review-v1/summary.json)。

## Session 工作流违例独立于数值正确性（2026-11-05）

工作流案例只改变隔离任务副本，原始输入与 native grader 均保留。缺少必交模型、view 代替 table、新建被禁止的 staging、最终查询降序，以及重复构建追加行，各自通过文件、实际物化类型或成功构建的输出建立违例；原生接受这五类错误。错误 schema 的对照先成功构建正确数值，再证明规定 schema 缺少目标，原生将其拒绝。两次独立初始化的正例各连续重建两次，保持完整输出和源码不变。

排序义务针对最终模型 SELECT，不能用表扫描顺序作为 SQL 关系的内在保证。非幂等案例同时违反 channel 粒度，但不把两条要求重复计为两个 trial。这八次是固定原始数据上的实际验证运行，不是模型尝试或额外输入 fixture。新案例仅增加明确的验证版本；旧案例字节与执行环境逐项不变，原生仍 weak、audit 仍 pending。[证据与复跑入口](../validation/deb/dbt-session-attribution/README.md)。

## Session 初始隐藏材料的声明范围（2026-11-05）

可见性裁决使用固定 Qwen shell service 对干净镜像的实际观察，而非只检查文件名。普通文件检查 exact hash 和连续完整源码行指纹；初始 dbt manifest、运行结果、msgpack 解析缓存、pickle 依赖图和日志另行解码，避免 JSON 转义或文本大小限制遗漏缓存源码。pickle 只读 opcode，不执行反序列化对象。声明隐藏文件、嵌入完整模型和目标名称均未匹配，初始目标表/模型不存在，数据库不变。

检查通过仅覆盖列明的资产、文件系统和结构化产物；虚拟文件系统、诊断输出、压缩包内容与一般二进制语义等排除项保留。没有把此结果称为网络隔离或通用信息流安全证明。原 `environment.json` 及其历史引用不改动，新 `visibility.json` 通过 contract 的 hash 绑定，使已有运行证据仍可追溯。native weak / audit pending 与单题冻结试点范围均未改变。[审查](../validation/evidence/session-visibility-review-v1/summary.json)。


### Workday 独立语义来源与验证存储（2026-11-05）

Workday 语义案例固定补充来源 Fivetran dbt_workday v0.2.0 / 3b5cd5fcd31413ba7df77e4cb67ad05af2a31aec；其 docs/schema 与 benchmark 相同。参考 SQL、许可证和下载 hash 保留在案例/证据中，原 benchmark sources.lock 和 native grader/gold 不修改。额外输入 fixture 保留五个原 profile 的核心值，只替换明确声明的关系数据并加入已删除 profile，结果单列。当前独立 helper 用于案例证明，尚未作为 validated audit checker。

文件数量配额限制下，Workday 批次副本仅对不可变 environment 构建输入使用 hardlink；实际执行固定 SIF，solution/tests/instruction 仍复制，结束后重新核对来源 hash。此策略只适用于该已构建镜像的案例入口，不推广为允许任意环境源码写入的共享工作区。失败准备目录已验证归档，原始 rollout 与成功 trial 不删除。

## Workday 工具上下文、重放与删除规则（2026-11-05）

Qwen shell probe 从实际安装后上下文读取工作目录，避免将 DEB 的 `/app` 写死为所有来源的目录。结构化缓存检查使用 contract 显式声明的目标关系；Workday 的初始备份路径由 SIF 构建定义确认是 `/opt/dbt-bench-initial`。错误路径的第一次检查保留为范围缺口，修正后重新执行，不能用扫描器单元测试代替实际容器证据。

提交重放仅支持显式声明的 task/工作区/数据库/目标表映射，不猜测数据库或自动选表。Workday 的 623 文件快照与五行结果在干净环境逐项核对并由未修改的 native verifier 读取。实际模型曾安装工作区外 CLI；证据只证明本次重放无需恢复该 CLI，未证明全部外部依赖均已捕获。此事实单独披露，不作为 infrastructure retry 或 trace 通过的依据。[执行审查](../validation/evidence/workday-environment-review-v1/summary.json)。

删除关系案例采用新版本 fixture，先消除相同关系键的重复原始行并检查冲突，保持 native schema 唯一性要求；旧 fixture 和结果不变。三个错误在调用 native verifier 前由独立关系遍历和手算成员证明，全部被 native 漏检。两个独立正例的聚合顺序允许变化、成员须稳定；这些证据仍不解决空集合类型表示，也不认证当前 helper 为补充 audit。[案例审查](../validation/evidence/workday-deleted-relations-v1/summary.json)。

## Workday 工作流证据不依赖最终分数（2026-11-05）

工作流负例先证明所有输出值正确，再从实际命令结果、固定启用模型集合、invocation、最终模型源码和关系类型证明违规。仅使用 `--select` 不是错误；覆盖全部所需模型的合法显式选择应接受。要求建表的依据是任务的 Create a table 及项目 table materialization，不能将此规则自动推广到其他允许 view 的任务。四个工作流案例均被原生漏检，单列为 explicit_workflow_requirement，不与数据语义错误混为同一统计。[独立证明](../validation/evidence/workday-workflow-review-v1/summary.json)。

干净镜像未保留 packages.yml，deps 义务是条件性的。首次案例误判该条件时，证明门阻止原生评分；修正只改变新的案例版本，先明确提供 packages.yml，再验证省略 deps/run 的违规。失败日志和 null reward 保留，不冒充已检出的负例。

文档将 family 字段描述为字符串数组，固定上游 SQL 使用拼接字符串；两种表示都已有实际正确案例，原生均接受。空数组和上游 SQL NULL 已有正例，空 VARCHAR 是否等价仍未最终裁决。这些观察只扩充 contract 证据，不把宽松的案例 helper 自动升级为可信 audit。

## Workday 实验性 audit 与采集证据（2026-11-05）

新 checker 使用独立的反向索引传播重算，未复用案例的嵌套遍历或读取 case validity/native reward 来定义正确性。输入注册表固定原数据和两个明确版本的额外 fixture；输出按完整键值关联比较，字符串聚合按完整成员匹配。空 VARCHAR 的无成员表示返回 unknown，核心 NULL 仍精确比较。整体分数在有明确错误时为 0，全部证据通过时为 1，否则为 null。

数据正确不能代替工作流证据。当前支持已审查的验证案例命令采集，生产模型采集尚未认证；已有 7 个正确案例因此保持 unknown。离线 runner 先验证原生评分前的文件清单、执行 helper、task 副本与文件 hash，再生成 checker 输入，不能根据修改后的文件重新生成引用后宣称是原提交。此前未执行这些附加绑定检查的原型单列保留。[审查](../validation/evidence/workday-audit-review-v1/summary.json)。

四项已验证组件不代表整个 checker validated。缺输出/nop 控制、生产采集、空表示和其余规则仍明确 pending，试点池不扩容。原模型只获得新的独立 companion 观察，原 native reward、原始 trace 和训练资格不被覆盖。


## Workday 采集边界与缺输出（2026-11-05）

v2 在隔离验证副本中包裹原生脚本，保留其全部文件字节、退出码和 reward 生成路径。采集器只读取明确声明的源码、输入表、目标表与 dbt artifacts；收集过程和原生评分前后都检查数据库 hash。收集错误不阻止原生 verifier，不用其错误伪造 0 分。目标关系不存在且成功完成采集时，才单独得到缺输出失败。[版本与执行范围](../validation/spider2/workday002/audit_v2/README.md)。

两次 copy-gold 即使数据正确也保持工作流 unknown。真实模型的最后一次 dbt test 会覆盖 run_results；不能据此推断此前没有完整 dbt run，也不能把数据通过等同于工作流已取证。当前只认证 nop 组件和已声明的采集边界，未解决全部合法工作流、依赖捕获和空聚合表示，任务池不扩容。


## Workday 工作流观察与语义裁决边界（2026-11-05）

允许从未改动原始工具结果和 dbt invocation 日志证明早先的完整构建；不要求最后一份 run_results 必须是 run，也不伪造已经被 test 覆盖的历史文件。当前观察器仅接受逐调用审阅的参数和效果，支持实际已发生的叶模型编辑后单独重建。非叶变更、重命名或未知命令需补依赖范围证据，不能用一个全局“曾成功运行”标志认证最终工作流。

空 VARCHAR 的聚合等价性在固定任务材料中未规定，明确归为语义不确定，而不是无限等待代码测试来决定规范。当前 checker 保持 unknown，任何将它一律接受或拒绝的规则均需独立版本和任务变体说明。工作流事实得到支持并不解决该数据语义缺口，也不补齐缺失的 memory 返回正文。[来源和实际证据](../validation/evidence/workday-model-workflow-review-v2/summary.json)。

## Session 保留产物 audit 的独立性与范围（2026-11-05）

新增 scorer 不调用原生 grader 或案例有效性 calculator。它以最近有效触点为时间基准，在归一化前抵消共同的 conversion-age 衰减因子，按 channel 聚合 Decimal 权重；手算样例另行检查位置、时间、加成和大整数 ID。比较器精确核对 channel、重复次数和必需列，只为数值存储误差声明 1e-9 容差。案例的独立正误结论仅在评分完成后用于核对预期，不参与评分计算。

输入只允许两个明确 hash 的数据版本；采集绑定核对已执行 task、case、proof 和输出文件。该入口复查历史案例，不作为生产采集或实际模型提交的证书。未知路径及边界语义保留，五个数据正确但缺完整工作流证据的案例不升级为 pass。三个 audit 组件通过不改变整题 pending 结论，也不扩充冻结池。[版本、命令与未完成项](../validation/deb/dbt-session-attribution/audit_v1/README.md)。


## Session 采集审计与临时提交传递（2026-11-06 UTC）

`audit_v2` 独立绑定原生评分前后只读快照，复用未修改的 v1 独立重算核心。完整输入身份、数据库/WAL、源文件和导出未变才评分；采集故障为 error/null，缺工作流为 unknown/null。两个固定项目路径均记录，不通过隐式路径修复抹除 instruction/native 的矛盾。当前七份采集不足以认证全部错误和合法表示，未将整题或实际模型提交认证为成功样本。

一次诊断重放在 Work 文件配额失败后，使用节点 `/tmp` 下的临时别名，使 Harbor 原生 sanitized copy 与上传仍按原逻辑执行；传入环境后逐个核对声明的文件。该局部传递适配不改 agent、调度、原始 artifact 或 reward，首次失败单列保留。[版本与证据](../validation/deb/dbt-session-attribution/audit_v2/README.md)。


## ADE 输出列政策与实际 Qwen 指引（2026-11-06 UTC）

评估新增列是否为合法等价时，同时读取任务正文、项目 schema/下游引用及实际 agent 上下文。不能仅因正文未列出禁止项就推断所有额外列合法，也不能把 grader 的精确列集合自动提升为业务要求。锁定 Qwen 0.26.5 默认读取 QWEN.md 和 AGENTS.md；保留客户试点的全部 19 次主请求已确认包含 AGENTS.md 的 no-extra-work 指引。库存新增列案例的历史 legal_equivalent 标签暂不作为 weak 裁决依据，canonical case 禁止以已审查案例执行；原始 reward 和证明内容仍保留。

该疑义也适用于客户试点既有的额外列政策，需单独完成语义复核，不能用库存任务的新标签默默改写冻结池或旧成绩。该发现尚未证明某份既有完整模型提交违反要求；已有逐键数据、构建、trace 证据仍分别保留。[锁定源码和实际请求证据](../validation/evidence/inventory-review-v1/qwen-guidance-followup.json)。


## 2026-11-06：原生控制池与库存输出接口

ana-eng005 的任务是修复现有模型粒度，结合实际交付的“不做额外工作”指引，本版本将最终九列接口保留作为 contract 的一部分。新增无关常量列不是该修复要求，内部辅助列和保持逻辑值的合法实现仍允许。早期等价假设不再用于原生弱判定，原始记录保留；新负例先独立证明再重跑。此为本题说明的明确解释，没有改 prompt、原生 grader 或 reward，也不直接推广到创建新模型的客户题。

native-control-v1 采用冻结 train 成员与当前原生协议 validated 的选择规则，不按模型成功筛题。它保留全部 135 题的账目、只纳入 ana-eng005；现有 audit-pilot-v1 原样保留。两者分别声明原生与补充验收要求。新控制题无模型轨迹，不产生候选。尚未完成的全量语义、构建见证、依赖与 trace/context 验收继续列在 delivery-status，未将单题冻结当作最终完成。


## 2026-11-06：客户输出政策和 audit v2

客户题的初始化删除 warehouse 模型，因此不能仅套用库存题的已有目标接口理由。该题单独依据公开 staging 的十八字段、请求的 rename/deduplicate 转换以及实际交付的 no-extra-work 指引，明确保留源属性且不发布无关新属性。旧额外列 legal-equivalent 假设只看主提示，现保留为历史；新负例在原生 grader 前独立证明。原生重复键漏检仍成立，native weak 不变。

customer-output-audit-v2 复用原有工作流／数据检查并新增显式输出 schema 判断；它是新的补充评分版本，不能用旧 v1 标签满足新池资格。全部旧快照和模型同提交快照重新审计，分数保存在新文件。validated-pilot-v2 合并当前合格的库存与客户两题，仍保留全 135 个候选和所有未完成项。旧两个冻结版本不修改。读取实际 trial 的报告使用 runtime Python 加载 Harbor 原生 schema；缺依赖时继续保留 unknown，而非猜造有效分数或完整 trace。


## 2026-11-06：F1 hard 独立审查与第三版冻结账目

`f1003` 与 `f1003-hard` 的固定项目和测试逐字相同，但 hard 的结论来自独立运行，不继承普通版本的 weak 标签。新的见证只从原始输入推导诊断答案；准备副本移除原始 solution 内容，原生 verifier 字节保持不变。前两次见证隔离断言失败保留为 category 2 准备错误、reward null。

当前数据的边界并列组证明三个其他模型存在成员歧义。十二类案例各重复两次，原生漏掉名字、NULL 行和输出列三个要求。答案顺序和 dbt view 是已执行的合法等价形式；包含原题已给出的示例尚未裁决。实际 Qwen/verifier 上下文及独立 audit 继续 pending。

`validated-pilot-v3` 冻结新的全 135 题账目，可采样成员和 checker 规则与 v2 相同，F1 hard 仍隔离。旧冻结目录不改写；新报告分别保留历史失败、原生分数、audit null、缺失检查和真实见证数量。实现验收与全量验证仍未完成。


## 2026-11-06：F1011 按单行诊断答案验证

本题不要求修复圈速分析 SQL，而要求一行、一列的诊断答案。十六个负例先成功构建，再独立证明错误，随后调用未改动的原生 verifier；其行数、列数和选项范围存在已执行的漏检。原生参考 fixture 接受与完整诊断语义认证分别记录。

对原始圈速/进站/赛道数据的独立 Python 聚合支持 E、排除 C/F；A/D 的一手领域依据和未执行负例明确列出。36 组圈数差异不能直接判作未完成圈，B 不依赖 grader 或参考字符串裁决。未将这个任务增加到真实见证或采样数量。

实际 shared verifier context 已记录；Qwen shell/PTY、visibility、完整提交和完整答案见证仍缺失。每个持久证明、独立事实导出、context 和日志直接进入 resume 的 hash 检查，修改证据副本或身份的失效行为已实际测试。

`validated-pilot-v4` 保留全 135 题新账目，可采样成员及 checker 版本不变，旧版本不改写。实现验收和全量验证均未宣告完成。


## 产品库存模型逐题审查与 v5 冻结

`ana-eng004` 的题面只固定模型名。保留原生仓库构建流程，独立从 raw 重算 fact_inventory/dim_products，再按 inventory_id 核对输出。26 次执行显示 set EXCEPT 漏掉重复次数；精确列集合也拒绝清晰的库存 product_id 别名和产品已有 attachments 字段。行列换序、view、由 grader 构建源码均被接受。只交物理表被预期测试数量检查拒绝。原生文件与 reward 均不改写，native 为 weak、audit pending。

现有 staging 把五个多供应商产品过滤掉，导致十六条库存无产品维度匹配。真实构建能重现原生仓库输出，不足以裁决“每件库存增加产品详情”的完整语义。因此保留独立计算与两个 scoped build 的证据，完整 witness 仍标 unknown；没有以 gold 定义真值或宣布该任务 broken。后续需要任务范围裁决及独立补充 checker。

`validated-pilot-v5` 冻结全 135 题账目，保持原有两个可采样任务和 checker 版本。13 个案例 payload、两个 Harbor 可加载副本、覆盖/无效参数拒绝、26 个容器退出以及 490 个关键证据引用的变更失效检查通过。未修改共享执行/评分代码，未重跑既有 285 项全套测试。旧冻结快照不改写，完整实现与全量验证仍未完成。[本轮证据](../validation/evidence/product-inventory-review-v1/summary.json)。


## 三模型唯一性审查与 v6 冻结

`ana-eng006` 明确要求新建 dim_products、fact_inventory、obt_product_inventory，并要求后两者按 inventory_id 唯一。原生 setup 注入 52 条重复库存记录，形成 154 行、102 个唯一 ID。独立 Python 去重/日期/供应商关联及 keyed enrichment 用于预先证明案例，不调用待测 grader 定义真值。

30 次独立执行确认：fact_inventory 单独重复、遗漏去重均被原生行数保护拒绝；只让宽表重复则两次满分，违反显式要求。不能因两题共用 equality 宏就忽略本题单独的 fact 行数检查。数量、对应关系、覆盖、三个模型源码、重排/view/source-only 等案例分别保留。原生文件/reward 未修改，native 判 weak，audit pending。

与 ana-eng004 一样，初始化产品 staging 过滤五个多供应商产品，造成十六条库存无产品详情。两次 reference-free 真实构建仅证明既有 staging 范围，不认证完整任务见证；维持 unknown 并等待产品域范围裁决。未因原生参考答案通过就消除此缺口。

`validated-pilot-v6` 冻结 135 题账目，可采样任务和指定 checker 版本仍为原来的两个。15 个 payload、2 个 Harbor 可加载副本、覆盖/参数拒绝、30 个容器退出及 800 个关键持久证据引用的变更失效检查通过。未修改共享执行/评分实现，既有 285 项全套测试未重复执行。旧快照原样保留，完整实现及全量验证仍未完成。[本题证据](../validation/evidence/inventory-three-models-review-v1/summary.json)。


## F1002 显式 YAML/参考/评分矛盾与 v7 冻结

题面把 `__stats.yml` 作为四模型合同；其中 most_podiums 明确需要 p1/p2/p3。固定参考 SQL 和 gold 都缺这三列，原生 equality 排除 driver_full_name 后仍要求列集合相同。两次原生参考缺字段却被接受；两次加上独立从原始结果表重算正确的字段则在列集合检查被拒绝。四次模型构建均成功，新增字段满足 p1+p2+p3=podiums，错误发生于评分，不能归为模型能力或 SQL 执行故障。

该冲突妨碍有效原生评估，native 判 broken，保留原生文件和分数。修复应产生独立评分/任务版本并重新验证，不能覆盖原成绩。当前只完成这个针对性矛盾及相关 raw 聚合核对；参与/完赛定义、排名/tie 合法形式、其他负例和 Qwen/可见性协议仍未完成，完整 witness 未认证。

`validated-pilot-v7` 保留全 135 题账目和原有两个可采样成员。4 个原始数据案例、2 个可加载准备副本、参数与覆盖拒绝、4 个容器退出和 86 个关键持久证据引用的变更失效检查通过。共享实现未修改，已有 285 项全套测试未重复运行；旧冻结快照不改写。[本题审查](../validation/evidence/f1002-schema-review-v1/summary.json)。


## 当前年龄任务审查与 v8 冻结

`f1009` 要求两列的当前年龄分布。独立 Python 从全部 860 个 raw DOB 计算完成的日历年，再与不同 SQL 表达式构建的模型按年龄键精确核对；原生实际日期为 2026-11-06、Etc/UTC，得到 108 个年龄组，其中 106 人本年生日未到。两个干净环境产生真实构建见证，缺完整见证的候选数降为 121。

28 次原始数据案例确认 UNION-based native grader 的缺口：空/缺失/重复输出，以及固定投影隐藏的第三列、硬编码表读取隐藏的模型源码缺失均可通过；数值偏移、生日遗漏和键值错配被拒绝。正确模型与重排、view、源码提交均通过。保留原生分数，native 为 weak、audit pending。

不覆盖 CURRENT_DATE，不因日期变化删除字段。每次模型构建和原生评分都记录实际日期/时区并核对输出。七个手算生日/闰日规则 fixtures 单独标明，未运行原生 benchmark grader，不能计作额外 benchmark trials；非闰年 Feb29 采用原生月日规则的 March1 约定，原始数据此次十一月的年龄不依赖这个边界。未来提交仍须自己的日期与验收证据。

已有 setup 依赖故障及独立环境修复证据保留；实际 Qwen 工具上下文、完整可见性/提交协议与 audit 仍缺。`validated-pilot-v8` 冻结全 135 题的新账目，维持两个可采样成员及 checker 版本。14 个 payload、两个可加载副本、参数/覆盖拒绝、28 个容器退出和 644 个关键证据引用的变更失效检查通过；共享实现未改，既有 285 项全套测试未重复运行。[本题审查](../validation/evidence/f1009-age-review-v1/summary.json)。


## F1010 提示变体分别执行与 v9 冻结

基础版要求所有圈，medium 增加正确处理进站的要求；固定项目、参考解法和 grader 字节相同。两个变体仍各自执行所有案例，不能继承对方结论。独立 raw 整数聚合得到 532 组，all-lap 与排除已记录进站圈的均值在 273 组不同，两种结果均被原生 dual-gold OR 接受。两题还分别证明 EXCEPT 忽略重复次数和隐式数值比较忽略显式 INTEGER 类型要求；native 均 weak，audit pending。

基础版增加两次完整输入构建见证。medium 的 exclusion 构建仅证明该解释下的算术，完整进站处理及历史记录覆盖仍未知，不转成完整 witness。最近整数是本次原生兼容约定；未把未指定的其他舍入约定自动判错。已有 split 将变体置于同一家族，未改动分组。

最初 8 次案例因类型检查错误在评分前中止。锁定镜像的 DB-API 返回 NUMBER，而 DESCRIBE 正确区分 INTEGER/BIGINT/DOUBLE。旧脚本/失败日志/null reward 原样保留，新版本修正后执行 60 次评分；没有覆盖旧证据或假称修复了原生评分。该故障归为验证实现问题，不归为模型行为或 infrastructure retry。

v9 冻结全 135 题新账目，其他 133 条记录及两个可采样成员不变。30 个 payload、4 个可加载副本、参数/覆盖拒绝、68 个容器退出和直接证据变更失效检查通过；未修改共享实现、原生 grader 或 reward。完整实现和全量认证继续未完成。[逐题证据](../validation/evidence/f1010-pair-review-v1/summary.json)。


## Airbnb007 六模型原始数据审查与 v10 冻结

本题合同取实际 setup 复制的 schema.yml，而非初始项目中随后被删除的模型。明确区分五张 table 和一个 incremental 模型：合法重排/源码提交与违反物化要求的 view/table 改动分别执行。初始库只有三张 raw 表，不能靠已有解答获得构建通过。

58 次实际评分包含 4 个 scoped input-built 控制和 6 个等价提交；48 个独立错误检出 26、漏掉 22。独立计算覆盖所有 raw 行，原生汇总比较不保证日期/房源关联，set equality 不保留维度重复次数，事实表仅物化检查也不能保证其内容。每项覆盖与缺口单列。

没有以参考解法决定全部语义：3256 个零有效评论房源的范围、host-newer / unmatched-host 反例、增量新/同日期/迟到输入、两条 SQL-TRIM-empty 评论及合法 key 编码仍待完成，完整 witness 保持 unknown。固定输入下重复稳定与新输入语义分别记录。评论正文在紧凑证据中用 UTF-8 SHA-256 表示，NULL 区分；完整原文由固定镜像 DB 提供，转换前多重集 hash 也保留。

原窗口变异错误连带更改 nps_28d，两个 trial 在独立证明阶段中止；旧脚本及 null reward 保留，新版本仅改 reviews_28d。坏 incremental 案例在 grader rebuild 后继续增行，前后产物变化明确记录。另有一次 native adapter 的 agent 前启动重试，第一次未标记服务就绪，第二次成功；根因 unknown，独立记录其两个容器和退出状态，不增加独立 attempt。没有覆盖原始 native reward、伪造环境 retry 或新增模型请求。

validated-pilot-v10 冻结 135 题账目，其他 134 条记录及两个可采样成员保持不变。28 个 payload、两个可加载副本、参数/覆盖拒绝、60 条 trial 的 61 次容器启动均退出及 2800 个关键持久引用变更失效检查通过。未改共享实现，既有 285 项完整测试未重复运行。完整实现和全量认证仍未完成。[本轮证据](../validation/evidence/airbnb007-six-model-review-v1/summary.json)。


## Intercom 三题与 v11 冻结

原始输入只有两个 active conversation，part 均不匹配。92次原始运行的重复行漏检支持三题 native weak；55次独立额外 fixture 补查声明的事件规则，不能认证原题未定义的业务政策。原生 SQL checker 保留，额外 expected seeds/数据显式换版，audit 仍pending。

source-only、BIGINT、行列顺序和 view 均可接受。缺源文件由 expected-test-count 守卫拒绝，缺列触发结构错误，不混入数值语义检出统计。额外列、admin/body、关闭历史和时间规则仍有说明歧义，策略拒绝不是原题错误的独立证明；合成数据领域假设也单列待审。

执行 task/case 文件、输入/输出/评分直接绑定 hash。复制证据的改动/删除/恢复检查确认失效处理，旧探针和离线脚本失败不算模型失败或 retry。v11 冻结135候选，其他132条记录和两个可采样成员不变；完整实现与全量验证仍未完成。[审查范围](intercom-review.md)。


## Daily Order 与 v12 冻结

32次原始输入案例取得真实构建见证并确认native weak：总数/总额正确不足以保证逐日期结果正确；元数据/交付错误与业务值错误分开计数。原生参考的home profile偏离明确位置要求，未静默改写；独立见证采用项目内profile。

FAILED和NULL分支在原始输入缺席，排序与额外列检查仍未执行，不以静态阅读或离线fixture冒充native覆盖。保留actual Qwen/visibility/submission/audit的未完成项；该题同时属于test split，不扩大训练候选。

新运行直接存SCRATCH并保留WORK链接。v12只更新该题，其他134条记录、两个采样成员、历史模型/重验与原始成绩保持不变。[审查和命令](../validation/deb/dbt-daily-order-summary/README.md)。


## Weekly Sales 与 v13 冻结

Weekly Sales完成56次原始数据验证，取得三层模型的完整构建见证；40次业务错误检出20、漏掉20，10次类型/交付错误检出4、漏掉6，native weak / audit pending。逐题合同来自实际说明/三层SQL/29项pytest阅读，不能沿用Daily Order的单表规则。完整原始输入见证独立重算三层输出；spot checks和内部公式一致性不足以证明各周与原始订单对应。缺模型源码但关系尚在的工作流错误单列。

原始输入缺席的分支继续pending；不把独立Python手算fixture当原生执行，也不把正确解通过转成原生validated。Oracle与实际Qwen上下文分开，补充audit未认证。本轮56条全部原始reward保留，无模型请求。

新运行直接SCRATCH，WORK留链接。v13只更新该题，其他134条、两个采样成员、历史模型/诊断成绩保持不变。[证据与命令](../validation/deb/dbt-weekly-sales-growth/README.md)。


## Channel Revenue 与 v14 冻结

Channel Revenue的v2完成44次验证：30次业务/时间错误检出10、漏检20，10次交付错误检出8、漏检2。native weak / audit pending；渠道键与缺失日期政策未解决，完整见证仍unknown。没有根据原生参考通过就修补渠道映射或把NULL渠道结果称为完整见证。明确保留源键不匹配和日期域缺口；负例围绕可独立证明的月份、金额、计数、时间与交付要求。

动态时间字段有自己的毫秒精度查询区间检查，不能统一删除。v1证明工具的精度错误和原生Decimal类型异常均保留，v2重新执行；不覆盖旧reward，不将验证工具故障当模型失败。安装路径错误也是单独的非benchmark实现记录。

v14只更新该题，其他134条及两个采样成员不变。88个新trial全部留存，新运行直接SCRATCH，无新模型请求。[证据与复跑](../validation/deb/dbt-retail-task-01-channel-revenue/README.md)。


## Spider DuckDB 1.2.2 与原始依赖包修复

按用户复现，Spider 单独固定 DuckDB 1.2.2，保留每题原始 dbt_packages（字节、mode、symlink），不在构建时运行 dbt deps。旧镜像及评分保持历史记录，ADE/DEB 环境不变。此次属于环境兼容修复，未更改原生 verifier 或任务业务 SQL。新 Python lock 和每题 SIF/provenance 记录实际身份；来源 lock 未因环境修复重写。

asana001 使用固定 dbt_asana v0.8.1 上游两模型完成两次真实构建，原生均为1，45行全部列一致。55题未修改项目检查已实际执行：30通过、10缺失节点阻断的实际gap、15其他错误失败。15题没有自带包，与缺少已声明包的五题区别记录。Spider语义工作和后续批处理暂缓，未用copy-gold通过替代真实构建兼容性；无新增模型请求。完整结果见 [环境记录](spider-environment-v3.md)。


## Phase 1 第二版与原生裁决引用纠正

第二版冻结20/131题的原生14项审查，含全部15个非broken的ADE任务；fail和实际执行后仍未决的gap均保留。没有新增模型调用，阶段二未启动。三题的已确认漏检原先只存于证据列表，缺少独立证明和实际评分的机器字段，导致裁决仍为pending。共用phase1_adjudicate以同task、同trial、评分前独立证明和native1核对后补齐字段，当前为27weak；v02快照不改写，下次正常冻结收录当前账本。

当前canonical对可变review文件引用改为已保留SHA对应的原始字节，防止继续编辑工作文件使已发布证据失效；丢失原始字节仍拒绝恢复，不把新内容冒充旧证据。该修复仅影响证据引用与裁决元数据，原始评分、案例与audit保持不变。[纠正记录](../artifacts/validation/phase1-review-v1/adjudication-correction-v2/manifest.json)。

## Phase 1 第三版与共用暂存检查

[第三版](../catalog/pools/native-phase1-v03/report.md)增加10个DEB完整原生14项审查，合计30/131，另4个broken继续隔离。当前为1 validated、36 weak、4 broken、94 pending；采样资格仍为2题。参考构建能执行与参考语义正确分别记录：已知错误或工作流违约的原参考不因原生reward=1而通过reference认证；原来的执行成功记录仍保存。interval的真实语义歧义只得到实际执行后gap，未宣称完整合法解。

`stage_phase1_reviews.py`共用身份与日志核验，只暂存root选定的真实执行审查；模板gap不合并。首次合并因说明字段不在允许的contract字段内而被拦截，6个文件完整恢复，账本未动。说明移至review顶层后新attempt成功；旧失败与两次冻结均保留。共用preflight现提前拒绝此类字段和audit覆盖，308项实现测试通过。这里的fixtures不计作benchmark执行，原生评分未改，阶段二未启动。


## 2026-11-07：节点恢复、v04 与 Spider 指定六题修复

旧 allocation 1124005 由用户取消；在 1125801 上只补四批缺少的 22 个 terminal slots。旧 partial jobs、原始分数和任务副本全部保留，completion manifest 将新结果关联回原计划，不新增 attempt 分母。三个 review group 已经逐文件核对后迁移至 SCRATCH，WORK 保留兼容链接，移走 8119 个文件。

[native-phase1-v04](../catalog/pools/native-phase1-v04/report.md) 新增十题，累计 40/131 原生14项审查完成，4 broken 隔离。全量为1 validated、46 weak、4 broken、84 pending；可采样成员仍为两题。相对参考的等价输出在题意未决时只得到 gap；已证实错误参考不因 reward=1 获得合法解身份。319 项实现测试通过，阶段二未开始、没有新增模型请求。

[Spider v4 六题](spider-environment-v4.md) 仅使用旧 frozen-deps 镜像补缺包，保留原有包；nba001 单独固定 pandas 2.2.2。真实 Qwen shell dbt run 得到2 pass、2 gap、2 fail。其余9个失败题不修；reference/independent_semantics不以copy-gold结果认证。原生 verifier 字节不变，全部旧镜像与失败构建证据保留。


## 2026-11-07：第五次阶段一冻结

[native-phase1-v05](../catalog/pools/native-phase1-v05/report.md) 累计完成 50/131 题，另 4 broken 隔离。全量原生结论为 1 validated、54 weak、4 broken、76 pending；两题采样资格不变。十题机械审查为 78 pass、11 fail、1 实际 gap，业务规则仍保留漏检和完整合法性缺口。

首次发布因归档工具重写了已有 audit 引用路径而被保护断言拦截，18 个文件全部回滚，账本与冻结池未变。修复后仅归档 native 引用，audit 及规则级 audit 分支精确保留；320 项实现测试通过，第二次发布成功。旧失败、旧脚本字节与所有历史冻结保留。阶段二未启动，无新模型请求。


## 2026-11-07：第六次阶段一冻结

[native-phase1-v06](../catalog/pools/native-phase1-v06/report.md) 累计完成 60/131 题，4 broken 隔离。原生结论为 1 validated、61 weak、4 broken、69 pending；采样资格仍为两题。本批十题的机械检查为69 pass、16 fail、5实际 gap；未执行项没有转为 gap。

Cross-sell 原始输入下四个合法输出为空，新受控输入案例单列为 additional_fixture，不计作原始数据语义检出；索引按明确输入作用域分类，并保留构造失败的未知作用域。FX 原生 verifier 再次执行 dbt，动态时间列按实际两次执行区间核对，其余28列及 SQL 类型精确保留。Harbor affinity 补跑严格 INTEGER 等价解接受，旧 v05 和原生分数不变。实现测试321项通过，v05/v06冻结完整性均复核通过；无新模型请求，阶段二未开始。


## 2026-11-07：第七次阶段一冻结

[native-phase1-v07](../catalog/pools/native-phase1-v07/report.md) 累计完成70/131题，4个broken继续隔离。原生结论为1 validated、69 weak、4 broken、61 pending；采样资格仍为ana-eng003与ana-eng005两题。本批十题的机械审查为69 pass、17 fail、4实际gap。全部非broken检查当前为706 pass、252 fail、81 gap、795 pending；full validation和阶段一仍未完成，阶段二未开始。

## 2026-11-07：恢复 Spider 并完成第八次冻结

用户解除 Spider 暂停后，DEB 收尾先完成；[native-phase1-v08](../catalog/pools/native-phase1-v08/report.md) 合并其余七个 DEB 和 Airport/Tickit/TPCH 三题，累计80/131。ADE、DEB 非隔离题全部完成原生14项审查。全量原生为1 validated、79 weak、4 broken、51 pending，采样资格仍为两题；非broken检查为782 pass、290 fail、104实际gap、658 pending。这里的完成表示已执行和裁决，包含弱评分及真实解缺口，不等于语义认证。

指定16个公开候选经 locked Qwen shell 各两次干净构建，32次原生1且重复输出一致；五个日期任务真实时钟的10次0与固定日期诊断的10次1分别保留。54题初始DB只读探针完成，按公开输入与实际扫描逐题解释隐藏材料可见性。11题原有环境fail不修，没有修改镜像、原生verifier、原始分数或新增Asana/Workday重复的参考认证批次；新变异案例在干净副本建立各自基线。有限原输入重算与同源SQL/gold一致严格区分。[执行范围](spider-resume-v4.md)。

冻结前的共同schema gate曾拒绝将copy_gold写入reference_kind；旧元数据保留，改用合法参考范围枚举并将copy_gold记在witness_kind，所有证据重核后才发布。没有为通过检查放宽schema。330项实现测试通过；阶段二未启动，没有新增模型请求。

## 2026-11-07：第九次阶段一冻结

[native-phase1-v09](../catalog/pools/native-phase1-v09/report.md) 累计90/131题。新增六个已有当前输入构建见证的应用题，以及Asset、Netflix和两题Airbnb；原生结论为1 validated、89 weak、4 broken、41 pending。非broken检查为867 pass、313 fail、121实际gap、533 pending，采样资格仍为两题。真实构建、有限独立语义、原生漏检与合法性缺口分项保留；没有因公共SQL/gold一致认证完整语义。

实际审查撤回了Recharge001的一条退款负例：NULL金额不能由`WHERE total_refunds<>0`无返回推导为已知零。原15次运行及分数保留，独立有效性不足的单案从语义覆盖中排除；其余13有效负例及1等价案继续复用。共同trial索引现在绑定该伴随裁决及hash，原始proof的自报标签不能覆盖人工核实结论。新增回归验证了变更/错绑证据不能排除别的案例，333项实现测试通过。

十项目104案的一次准备因两个大写label被共同gate拒绝，未启动trial；旧目录和失败记录保留。新计划只改label，SQL、case_id和engine不变，经全104命名预检后启动。任何后续已知错误基线的元数据解释使用独立companion，原始proof不改写。

发布前两次保护检查分别拦截了轻量Python缺少Harbor运行元数据，以及Consolidate五条已有结论缺少显式proof/result绑定；均完整回滚、保留失败记录，修正运行入口和伴随元数据后第三次发布成功。没有重跑这些案例，也未改动原生reward或audit分支。缺列导致observer不能投影时保留实际观察缺口；原参考的业务错误、明确工作流错误和解释歧义分别记录。实现测试最新结果仍为321项通过，无新增模型请求。


## Spider 原生阶段一 v10 / v11

2026-11-07，按每10题连续冻结 [v10](../catalog/pools/native-phase1-v10/report.md) 和 [v11](../catalog/pools/native-phase1-v11/report.md)，累计110/131个非隔离任务完成14项审查。4个broken保持隔离。原生结论为 {'broken': 4, 'pending': 21, 'validated': 1, 'weak': 109}；非隔离检查为 {'fail': 359, 'gap': 189, 'pass': 1013, 'pending': 273}。可采样任务仍为2题，完整认证与阶段二均未完成。

有限原输入重算中观察到ATP身高单位、Flicks演员身份、Synthea金额恒等式和NBA标志表示等不一致；分别保留实际范围。copy-gold仍只作grader fixture，reference为已执行gap，未认证真实构建。无序重排若缺合法基线，只记录reference-relative，不提升为合法等价。Recharge无独立有效性证明的退款案例保留原始运行与分数，显式排除规则覆盖。

发布前轻量Python缺Harbor依赖触发自动回滚，未修改ledger或冻结池；改用锁定runtime后发布成功，未修改任务环境。运行记录与回滚证据保存于 `artifacts/validation/phase1-review-v1/publish-v10-runtime-diagnosis.json`。333项实现测试仍为最近通过版本；本次仅归并证据及文档，无运行或评分代码变更。


## Spider 原生阶段一 v12

2026-11-07，[v12](../catalog/pools/native-phase1-v12/report.md) 冻结120/131题；本批10题已逐项核对当前身份、独立证明和原生结果。原生结论为 {'broken': 4, 'pending': 11, 'validated': 1, 'weak': 119}，非隔离检查为 {'fail': 383, 'gap': 204, 'pass': 1104, 'pending': 143}。4个broken保持隔离，采样资格仍为2题，阶段二尚未开始。

Greenhouse的两次真实构建稳定且原生1分，但都缺必需locations列，reference/reference_repeat记fail，重排仅是已知错误提交实验。Provider的主taxonomy、specialty和格式等明确错误有独立原输入证据；SAP同时保留已检出的两条一致性规则、漏检的timestamp格式和缺失原ledger输入的实际gap。有限原输入重算pass不认证全业务正确性。

QuickBooks002/Shopify002遵循用户授权的frozen-deps策略，未联网重解依赖不作为工作流合法性缺口。其relative实验的gap仅表示整份业务合法性尚未证实。旧说明及两次handoff均保留，正式冻结使用修正后的v2证据。原生verifier、reward和已冻结v01–v11均不改写；333项实现测试记录保持，新增案例分别执行当前SIF selftest与Harbor原生路径。


## 原生阶段一最终完成：v13 / v14

2026-11-07，v13冻结130题，v14单独冻结最后的Zuora，131个非隔离题全部14项完成审查；4个broken继续隔离。1177 pass、425 fail、232实际gap、0活跃pending。原生1 validated、130 weak、4 broken；audit仍独立为1 validated、134 pending。完整实现/语义认证仍为false，采样资格仍为既定2题。[最终报告](phase1-completion.md)。

最终0 pending触发Counter与普通dict对零值桶处理不同的发布校验错误。保留未发布副本和恢复证据，将工作账本恢复到v13，再以仅修改报告计数比较的共用publisher重做末批。新增测试实证修复前失败、修复后成功，同时保证重新hash的错误计数仍被拒绝；全套335项通过。原生任务、环境、verifier、reward及历史v01–v13未修改。最终全部131题当前身份/证据和14版冻结完整性已核对。


## 阶段二独立 audit 开始

阶段一完成报告与 native-phase1-v14 保持不可变。130 个 weak 题进入独立队列，其中一个已有 validated audit 保留；四个 broken 不投入。优先选当前真实见证存在且 gap 少的任务，首批为 f1003 家族、daily-order-summary、Lever。

通用 audit 与重建工具放在 scripts，新增逐题实现放在 audit_phase2_v1，不修改 native validation implementation 或原 cases 身份。证据与案例放 SCRATCH。表级比较复用既有 comparison，显式检查物理列/SQL类型/键和重复；期望值必须有独立原输入推导。

工作流认证需要可信 fresh dbt 执行，候选自写 manifest/run_results 不作证书。重建 runner 通过锁定 Qwen shell，只装声明源码，按原镜像输入重建；native verifier 原样运行，audit 单列。保存源、输入和依赖 hash，旧缺失的历史事件不由重建补造。详情见[阶段二进度](phase2-progress.md)。

## 阶段二首批冻结与发布恢复

首 10 个新 audit14 审查冻结为 [audit-phase2-v02](../catalog/pools/audit-phase2-v02/report.md)。3 个新 audit 通过，7 个保留实际 gap；加上既有 ana-eng003，全部候选 audit 为 4 validated、131 pending。train 可采样任务为 ana-eng003、ana-eng005、f1009、Lever，Daily 保持 test。原生账目与分数不变，未新增模型请求。

v01 的 audit 证据引用了合并前的可变 contract，合并后 hash 失效导致 6 项 pending。保留该无效冻结和全部发布前后字节，恢复工作账本后修复共用 publisher：只对 audit 分支归档可变输入，并在发布前后核对完整分项及结论。新增回归真实复现“原检查通过、写入新 audit 后证据失效”，全套 371 项测试通过；v02 再独立核验全部 135 题。旧 v01 现在会被加强后的 verify 明确拒绝。[恢复证据](../artifacts/validation/phase2-review-v1/publication-recovery-v1/completion.json)。

第二批十题冻结为 [audit-phase2-v03](../catalog/pools/audit-phase2-v03/report.md)，累计 20/129 个新 audit14 审查，逐项没有 pending。Geo/Risk 新 audit 通过，八题保留执行后 gap 或参考失败；整体 audit 为 6 validated、129 pending，train 可采样任务 5 题。缺少负例可区分数据时不会把实际等价变异算作检出。全部原生字段与 native-phase1-v14 保持相等。

第三批 [audit-phase2-v04](../catalog/pools/audit-phase2-v04/report.md) 累计冻结 30/129 题，整体 audit 为 10 validated、125 pending，train 可采样任务 6 题。新增 Fraud、Inventory、Category 的 audit 通过但仍是 test；Workday001 新增 train 资格。App/QB 只补齐原执行已绑定同字节的间接模块声明，旧记录保留，两个新回归禁止通过依赖修正改变判定或既有代码；380 项实现测试通过。原生账目及分数保持不变。

后续实际合法配置案例复现 Inventory 已冻结 audit 的误拒绝。修订独立排队，归档原记录与新 checker 证据，随下一批新审查发布；不会覆盖旧冻结或增加新完成任务数。新 checker 只解析可信采集中单独的字面量 `env_var` 调用，不执行 Jinja；无受信绑定的模板保留 unknown。补充发布集成回归后全套 384 项通过。此修订仍在待发布队列，v04 不作追溯改写。

第四批 [audit-phase2-v05](../catalog/pools/audit-phase2-v05/report.md) 已冻结40/129题，并单列发布Inventory修订。整体audit为14 validated、121 pending，协议级train资格9题。Xero001原生0和Consolidate合法DECIMAL变体原生0仍保留，audit通过不替换原始奖励，也不跳过训练候选的原生成功条件。

Consolidate真实执行发现runner未允许`dbt test`，四次旧错误完整保留；最小修复后四次完整deps/seed/run/test成功，原生分数差异仍在。另将预检依赖的合并前record直接归档，旧30份输入用已有备份按哈希定位；没有改写旧冻结或重跑benchmark。共用当前证据复核完成v05全部40题，新增实际文件变更回归后全套388项通过。

第五批 [audit-phase2-v06](../catalog/pools/audit-phase2-v06/report.md) 累计冻结50/129个新audit14审查，新增Weekly/Channel/CAC三个validated audit；整体17 validated、118 pending，协议级train资格12题。QuickBooks001独立原始付款重算确认三笔小数金额丢失；LTV的订单资格、Web的精度及Hive真实构建等实际gap原样保留。全部原生字段与分数不改，当前仍无新增模型请求。

第六批 [audit-phase2-v07](../catalog/pools/audit-phase2-v07/report.md) 累计冻结60/129个新audit14审查，新增Capacity validated audit；整体18 validated、117 pending，协议级train资格13题。全部60题当前身份、原生字段及声明证据通过共用复核；原生账目和既有评分未改。Airport距离和Airbnb MOM的宽界仅用于证明错误，界内不直接判正确；缺真实构建的Spider任务不认证可解性。

阶段二共享代码另完成当前版本[干净导出安装验收](../artifacts/verification/clean-export-phase2-v1/completion-v2.json)：388项测试，387通过、1项旧仓库缺失跳过；缺资产保持135pending和0采样资格。安装完全离线使用已有缓存。补丁凭据必须随源码准备；导出器漏带证据导致的两次preflight拒绝和随后按lock补齐9份凭据的过程均保留。此项不覆盖活动逐题audit、镜像全链来源或完整release bundle，implementation_complete继续false。

第七批 [audit-phase2-v08](../catalog/pools/audit-phase2-v08/report.md) 累计冻结70/129个新增audit14审查，新增Retention/Repeat/ROI三个validated audit；整体21 validated、114 pending，train协议资格14题。Repeat按题面先订单去重再忽略完全无日期订单的见证与独立原输入一致，但原生仍0；原始奖励和native结论不改，候选trace仍须满足原生成功。六道本批Spider保留实际无完整解/环境问题，有限独立重算不代替可解性认证。

第八批 [audit-phase2-v09](../catalog/pools/audit-phase2-v09/report.md) 累计80/129题新增audit审查，新增Cart/Trust两个validated；整体23 validated、112 pending，train协议资格16题。Acquisition实际四次首次构建成功、重复构建失败且四列DOUBLE违反DECIMAL要求，原生仍为1；失败与业务定义gap分别保留。所有80题当前身份、audit14证据及原生字段复核通过，无新增模型请求。

第九批 [audit-phase2-v10](../catalog/pools/audit-phase2-v10/report.md) 累计90/129个新增audit审查，整体24 validated、111 pending。Harbor Product Affinity以两次干净构建、原输入独立推导及可变阈值/去重等价案例通过；它属test，train协议资格保持16。Basket的题面项目路径冲突保留contract/reference/repeat/equivalent四gap，实际字段与稳定性通过不代替完整合法见证。90题发布后身份、原生字段和声明证据复核通过，原生分数及旧失败保持。

## 第十批补充验收冻结（2026-11-07）

[audit-phase2-v11](../catalog/pools/audit-phase2-v11/report.md)累计冻结100/129题新audit14，整体25 validated、110 pending，train协议任务17题。Running Balance接受未指定舍入方向时独立成立的nearest-cent分支，并用该分支重新计算全部下游金额/风险；非货币平均天数保持有理数语义，不误作两位金额舍入。原生CLI失败和旧错误完整保留。Product Affinity已独立证明的197处错误必须记fail，不能因另有未决策略而统记gap；修订v2只改三项裁决，不改代码/捕获/原生分数。全部100题冻结身份及证据复核通过；本轮无模型请求，完整实现验收与其余audit仍未完成。

## 第十一批冻结与历史引用（2026-11-07）

[audit-phase2-v12](../catalog/pools/audit-phase2-v12/report.md)累计110/129题新audit审查，无新增validated，train协议资格保持17。原生记录、分数及4个隔离任务保持不变。Metrics的实际中间层精度反例撤销了未发布的14pass候选；独立舍入阶段重算仍有2个效率分数差异，新checker保留5gap，不以参考SQL决定唯一语义。GL/CLTV/Shopify001已有独立业务反证的分项从gap改为fail，其他政策缺口保留。

历史路径与有效证据分开处理：旧contract原路径注释不能冒充当前文件，只有明确配对、hash相同且归档字节可核验的immutable副本可以解析。v12第一次发布被拒绝后，31个工作文件回滚核验相同；第二次使用独立attempt目录成功，保留首次失败。未放宽活动检查的precheck，未重跑benchmark。完整395项实现/fixture测试及110题当前证据复核均通过。[恢复证据](../artifacts/validation/phase2-review-v1/publication-history-alias-recovery-v1/completion-v1.json)。

提交采集声明采用显式版本路径；保持旧canonical声明与历史trial副本。新入口必须把归档合同hash与当前合同再次比较，并验证路径审查证据，防止旧合同自洽但过期时启动新采集。声明校验和真实采集分别记录；本次没有模型或新采集运行。[403项实现测试及冻结证据复核](../artifacts/validation/phase2-review-v1/submission-declarations-tool-v1/completion.json)。

正式运行采用`prepare_model_run.py`产生显式job name和最终配置身份；它复用现有准备/Harbor schema，保留原生成文件，并在加入提交声明后统一计算hash。实际发现旧生成器先hash后改job name，导致report拒绝其manifest；此修复保持核心validation代码及旧实验不变。当前模型/配置probe仅生成文件，0模型请求。[复现及407项测试](../artifacts/validation/phase2-review-v1/model-run-identity-v1/completion.json)。

v13继续区分确定业务错误、接口失败和实际未定政策：Interval的DECIMAL要求由真实物理类型检查，独立业务重算保留政策gap；最终SELECT的排序在相同中间数据、反向物理插入及ASC负例上实际执行。MMM保留每个ID/非ID差异，不把未声明的固定编号或诊断1e-6容差当作合同。十题无新增认证，120/129新增audit审查已冻结；native1validated/130weak/4broken及原分数保持不变。[v13报告](../catalog/pools/audit-phase2-v13/report.md)。

## 2026-11-09：用户接受三道 TRY_CAST 题面补丁

决策来源：用户决定，由 Claude 转达。用户接受补丁后的题面规则“下单时间为空的订单不计入”，确认以下三题算可用，应保留在候选池，使用补丁版本正常参与难度测量和后续 split。

| 任务 | 已登记的题面版本 |
| --- | --- |
| `deb__dbt-customer-cltv-forecasting` | `phase4-instruction-db2dad91b69b4407` |
| `deb__dbt-customer-churn-early-warning` | `phase4-instruction-72c0e039c28d2c84` |
| `deb__dbt-fix-repeat-purchase-cohort-revenue` | `phase4-instruction-f401f0d6f8a5c222` |

版本与补丁引用见 [phase4 task versions](../catalog/phase4-task-versions.json)。本决定确认题面及候选资格，不重写历史验证结论、原生 verifier、分数或证据；难度测量仍按既定 native 与 audit 同时通过的口径计数，仅用于分层，不用于训练。family 分组及 split 规则不变。

登记时只读核对发现一个尚未同步的清单差异：`${ARTIFACT_ROOT}/difficulty/qwen36-v1/population-v1.json` 中，CLTV 与 churn 的 `eligible_for_measurement` 已为 `true`；repeat-purchase 仍为 `false`、`audit_conclusion=pending`，尚未体现本次用户决定。该文件当前列出 66 道可测题，不能据此声称三题都已进入测量计划。按用户“只登记、不需要别的操作”的要求，本次保留该历史清单和已提交作业，记录此差异供后续候选清单与测量计划同步。

当前 A/B 核查已完成，结果见 [维护前运行与对账检查](maintenance-readiness-2026-11-09.md)；本次仅追加决策记录。

## 2026-11-09：用户批准 repeat-purchase 的 deps 构建修复及条件性纳入

用户批准，由 Claude 转达：仅在参考构建及复现 request 中补上 `/app/dbt_models_duckdb` 的 `dbt deps`；SQL、题面、audit 契约与检查器保持不变。使用 locked Qwen shell 两次干净构建，复核 native/audit，在接受的正例上复核负例并增加遗漏 deps 案例。资格满足后新增候选清单版本，并按 v2 runner/sbatch 准备四次难度测量配置，只准备、不提交。保留历史文件和已提交作业引用，不使用 GPU、不发模型请求、不操作 Slurm、不改 split 规则、不抽签、不冻结；复验失败或需要超出上述范围时停止并汇报。

实际已新增构建文本和 request，两次 Harbor 干净构建均成功，原生 reward 均为 1，`dbt deps` 返回 0、安装 `dbt_utils 1.3.3`。题目版本仍为 `phase4-instruction-f401f0d6f8a5c222`。完整 audit 重放随后遇到检查器异常：`review_support_v4.py:79` 将带 `--project-dir` 的 deps 命令当成上游模型构建，读取其正常为空的 `run_results` 时触发 `TypeError`。本次 audit 为 error/null，不能记为通过或业务 0 分。

已按用户停止条件停止后续处理。负例尚未复核，未新增候选清单或测量配置，现有 66 题清单及 pending 结论保持原样。建议另行批准新增检查器辅助模块版本：仅从实际 `run/build` 命令的 manifest/run_results 判断上游构建，同时保留独立的 deps 执行检查；该建议尚未实施。36 个受保护文件 hash 未变，两个容器清理核验通过。

本次证据位于 `$SCRATCH/dbt-bench-storage/artifacts/validation/repeat-purchase-deps-20261109-v1/`：`preparation.json`、`repeat-purchase-deps-build-v1/reconciliation-v2.json`、`validation-blocker-v1.json` 和 `cleanup-v1.json`。实际发生的构建与异常均保留，未改历史原生分数或冻结池。

## 2026-11-09：用户批准 workflow 辅助模块 v5 及历史回归

用户批准，由 Claude 转达：新增版本修复 `review_support_v4.py:79`，上游构建只统计实际 `run/build` 的 manifest/run_results，独立 deps 检查和全部通过标准保持不变。先对所有曾使用旧版评分的题目重放现有证据，除 repeat-purchase 外有任何结果不一致即停止；回归通过后继续既定正负例复核、条件性新增候选清单及准备四次 v2 runner/sbatch 测量配置。只准备、不提交，历史文件和已提交作业引用不变，不使用 GPU、不请求模型、不操作 Slurm、不改 split、不抽签、不冻结。

新增 `review_support_v5.py` 相对 v4 只增加上游命令类型过滤。13 道题、21 份历史评分（含历史修订版）、42 个 trial、126 份分阶段完整 audit 结果逐项完全一致，变化为 0；旧 repeat-purchase 的 audit0 也保持。新增 7 项针对性测试全部通过，包括旧版异常复现、deps 不冒充上游构建、缺 deps 拒绝、失败/compile 不算成功构建，以及回归入口不修改旧模块。首次回归入口在评分前发生自引用递归，失败目录保留；新增入口版本修正后完成上述回归，未重写第一次记录。

用新版辅助模块重放已完成的两次 deps 完整构建：native 仍均为 1，六份快照完整 audit 均为 1；11 个表级负例、5 个工作流负例及一个新增遗漏 deps 负例全部检出，一项合法列顺序等价案例通过。未重跑容器或模型。SQL、题面、业务检查器及 audit 契约未修改，历史分数保留。

资格登记随后被现有 `phase4_review.py:141` 拒绝：当前原始实现身份比历史身份多了 `src/dbt_bench/report_v6.py`，其余实现文件 hash 完全相同。`validation.identity()` 仅排除字面文件名 `report.py` 和 `trace.py`，因此把后来新增的报告模块计入了身份。这是辅助模块修复以外的身份兼容问题，尚未修改检查规则或绕过该 gate。已停止候选登记与配置准备，当前清单仍为 66 题、历史 audit 结论仍为 pending；正负例通过不冒充资格登记完成。

回归及阻塞证据位于 `$SCRATCH/dbt-bench-storage/artifacts/validation/repeat-purchase-helper-v5-20261109-v2/` 的 `regression.json`、`unit-tests.json`、`qualification-blocker-v1.json`；新正负例位于 `repeat-purchase-deps-20261109-v1/review-v3/`。后续需要批准对这一明确报告模块增量做版本化身份兼容，保持任务、数据、checker hash 和历史身份不变；本次未实施。

## 2026-11-09：用户批准精确报告身份兼容及 repeat-purchase 新版资格登记

用户批准，由 Claude 转达：只按文件名和当前 hash 放行 `src/dbt_bench/report_v6.py` 这一新增报告模块，其余实现文件逐个 hash 一致，保留历史身份以及题目、数据、checker hash。完成引用核查后继续登记新增候选清单，并按 v2 runner/sbatch 准备 repeat-purchase 的四次测量配置，只准备、不提交。

静态导入核查发现正式代码只有 `scripts/report.py` 导入该模块，另有 `tests/test_training_purpose_exclusion.py` 的报告测试导入。审查、打分和资格登记路径无该依赖；实际导入及登记过程还启用了阻止直接/间接报告模块导入的 guard。新增 [phase4_review_identity_v2.py](../scripts/phase4_review_identity_v2.py) 只接受历史实现清单额外增加 `report_v6.py`、SHA-256 为 `20cd8f08f6fdfedfda98ae21200858c4928b636b33cd4ea5159a284bfe3392aa` 的情况。它将实际身份与精确兼容凭据作为 companion 保存，历史身份保持原值。旧 identity、precheck、stage 和报告代码均未修改；不是通配排除，其他文件增删改、任务、数据、环境或 checker 漂移仍被拒绝。

复用此前实际完成的两次 locked Qwen shell 干净构建和 v5 辅助模块回归证据，新增资格记录位于 `$SCRATCH/dbt-bench-storage/artifacts/validation/repeat-purchase-deps-20261109-v1/qualified-v4/ready/deb__dbt-fix-repeat-purchase-cohort-revenue/record.json`。audit 14 项均 pass、结论 validated；两次原生 reward 为 1，六份实际提交快照完整 audit 均为 1。原生协议仍为 weak（12 pass、2 fail），原生漏检和旧 pending/失败记录完整保留。此前已完成的 11 个表级、5 个工作流、1 个遗漏 deps 负例以及合法等价案例的证据继续引用，本次未重跑构建或打分。

新增 [population-v2.json](${ARTIFACT_ROOT}/difficulty/qwen36-v1/population-v2.json)：候选数 66 → 67，ADE 15、Spider 16、DEB 36。只有 repeat-purchase 的当前资格发生变化；其题面版本保持 `phase4-instruction-f401f0d6f8a5c222`。其余 134 条任务记录、全部 135 条历史冻结池行、family、task version 和历史 split 都保持。`frozen_pool_row` 字段明确保留旧快照，由新的 `qualified_review` 作为本题当前资格依据，未修改旧 pool 或 population-v1。

新增 [测量准备结果](${ARTIFACT_ROOT}/difficulty/qwen36-v1/repeat-purchase-measurement-v1/completion.json)，复用既有 Harbor + Qwen Code 和 Qwen3.6-35B-A3B 配置：一个 v2 批次，模型服务只加载一次，四次独立 attempt、单并发、零自动重试；每次 agent 4000 秒、verifier 3000 秒的原生预算保持。准备的 sbatch 仍为 `${ALLOCATION} / gh / 48h`，沿用 v2 的 24–48h allocation policy 和 TimeMin=24h。现有八个排队作业及其旧 runner、TimeLimit/TimeMin 均未操作。原生与 audit 同时通过的成功口径、四次有效 attempt、分层用途和禁止训练约束保持；无抽签、无冻结。

[最终复核](${ARTIFACT_ROOT}/difficulty/qwen36-v1/repeat-purchase-measurement-v1/verification-v1/summary.json)记录：新增 13 项针对性测试全部通过，实际生成配置通过 Harbor schema、原生任务只增加观察采集的核对及 v2 runner preflight；223 个受保护历史文件 hash 全部未变，其中包括排队批次的 81 个引用。本次 GPU 使用、模型请求及 Slurm 操作均为 0。准备命令文本留在测量目录的 `submission-command.txt`，没有执行。

## 2026-11-09：用户批准提交 repeat-purchase 四次难度测量

用户批准，由 Claude 转达：按 `repeat-purchase-measurement-v1/submission-command.txt` 从 login1 提交这一个作业，记录作业号和提交证据；现有八个作业不修改、不取消，不提交其他测量。提交前再次通过既有 v2 runner preflight，核对命令仅包含本题四次 attempt。随后通过 `timeout 60 ssh login1 '…' < /dev/null` 执行一次原定 `sbatch`，成功返回作业号 **1133533**，提交账户 `${ALLOCATION}`、分区 `gh`、TimeLimit `48h`、TimeMin `24h`，配置和已绑定源码均未改动。测量仍仅用于难度分层，禁止训练。

2026-11-09 21:53:33 CST 的只读查询显示 `PENDING`，原因为 `ReqNodeNotAvail, Reserved for maintenance`，开始时间尚未确定。原始 sbatch 输出包含 TACC 提示信息，首次本地整串解析未提取作业号；从原始回执的唯一 `Submitted batch job 1133533` 行及 squeue/scontrol 交叉确认，未再次提交。首次解析记录、原始 stdout/stderr、退出码、提交 intent、当前状态以及[核对后的提交回执](${ARTIFACT_ROOT}/difficulty/qwen36-v1/repeat-purchase-measurement-v1/submission-v1/receipt-reconciled-v1.json)均保存在新建的 `submission-v1/`，旧的“只准备”完成记录保留为历史。此次仅提交这个作业并查询其状态，未操作现有八个作业，未抽签或冻结。
