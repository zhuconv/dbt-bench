# 阶段三：train 独立解复现与题面版本

本阶段只处理 train，不请求模型。旧 `validation/ledger.jsonl`、逐题旧
`record.json` / `contract.yaml`、`audit-phase2-v14` 和全部 test 记录保持原字节。
新记录以 companion ledger 保存，不覆盖原生 reward。

输入为 `${SCRATCH_ROOT}/claude-two-version-pilot/results.json`。
已核对 61 个输入 ID 均属于 train，原题面与当前固定任务逐字一致：
33 个直接一致、4 个未评分列存在差异、15 个补题面版本、9 个按用户要求排除；
另保留 Xero001 不兼容结论，共 62 条阶段三账目。外部试点不是正式验证证书。

当前状态以 [阶段三采样策略](../catalog/phase3-current.json) 指向的不可变文件为准。
初始策略 `train-phase3-v00` 从原 17 题中移出 Cart Recovery、Trust Risk、
DQ Macro Enforcement、Xero001，剩 13 题。每 10 个正式复现任务完成审查后冻结一版，
最终不足 10 题一起冻结。待冻结的审查不会提前计入当前可采样数量。

15 个补题面版本的完整运行路径、补丁与内容 hash 记录于
[任务版本](../catalog/phase3-task-versions.json)。Hive001 和 ADE F1010-medium
明确标记为按 gold 做法修正的版本。生产任务保留原生 tests，不携带验证用提交、
观察脚本或模型请求配置。

Airbnb005 单独增加了按依赖 lock 构建的 Harbor 通信服务层。前后初始化业务文件、
业务 Python 与依赖已实际比较一致；原镜像和原绑定保留。
[运行环境版本](../catalog/phase3-environment-versions.json) 记录新的生产路径。

共享入口如下，使用已锁定的运行环境。所有批量输出必须放在 `$SCRATCH`。

```bash
export PYTHONPATH=src:scripts
export PYTHONDONTWRITEBYTECODE=1

# 准备指定作者提交；原生 verifier 不变，_v2 题面只安装到独立副本。
runtime/.venv/bin/python scripts/phase3_prepare.py \
  --base-batch <固定机械基线> --requests <已审阅请求.json> \
  --output <新的SCRATCH批次目录>

# 在 compute-node allocation 中启动已准备的无模型批次。
# 入口核验配置和工具 hash，记录实际节点/作业；运行与调度仍由 harbor run 负责。
runtime/.venv/bin/python scripts/launch_phase3_validation.py --batch <批次目录>

# 对账已结束批次并检查容器退出；不调度新任务。
runtime/.venv/bin/python scripts/phase3_account.py --review <root-control-v1>

# 逐题只读核验；原题保留旧 native14，修正版必须新增完整 native14。
runtime/.venv/bin/python scripts/phase3_review.py --overlay <audit-overlay.json>
runtime/.venv/bin/python scripts/phase3_publish.py --review <root-control-v1> stage \
  --audit <audit-overlay.json> --reconciliation <reconciliation.json> \
  --candidate-review <正式候选分数记录.json>
# 修正版另加 --native <native-overlay.json>。
# candidate-review 必须绑定两次正式运行的六个提交时点，以及审查过的 checker。
# 为早期 companion 补齐实际分数绑定，保留旧 record 和旧冻结版：
runtime/.venv/bin/python scripts/phase3_publish.py --review <root-control-v1> \
  amend-candidate --candidate-review <正式候选分数记录.json>

runtime/.venv/bin/python scripts/phase3_publish.py --review <root-control-v1> freeze
# 最后一批不足10题时加 --final。
runtime/.venv/bin/python scripts/phase3_report.py --review <root-control-v1> \
  --output <新的SCRATCH报告目录>
```

本次 root 控制目录为
`${ARTIFACT_ROOT}/validation/phase3-review-v1/root-control-v1`。
`inventory.json` 固定全部输入与原记录，`protected-files.json` 保护旧文件，
`state.json` 区分已冻结与待冻结记录，`execution-summary-v*.json` 保存运行对账。
报告同时导出 62 条 JSONL / CSV，逐类统计 audit 通过数与实际冻结池中的 train 数量。
`audit_validated` 表示 checker 通过声明的协议；它不覆盖正式候选的失败。
`formal_candidate_audit_failures` 单列这种情况，`eligible_after_review` 还要求
正式构建、原生成绩、环境与候选实际检查均符合准入规则。
分数记录直接引用原始 audit 的分数与 snapshot 字段；独立的表和 workflow 分数
需同时通过。不能省略分数记录，不能用 checker 的 validated 代替候选分数。
已封存记录需要补证时生成新 companion，旧文件和旧冻结版继续保留。

后续模型运行可从已冻结的 train 池准备配置。下面只写配置，不请求 endpoint，
不启动 Harbor。命令会拒绝 test、未入池任务、漂移的题面/镜像，以及包含验证专用
提交的任务目录；修正版和 Airbnb005 通信服务层使用实际已审查的生产路径。

```bash
runtime/.venv/bin/python scripts/prepare_phase3_run.py \
  --source ade --task ade__ade-bench-airbnb005 \
  --model "$MODEL" --endpoint "$ENDPOINT" --deployment "$DEPLOYMENT_ID" \
  --output-dir "$SCRATCH/dbt-bench-runs/<新的运行名>"
# 后续另行获准启动实验时，正式运行仍用 Harbor：
# runtime/.venv/bin/harbor run -c "$SCRATCH/dbt-bench-runs/<新的运行名>/config.yaml"
```

此入口限于本阶段验证过的 Apptainer 后端，保留原生预算、原生 verifier 和 Qwen
adapter。DEB 自带的 Harbor task name 保留，并在运行身份中映射到 catalog ID。
运行配置、模型部署标识、pool 版本、生产任务和镜像 hash 一并保存。生成配置不代表
已收集模型上下文或通过提交后的 audit。

重复构建各使用干净初始化；原题明确要求幂等时才额外执行同环境重复命令。
Apptainer 下层目录删除导致的安装失败保留为独立 trial，修订使用逐文件覆盖或新的
target/log 路径，不篡改原始 0 分，也不计作 SQL 语义失败。

2026-11-08 已完成本阶段审查，最终冻结版为
[train-phase3-v06](../catalog/pools/train-phase3-v06/manifest.json)。六次冻结分别新增
10、10、10、10、10、2 题，共 52/52 题；另有按用户要求排除的 9 题和维持原结论的
Xero001，共 62 条完整账目。全部 test 候选保持原值，278 个受保护的旧文件 hash 未变。

| 类别 | 完成审查 | audit checker 通过协议 | 正式候选实际通过 audit | train 入池 |
|---|---:|---:|---:|---:|
| 直接一致 | 33/33 | 32 | 31 | 31 |
| 原生未检查列存在差异 | 4/4 | 3 | 3 | 3 |
| 补题面版本 | 15/15 | 12 | 12 | 12 |
| 合计 | 52/52 | 47 | 46 | 46 |

实际可采样 train 为 **46 题：ADE 15、DEB 15、Spider 16**。全部正式候选均有两次
干净输入构建及原生 1 分，但准入仍要求经过验证的 audit 与候选自身同时通过。
两次构建的 agent 提交、verifier 前、verifier 后共 312 条实际 audit 分数为
276 个 1、24 个 0、12 个 null；null 不计通过。

未纳入的六个正式候选如下。原生分数、作者提交和先前证据全部保留。

| 任务 | 正式候选 audit | 原因 |
|---|---|---|
| DEB Channel Attribution | 0 | checker 已通过协议，但候选没有执行题面要求的 dbt deps 前置步骤。 |
| DEB HR Analytics | 0 | 原始 employment_type 的字面计数与候选的规范化解释不同，实际独立重算发现 72 个计数/派生值差异；缺少完整合法构建正例。 |
| DEB Cart Recovery 修正版 | 0 | 最终表缺少新增题面要求的 items_total；缺少完整合法构建正例。 |
| DEB Trust Risk 修正版 | 0 | review_month 为 DATE，题面指定 TIMESTAMP；一个 flag 的 CASE 使用 ELSE TRUE，违反指定 ELSE FALSE 的形式要求。业务布尔值相同不消除该要求。 |
| Spider quickbooks002 | null | 已执行构建和原始输入重算，但 5 条未付款记录的当前日期回退与 overdue 规则仍不能从题面确定。 |
| Spider recharge001 修正版 | null | 已执行重算与额外 NULL-refund 案例，退款 NULL 的纳入规则仍不能确定。 |

52 题的有效 native 检查账目为 573 pass、124 fail、31 gap；audit 为 705 pass、
9 fail、14 gap，均无 pending 检查。37 个原题保留旧 native14 并追加正式 reference
证据；15 个修正版有自己的 native14（168 pass、36 fail、6 gap）。全部 52 个原生
verifier 仍判 weak，补充 audit 不改变此结论。audit 为 47 validated、5 pending；
这里的 pending 是认证结论，所需调查已实际执行并留下 fail/gap，不是未执行模板。

最终两题新增的 21 个真实负例均先独立证明错误，再运行原生 verifier，全部原生为 0；
63 个阶段的目标字段检查确实检出对应错误，未用候选原有的接口问题充数。
全部阶段三 Harbor 执行累计 61 个结束批次、349 条终态 trial，包含保留的安装失败、
诊断和反例，不等同于 349 个成功正例。没有仍在运行的阶段三批次。

[最终报告](../artifacts/validation/phase3-review-v1/root-control-v1/reports/train-phase3-v06/summary.json)
和 [逐题表](../artifacts/validation/phase3-review-v1/root-control-v1/reports/train-phase3-v06/tasks.csv)
包含每题原生/audit 结论、未检查列差异、候选分数、生产版本和证据引用。
[交付状态](../validation/phase3-delivery-status.json) 单独保存本阶段结果，不覆盖旧交付状态。

公共代码 **440 项测试全部通过**，对应 199 个源文件的测试快照 hash 与最终代码一致。
[测试日志与快照](../artifacts/validation/phase3-review-v1/root-control-v1/tests-phase3-v5/source-manifest.json)
包含候选分数伪造、缺失/重复阶段、checker 替换、证据漂移、test 过滤及无模型运行限制的回归。
三来源及 Airbnb005 服务层共四份真实配置已通过锁定 Harbor 的 task/config loader；
只生成配置，没有启动模型实验。本阶段模型请求为 0，未执行新的真实 Qwen 模型 smoke；
locked Qwen shell 构建不冒充模型 smoke。

`phase3_review_complete=true`，`phase3_validation_complete=false`：五题仍缺少合法正例
或明确业务规则，不能认证。整体仓库的 `implementation_complete` 与
`validation_complete` 仍为 false；完整离线发布、镜像依赖重建闭包、各题正式采集声明和
逐 trial 的 trace/context 认证仍有先前记录的缺口。本阶段的 46 题是固定版本下的协议级
采样池，不代表已有 46 题可直接交给训练仓库的 rollout。
