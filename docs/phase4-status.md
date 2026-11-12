# 阶段四审查结果

2026-11-08，最终阶段四审查冻结于 `test-phase4-v04`。40道指定DEB test题
都有独立账目：37道完成正式复现及native14/audit14，另3道核对后排除。
两道native broken按用户要求不在这40题内。

| 类别 | 已审题数 | audit协议validated | 当前提交audit通过、可测量 |
|---|---:|---:|---:|
| agree | 27 | 14 | 13 |
| patched_pass | 10 | 7 | 7 |
| gold_error | 2 | 不认证 | 0 |
| degenerate_empty | 1 | 不认证 | 0 |
| 合计 | 40 | 21 | 20 |

37道正式审查题的全部检查已实际执行，均无pending检查；原生结论均保留为weak。
17份盲写提交未通过完整audit。checker协议认证与实际提交通过分别记录：
harbor-product-affinity的checker已认证，但当前提交缺少要求的依赖步骤。
另16题的audit总裁决仍为pending，不能认证为可靠任务；这与检查已执行后的fail/gap
不矛盾。因此 `phase4_review_complete=true`，`phase4_validation_complete=false`。

所有22个阶段四批次已结束，共121条实际trial，包含保留的安装失败与语义案例。
本阶段模型请求数为0。旧native/audit分数和证据未改；loyalty、RFM1、CLV的
审核修订以新checker、六个实际提交阶段分数和独立案例追加，旧版本仍可追溯。
CLV仅认证已审的Declining兜底解释，其他未审默认即使汇总自洽也保持unknown。

原 `train-phase3-v06` 的46道候选逐条保留。第二步的固定测量池为 **66题**：
ADE 15、Spider 16、DEB 35；每题4次，共264个独立attempt。该池保留历史split，
不是新train/test划分。难度轨迹禁止用于训练。

完整40题账目、原生与audit状态、各条证据及CSV：

```text
${ARTIFACT_ROOT}/validation/phase4-review-v1/pipeline/reports/test-phase4-v04/
${ARTIFACT_ROOT}/difficulty/qwen36-v1/population-v1.json
```

冻结入口见 [phase4-current](../catalog/phase4-current.json)，机器状态见
[phase4-delivery-status](../validation/phase4-delivery-status.json)。难度运行与预登记规则见
[难度测量说明](phase4-difficulty-plan.md)。完整测试套件已实际通过713项，证据为
难度目录下 `test-evidence-v4/full.json`，包含新增gh批次迁移与单服务运行回归。
这些实现测试和CPU fixture均不计为模型测量。

新split尚未抽签，须在测量完整后先汇报草案，取得用户明确确认才能冻结。
**DEB的train和test使用同一个零售数据库，评估熟悉数据上的新任务能力，
不代表对未见数据库的泛化。**
