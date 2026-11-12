# 补充 audit 阶段

阶段一已完成并汇报，全部 135 题的原生账目保留在 [native-phase1-v14](../catalog/pools/native-phase1-v14/report.md)。阶段二已开始：130 个 weak 题进入独立 audit 队列，其中 ana-eng003 的既有 validated audit 保留，其余任务按真实解、已执行 gap 和失败项数量排序。4 个 broken 题继续隔离。 最新 [audit-phase2-v14](../catalog/pools/audit-phase2-v14/report.md) 已冻结 129/129 个新审查：整体 audit 为 25 validated、110 pending，协议级 train 资格 17 题。完成审查不等于通过认证。

首批并行审查 f1003、f1003-hard、daily-order-summary 与 lever001。任务子 agent 只写各自新增 audit 目录和 SCRATCH 审查目录；root 负责共享工具、合并与冻结。新增案例、自测和实际运行产物都在 SCRATCH。不发模型请求，不改原生 verifier、镜像或奖励。

当前已有共用表级 audit 工具，检查显式列集合、SQL 类型、行粒度、主键、重复行和按键值对应，复用原来的精确整数及逐列数值容差比较器。缺失或损坏的采集返回 unknown/null。业务预期必须另有独立来源；表值相符不认证 dbt 工作流。

干净重建沿 Harbor 的新环境与锁定 Qwen shell service 执行，不调用模型、不上传 solution。只安装声明的提交文件，清除候选提供的 target/logs，然后记录真实 dbt 进程、输出、源文件、依赖库存和新生成的 manifest/run_results。原生 verifier 随后按原路径运行，补充 audit 另行评分。重建成功不能追认旧提交缺失的历史行为证据。

当前机器记录：

- [全部 weak 任务清单](../artifacts/validation/phase2-review-v1/inventory-v1.json)：队列不是已执行检查。
- [分工](../artifacts/validation/phase2-review-v1/assignments-v38.json)与[工作状态](../artifacts/validation/phase2-review-v1/state.json)。
- [Lever 两次基线重建对账](../artifacts/validation/audit-phase2-lever-rebuild-v1/reconciliation-v1.json)：均原生 1，重复输出和跨上下文传递相符。
- [Lever 四次工作流案例](../artifacts/validation/audit-phase2-lever-workflow-v1/reconciliation-v1.json)：注释等价版本两次原生 1；删除目标源文件两次构建失败、原生 0，缺输出的比较保留 unknown。
- [413 项实现/fixture 测试](../artifacts/validation/phase2-review-v1/implementation-tests-v15.json)。此结果不代替真实 benchmark 验证，也不覆盖随后修改的工具版本。

可运行入口（请求 JSON 必须使用逐题审查后明确的路径与 hash；不要复用已有输出目录）：

```bash
export PYTHONPATH=src:scripts
export PYTHONDONTWRITEBYTECODE=1

runtime/.venv/bin/python scripts/phase2_inventory.py \
  --assignments artifacts/validation/phase2-review-v1/assignments-v1.json \
  --output "$SCRATCH/dbt-bench-phase2-inventory-new.json"

runtime/.venv/bin/python scripts/prepare_audit_rebuild.py \
  --base-batch artifacts/validation/native-spider-v4-public-witness-v1 \
  --requests artifacts/validation/phase2-review-v1/lever-rebuild-request-v1.json \
  --output "$SCRATCH/dbt-bench-lever-audit-new"

# 在 compute allocation 内执行；Harbor 管理并发与 attempt。
PYTHONPATH="$SCRATCH/dbt-bench-lever-audit-new/tools" \
  runtime/.venv/bin/harbor run -c "$SCRATCH/dbt-bench-lever-audit-new/config.yaml"

runtime/.venv/bin/python scripts/reconcile_audit_rebuild.py \
  --batch "$SCRATCH/dbt-bench-lever-audit-new" \
  --output "$SCRATCH/dbt-bench-lever-audit-new/reconciliation.json"

runtime/.venv/bin/python scripts/phase2_review.py \
  --overlay /absolute/path/to/reviewed-audit-overlay.json \
  --output "$SCRATCH/dbt-bench-audit-precheck-new.json"
```

precheck 允许保留 partial review，但不会把它计为 audit14 完成。pass/fail/gap 必须有当前有效证据，运行项还须有实际执行证据；未运行保持 pending。原生结论和 audit 结论独立。新完成审查满 10 题后统一冻结，末批不足 10 题单独冻结。首批 10 题已冻结为 [audit-phase2-v02](../catalog/pools/audit-phase2-v02/report.md)：Lever、daily-order-summary 与 f1009 独立 audit 通过；两道 f1003、f1010、ana-eng004、ana-eng006、app_reporting002 与 apple_store001 保留已执行的 gap。第二批已冻结为 [audit-phase2-v03](../catalog/pools/audit-phase2-v03/report.md)，累计完成 20/129 题新增审查。第三批已冻结为 [audit-phase2-v04](../catalog/pools/audit-phase2-v04/report.md)，累计完成 30/129 题。第三批冻结时全部 135 题 audit 为 10 validated、125 pending，其中包括原有 ana-eng003；train 采样资格合计 6 题。完成审查不等于通过认证；实现验收和全量 audit 仍未完成。

执行统计可重复运行：

```bash
runtime/.venv/bin/python scripts/summarize_audit_execution.py \
  --review artifacts/validation/phase2-review-v1 \
  --output "$SCRATCH/dbt-bench-audit-execution-new.json"
```

该入口重新核对已登记完成批次的原始 result/execution hash，拒绝缺失、重复或变更结果，保留多个 reward 指标及 null；原生得分、Harbor 异常、构建错误、实际 nop 分开统计。输出归档读取时的状态，后续发布不会使该历史统计失效。

共同发布入口为 `scripts/phase2_publish.py --stage <完整 overlay>`、`--publish`、`--verify <冻结目录>`；每十个新完成审查冻结一版，只有全队列末批允许 `--final-remainder`。stage 不修改 canonical ledger 或已有池。最终发布另核对 native 记录逐值不变、135 题对账与 test split 排除。

首次 v01 出版未固定 audit 引用的合并前 contract 字节，6 个分项被重新置为 pending。旧版保留但已判无效；恢复 25 个工作文件后，共用 publisher 先归档可变证据，再合并，并验证所有 audit 分项与 precheck 完全一致。v02 的 10 题没有 pending 分项，135 题原生记录不变；没有为恢复新增 benchmark 运行。[诊断与恢复](../artifacts/validation/phase2-review-v1/publication-recovery-v1/completion.json)。

当前已对账 [341 次无模型运行](../artifacts/validation/phase2-review-v1/execution-summary-v44.json)，包含下一批任务的构建/工作流案例，不是模型成功率。新增 `after_command` hook 用指定 `command_index` 在首次 dbt run 后保留只读输出，随后第二次 run 可检查幂等；此接口已以两次实际子进程的不同状态验证，旧批次中的 driver 字节保持不变。

第二批十题为 Geo、Risk、Email、Paid、Google Play、HubSpot、Jira、Marketo、Intercom 与 Twilio。Geo/Risk audit 通过；其余八题保留实际 gap 或参考失败。HubSpot 两次参考构建的 campaign 计数与独立原始事件重算冲突。Email/Paid 原始事件无 purchase，已执行的禁用 fallback 变异与原输出一致，不能认证收入来源选择的负例覆盖。Geo 的 0.625 舍入边界以实际 fixture 证明 v5 误拒绝 0.62，v6 接受两个合法最近舍入端点而仍拒绝非舍入偏差；旧版本及重放证据保留。

Product Category/Performance、Fraud/Fulfillment 与 Workday001/Recharge001/002 已进入第三批冻结。已完成的重建与工作流证据继续保留。

当前重建清理曾在既有 `/app/dbt_models_duckdb/target` 遇到 EIO：Performance 两次原生 0、Paid 四次原生 1，均未执行可信 agent dbt 构建。这六次错误保留，不能由 native reward 推断构建成功。只对受影响题使用新 `audit_target`/`audit_logs` 路径，CLI、清理及采集范围一并固定；镜像、原生 verifier 和已成功的 Category/Email 不重跑。原始构建错误与修正版分别对账，见各批次 manifest 与逐题 correction 记录。

第三批十题是 Fraud、Fulfillment、Inventory、Product Category、Product Performance、App Reporting001、QuickBooks002、Recharge001/002、Workday001。新增四个 audit 通过：Fraud、Inventory、Category 为 test，Workday001 为 train；其余六题保留实际 fail/gap。原生账目与分数不变。

App/QB 的间接配置模块在原证据中已有同字节哈希，发布前追加到 code_refs；[依赖纠正核验](../artifacts/validation/phase2-review-v1/dependency-amendments-v1/verification.json)保留两份旧交付和新声明。共用 `--extend-staged-dependencies` 仅允许给未发布记录追加依赖，禁止改变代码哈希、判定或已冻结记录；没有重跑 benchmark。

第四批 [audit-phase2-v05](../catalog/pools/audit-phase2-v05/report.md) 已冻结：Consolidate、DQ、Loyalty、Order、Asana、Greenhouse、Shopify001/002、Workday002、Xero001，累计 40/129 个新审查。Consolidate/DQ/Order/Xero001 新 audit 通过，整体为 14 validated、121 pending，协议级 train 资格为9题。Consolidate仍为test；Xero001实际 audit1/native0 不能成为要求原生成功的训练候选。Asana/Workday002 未重跑原始参考，实际gap保留。下一批已收到Weekly、Web、Channel、CAC、Xero_new001/002和Pendo的完整交接，逐项通过预检后暂存，未满十题不发布。子agent继续Capacity、Customer LTV、GL Reconciliation和QuickBooks001/Hive001。

Inventory 的单个合法配置案例已实际复现旧 audit 对 `env_var` 数据库路径和目录尾斜杠的误拒绝。新版本接受同一次构建，并重放已有 25 个案例及两次真实构建的四份初始/提交采集；旧代码、分数、冻结记录均保留。[修订交付](../artifacts/validation/phase2-review-v1/shared_projects/inventory-loyalty-v1/inventory-profile-followup-v1/handoff-v1.json)已随v05发布，没有增加新完成题数。共用 `--stage-revision <overlay> --revision-reason <原因>` 保留旧记录并单列修订；集成测试实际核对十个新记录加一个修订的完整发布。

Order 的额外小输入 fixture 证明旧 audit 将最小非零浮点误当作零分母应返回的精确零。修订版仅收紧数学零，保留原误接受，重放 40 次 audit 并通过独立零值边界测试；原始数据未发现该零分母行，报告不将其称为原数据检出。

Consolidate 四次重建暴露共用 runner 未支持题目要求的 `dbt test`；旧批次完成 deps/seed/run 后报执行错误，reference 原生 1×2、DECIMAL 等价案原生 0×2 全部保留。新增 test 支持并将命令校验前置到源码安装前，实际子进程回归验证失败退出码和日志不丢失；全套 386 项通过。独立 v2 的四次 deps/seed/run/test 全部 exit0，原生分数差异仍保留。[根因与版本](../artifacts/validation/phase2-review-v1/driver-test-support-v1/diagnosis-v1.json)、[实际补验](../artifacts/validation/phase2-review-v1/driver-test-support-v1/completion-v1.json)。

Xero_new001 两次四表补采均实际 dbt run exit1、原生0：候选把原始组织的2月29日用当前年份拼成无效的 `2026-2-29`。该构建失败与历史2024固定时钟诊断分开保留，没有修候选或修改镜像。日期族五题的新 nop 均原生0且明确跳过构建；完整 audit 裁决仍由逐题证据决定。

v05归档预检所用的合并前record，使发布后仍可直接核对输入哈希。v02–v04的30份旧预检输入原已保留在发布备份，新增[按哈希定位索引](../artifacts/validation/phase2-review-v1/precheck-reference-publication-v1/historical-precheck-records.json)，旧冻结与评分未改。回归实际复现旧链接失效并验证修复。

当前身份与证据复核也使用共用入口；[v12结果](../artifacts/validation/phase2-review-v1/published-evidence-v12.json)覆盖全部110题，均通过：

```bash
runtime/.venv/bin/python scripts/verify_current_audits.py \
  --pool catalog/pools/audit-phase2-v14 \
  --output "$SCRATCH/dbt-bench-current-audit-evidence-new.json"
```

该入口先核对不可变冻结，再核对当前任务身份、原生字段、audit14完成度及声明的证据哈希；不会把证据复核计作新的业务执行。

第五批 [audit-phase2-v06](../catalog/pools/audit-phase2-v06/report.md) 新增 Weekly、Web、Channel、CAC、Customer LTV、Hive、Pendo、QuickBooks001、Xero_new001/002，共十题。Weekly/Channel/CAC 的 audit 通过，另七题保留实际 fail/gap；50/129 新审查已冻结，全部原生记录仍与阶段一相同。GL 已交接并暂存下一批：参考遗漏15个有原始交易的账户是明确失败，未知科目类型的余额表示和 trial balance 口径单独保留 gap。子 agent 继续 Capacity、RFM/CLTV 与 Reddit/SCD，随后按已登记分工推进营销归因/ROI。

[RFM/CLTV八次重建](../artifacts/validation/audit-phase2-rfm-cltv-rebuild-v1/reconciliation-v1.json)均保留原生1分，但CLTV四次的第二个dbt run均exit1：`analytics`与已有`ANALYTICS`触发approximate relation match。首次run均exit0且有独立产物，整套runner的build_success仍为false，未重跑或修schema。首次构建的可信工作流、后续失败与客户/收入语义问题分别审查，不用原生1分推定构建或语义通过。

第六批 [audit-phase2-v07](../catalog/pools/audit-phase2-v07/report.md) 新增 GL Reconciliation、RFM、CLTV、Capacity、Reddit、SCD、Google Play002、Qualtrics、Airbnb 与 Airport，共十题。Capacity 的完整原始输入重算、工作流和有效负例通过；其余九题保留实际 fail/gap。60题的当前身份、原生字段、audit14和全部声明证据哈希已由共用入口复核通过。GL遗漏账户、CLTV第二次构建失败、Spider缺少真实构建及未明确的业务口径分别记录，没有由原生满分推定通过。

下一批继续营销归因/ROI、Retention/Repeat 和 Playbook/Social。Repeat 的旧候选先过滤空日期再去重，与题目要求冲突；原始输入独立重算后，仅移动过滤位置的修正另存为新候选，两次干净构建和匿名客户计数负例均已实际完成，原生0分保留。最终 audit 仍须逐题复核，不由构建成功自动认证。

共享实现的新[干净导出验收](../artifacts/verification/clean-export-phase2-v1/completion-v2.json)已完成：离线锁定安装、四项运行时补丁及幂等检查通过；388项测试中387通过、1项因独立导出没有旧训练仓库跳过。Harbor/Qwen schema与error-only转换检查通过。没有任务资产时135题全部pending、采样资格0，空报告正确返回失败。该导出只覆盖共享实现、任务声明和独立fixture，排除历史大证据、冻结池及仍在修改的逐题phase2 audit；不是完整发布包，也不替代逐题认证。导出器先漏掉必需补丁证据，第一次只补mirror仍不完整；两次拒绝均保留，最终按锁定元数据补齐9份原字节凭据后继续成功，原仓库与锁均未改。

[镜像依赖实查](../artifacts/verification/image-dependency-inventory-v1/review-v1.json)覆盖131个非隔离任务、72个去重后的SIF；全部镜像及声明构建证据hash仍一致。实际记录各Python解释器的包版本/metadata、dbt shebang、Node/npm、系统包和dbt包树指纹；只保存所需清单，不复制整份工作区。Spider DuckDB均为1.2.2，NBA pandas为2.2.2；DEB session-attribution实际pandas为2.2.3，与其余两个DEB镜像的2.2.2分别记录，不改环境。airbnb005镜像缺少预装的独立Harbor server环境，启动阶段依赖安装仍需单独审查；本项不认证源码重建闭包或跨后端等价。新探针4项文件变化、权限及符号链接回归通过，原388项共享实现测试仍单独保留。

在compute node按现有镜像重复该只读依赖检查，使用新的输出目录：

```bash
PYTHONPATH=src:scripts PYTHONDONTWRITEBYTECODE=1 runtime/.venv/bin/python \
  scripts/inspect_image_dependencies.py \
  --output "$SCRATCH/dbt-bench-storage/artifacts/verification/image-dependency-inventory-v2"
```

该入口去重镜像并逐题绑定，默认排除四个隔离题；不执行dbt、参考解、verifier或模型请求。逐题真实上下文检查仍使用原来的Harbor验证路径。

第七批 [audit-phase2-v08](../catalog/pools/audit-phase2-v08/report.md) 冻结 Retention、Repeat Purchase、Multi-touch、ROI、Playbook、Social、Tickit001/002、ATP Tour、Flicks，共十题。Retention/Repeat/ROI audit通过；其余七题保留实际gap及既有环境fail。Repeat修正见证与实际CTE等价均audit1/native0，原生0不会被补充评分替换。全部原生记录仍与阶段一相同；70/129个新审查完成，整体21 validated、114 pending，train协议资格14题。

用户短暂停止并恢复目标后，原三个worker已不在live tree；按磁盘未完成步骤恢复三个实例，已结束的Harbor未重跑。[恢复记录](../artifacts/validation/phase2-review-v1/worker-recovery-20261107-v1/recovery.json)。当前继续Cart/Trust、Supplier和SAP/Zuora。Payment全部12张发票晚于固定分析日的负age处理，以及Scorecard10/39无eligible-cost供应商的零/NULL政策，实际重算后单独保留定义gap，不因候选或原生满分匹配而认证。

随后 Cart、Trust、Supplier Payment、Supplier Scorecard、SAP、Zuora、Shopify Holistic、Synthea 八题完成 root 复核，连同 Funnel/Acquisition 一起进入 v09。Cart/Trust 的 audit14 全部通过；供应商两题保留实际政策分歧，四道 Spider 保留原环境失败、真实解及原始数据缺口。这批完成后继续下一组逐题审查。两组 Spider 的新 nop 均已完成，只采集未构建状态，没有运行参考解；Holistic/Synthea 缺 last-touch 事件和缺事件 ID 对应依据均已实查，不能以原始总额代替完整输出。

Trust 的最终 checker 通过两次独立干净构建、196字段完整比较、43组独立错误案例与两个合法变体。无匹配聚合分组的NULL/0表示由原始输入证明后允许等价；旧checker在实际合法变体上的误拒绝保留为回归证据，没有放宽已有分组或原始维度NULL的要求。

[Funnel/Acquisition 八次重建](../artifacts/validation/audit-phase2-funnel-acquisition-rebuild-v1/reconciliation-v1.json)已全部结束，原生 reward 均为1。Funnel四次双run成功；Acquisition四次依赖、上游和首次目标run成功，第二次目标run均失败，完整构建状态为false。未删表重置或修改原生环境，第二次构建失败与原生1分分别保留。

第八批 [audit-phase2-v09](../catalog/pools/audit-phase2-v09/report.md) 已冻结并完成80题当前身份/证据复核。累计80/129个新增审查；整体audit为23 validated、112 pending，train协议资格16题。Cart/Trust新增validated，其余本批八题保留实际fail/gap；所有135题原生记录不变。Analytics/Asset已审查并暂存下一批，继续Basket/Affinity、LIFO/ABC及Spider F1002/Maturity。

Basket/Affinity v1四次在dbt命令前被runner拒绝：请求含未允许的DBT_LOG_PATH环境覆盖，runs为空、原生0全部保留。v2只删该覆盖，保留显式run --log-path；镜像和共用runner未改。该请求错误计入producer/build失败，不解释为任务能力或业务SQL失败。

Basket/Affinity v2四次构建已结束，均build_success=true、原生1；Spider F1002/Maturity两次nop也已结束，均原生0。实际对账与六容器清理检查通过，后续业务audit仍由逐题证据裁决；未以构建或原生成功提前认证。

LIFO/ABC四次双run构建完成且均成功，ABC原生1×2、LIFO原生0×2；独立原输入审查仍保留成本池为空、元数据冲突与舍入政策问题，不从构建成功推导语义通过。F1002/Maturity已完成有限audit14复核并暂存，连同Analytics/Asset共四题等待下一次十题冻结。

TPCH001/002的audit14已完成复核并暂存，下一批累计六题。001保留金额口径/阈值缺口，002保留必需原输入缺表和原环境失败；002说明另存v2，明确连接/目录枚举成功不等于必需源关系可读，评分与执行证据未变。Basket完整合法见证还需处理题面项目路径冲突：当前只证实实际项目目录下的数据与运行，不改路径或借原生1消解矛盾。

LIFO/ABC已完成audit14复核并暂存，下一批累计八题。LIFO为8pass/6gap，ABC为8pass/2fail/4gap；完整audit均pending。已实际重算/执行的空池、等价算法分支、元数据、舍入政策问题与明确类型/日期格式违规分开记录，不由原生分数判定完整正确性。后续金融同族审查转向Settlement/Running Balance。

第九批 [audit-phase2-v10](../catalog/pools/audit-phase2-v10/report.md) 冻结 Basket、Harbor Product Affinity、LIFO、ABC、Analytics Engineering、Asset、Spider F1002、Maturity、TPCH001/002，共十题；90/129个新增audit14审查已冻结并完成当前身份/证据复核。整体24 validated、111 pending，原生记录仍逐值不变。新增validated的Harbor Product Affinity属test split，train协议资格保持16；允许去重及显式阈值变化的实际等价案例通过，没有把固定2198行当作唯一正确输出。Basket因项目路径冲突保留4gap，其余任务以各自证据裁决。

Airbnb002/F1001已root复核并暂存下一版，保留原跨上下文失败/初始答案可见失败及真实构建缺口。后续批次继续Settlement/Running Balance、Product Affinity/Velocity和NBA/Netflix；当前新审查仍无模型请求。

第十批 [audit-phase2-v11](../catalog/pools/audit-phase2-v11/report.md) 冻结 Running Balance、Settlement、Product Affinity、Sales Velocity，以及 Spider Airbnb002、F1001、NBA、Netflix、Activity、Provider；100/129个新增audit14审查完成当前身份与全部引用复核。Running Balance的真实双初始化/双run、38字段重算和41项目标负例支持audit validated，train协议资格增至17。Settlement保留类型失败及两条规则缺口；Affinity的197个已独立证明错误以修订v2将三项gap更正为fail，旧v1及原生1分均保留。Spider六题仍按各自可见性、语义错误和真实解缺口保持pending。Inzight已完成审查，另行暂存下一批，不提前计入本次冻结。

下一批已有7题通过root复核并暂存：Inzight、Coupon、Monthly Channel Revenue、两道Fulfillment及两道RFM。另有GL、CLTV、Shopify001三项裁决修订：独立重算已证明的业务错误从gap更正为fail，未决子规则仍保留gap；仅因先前发布加入audit元数据的身份变化已与不可变旧contract精确核对，原生字段、代码和执行记录不变，修订不增加新审查计数。

Fulfillment Metrics在root复核时发现旧补充checker误拒中间层未舍入的合法表示：原始输入的54个中间cell保持精度、最终21列不变，旧版仍拒绝。实际重算还显示2类订单的效率分数受舍入阶段影响。新版独立保留这一政策gap，完成已有3次实际trial、11个目标负例和10个capture fixture的重放；最终9pass/5gap、整体pending，撤销尚未发布的14pass候选。[反例与修订](../artifacts/validation/phase2-review-v1/shared_projects/fulfillment-pair-v1/deb__dbt-order-fulfillment-metrics/handoff-v2.json)。Analytics的145个已证明仓库计数/缺键错误仍为fail，已暂存checker不变。

[CLV/Cross-sell四次干净构建](../artifacts/validation/audit-phase2-clv-cross-sell-rebuild-v1/reconciliation-v1.json)均原生1，但只有CLV两次的双run完整成功；Cross-sell两次整体构建失败，保留失败路径且未删表重置。[Churn两次nop](../artifacts/validation/audit-phase2-churn-cohorts-warning-nop-v1/reconciliation-v1.json)已完成真实未构建采集。两组共6次均完成对账及容器清理，逐题audit仍由子agent审查，不能据原生1认证。

[Test Orders/HR四次构建](../artifacts/validation/audit-phase2-test-orders-hr-rebuild-v1/reconciliation-v1.json)也已完成，两次独立初始化的双run均成功、原生均1，四个容器的进程与端口清理通过。判定只使用显式输入投影、19个目标关系及必要源码，没有整份保存`/app`；HR既有业务错误及Test Orders额外过滤标记的未决范围继续逐项审查。

第十一批 [audit-phase2-v12](../catalog/pools/audit-phase2-v12/report.md) 已冻结上述七题、Churn两题和Customer Lifetime Value，累计110/129题新审查，全部当前身份与证据复核通过。GL/CLTV/Shopify001三项历史裁决同时修订，不增加完成题数。整体audit保持25 validated、110 pending，train协议资格17题；全部135题原生记录逐值不变。54道非隔离Spider的补充audit审查现已全部冻结，实际缺口仍按逐题记录保留。Cross-sell完整审查已暂存下一批，Test Orders/HR已交接待root复核。

首次v12发布在历史contract路径注释处被hash检查拒绝，31个工作文件全部逐字节回滚，失败目录保留。共用publisher仅对显式`original/immutable`配对且字节hash相同的历史引用使用归档副本，缺失、变更或未声明别名仍拒绝。重试通过`--publish --publication-attempt 2`写入独立artifact目录，未覆盖旧尝试；完整395项实现/fixture测试通过。[根因、精确回滚与恢复](../artifacts/validation/phase2-review-v1/publication-history-alias-recovery-v1/completion-v1.json)。本次无benchmark重跑或模型请求。

CLV/Cross-sell旧自测报告的task_key被列名变量覆盖，实际输入与checker仍按各自任务运行。新私有自测版本增加task/spec身份断言并分别重跑host及固定SIF各30项，全部通过；旧错标签及输入hash对应证明保留，旧记录未被改写。

版本化提交声明入口已经实现，gh-dev smoke 可显式选择 `DBT_BENCH_SUBMISSION_SPEC`，参考批次可选择 `--submission-spec`。实际当前声明检查与403项实现测试通过，110题冻结证据仍有效；三份过期声明的逐题替代与实际采集验收尚未完成，不自动刷新合同hash。[实现证据](../artifacts/validation/phase2-review-v1/submission-declarations-tool-v1/completion.json)。

正式模型运行配置新增`prepare_model_run.py`入口：原生成器在计算experiment hash后改写job name的问题已实际复现，新入口以最终明确job name/config/capture生成自洽身份。锁定Harbor schema及407项实现测试通过；未执行probe配置、未启动模型。[实际检查与保留的旧生成文件](../artifacts/validation/phase2-review-v1/model-run-identity-v1/completion.json)。

第十二组十题已冻结为[audit-phase2-v13](../catalog/pools/audit-phase2-v13/report.md)，累计120/129新增审查：Cross Sell、Customer Order、MMM、HR、Stockout、Interval、Payment Fraud、Price Elasticity、Session、Test Orders。无新增validated，整体25validated/110pending，协议级train资格17不变。HR明确薪酬/经理业务错、Stockout的NULL比率错、Cross Sell连续run失败保留；Interval类型错误与未定业务政策分开，排序依据实际compiled SELECT而非物理扫描。MMM完整ID变化及数值微差保留，但未声明固定编号的合法性仍gap；Session独立金额/归因及实际查询排序通过，路径矛盾仍gap。Price两次avg_price为138.39/138.40的精确差异不删除，cap顺序等未定政策另列。剩余9题已分工，最后不足十题统一冻结。

v13发布后[120题当前身份/证据复核](../artifacts/validation/phase2-review-v1/published-evidence-v13.json)全部通过；不改变任何native记录，末批9题仍在进行。

提交采集新增 schema2 文件/目录类型声明，实际原生 ArtifactHandler 在本地传输 fixture 上验证 extensionless 文件、目录、失败/缺失/重复及内容变更；不计作 benchmark。413项共享实现测试通过，120题冻结证据仍有效。[实现证据](../artifacts/validation/phase2-review-v1/submission-file-support-v1/completion.json)。当前共享实现的新干净导出也通过锁定离线安装、补丁幂等、413项测试（412通过、1项缺旧仓库跳过），无资产时135题全部pending。[验收范围](../artifacts/verification/clean-export-phase2-v2/summary.json)。

末批九题已冻结为[audit-phase2-v14](../catalog/pools/audit-phase2-v14/report.md)，129/129新增审查全部完成，130题audit14（含既有ana-eng003）全部具有执行/调查记录，整体25validated/110pending。最后九题均保留实际缺口；没有把原生1或稳定性替代完整语义证明。最终129题当前身份、原生字段与证据复核全部通过。[交付报告](run-results.md)、[机器摘要](../artifacts/validation/phase2-review-v1/final-delivery-v1/summary.json)。
