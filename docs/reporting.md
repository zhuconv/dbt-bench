# 结果对账与训练候选

原始 Harbor trial/config/result/agent/qwen-sessions/trajectory/verifier 日志应保留。report 对原始目录只读，输出 companion 索引；不重写 reward 或异常。未识别的字段仍在原始 artifact 中，其路径和 hash 进入索引。

## Experiment 与 bindings

`prepare_model_run.py` 在运行前生成最终 experiment manifest，包括明确的 job name、完整配置、task hash、来源锁、catalog/split/validation hash 和预定 task/attempt（从 1 开始）。旧 `prepare.py config` 的 job name/hash 顺序缺陷已复现，原生成文件和历史 manifest 保留；新实验使用 README 中的正式入口。该准备命令不会启动模型，也不会自动生成运行后的 bindings.jsonl。变更 endpoint/checkpoint/budget/retry/task/checker 时要生成新的身份。实际服务端参数可未知，但必须保留 unknown；最终身份审查需补充可获取的运行证据。

当前 Harbor result 不提供本仓库需要的全部 attempt/基础设施 retry 关联。不要从 trial 目录名称、文件排序或失败字符串猜编号。运行后创建每 trial 一行的 bindings JSONL：

```json
{
  "trial_id": "Harbor result.id",
  "task_key": "ade__ade-bench-airbnb005",
  "attempt": 1,
  "retry_of": null,
  "retry_index": 0,
  "experiment_id": "运行前manifest中的ID",
  "result_sha256": "原始result.json的SHA256",
  "adapted_task_hash": "本仓库任务树hash",
  "resolved_config_evidence": {"path": "...", "sha256": "..."},
  "environment": {"valid": true, "evidence": {"path": "...", "sha256": "..."}},
  "submission": {"path": ".../submission-capture.json", "sha256": "..."},
  "audit": null,
  "cause_annotation": null
}
```

上面仅说明数据格式。有效性必须由实际证据支持。resolved_config_evidence 是运行身份审查记录，至少绑定 experiment_id、task_hash、trial_id，并应说明 Harbor resolved config、镜像、依赖、模型部署和参数的核对过程。它不是虚构一个三字段文件就能得到的运行证明。

retry_of 指向同 task/attempt 的前一个 trial ID，新的模型 attempt 用新 attempt 编号；report 会检查重复、未知、缺失、错误关联和循环。缺绑定的 trial 保留原始结果并报错，不按目录名猜测；未出现的预定 attempt 也保留为 missing。

## 原生与 audit

Harbor native reward map 完整保留。reward-validity 优先读取原生字段；若未序列化，使用安装的固定版本 TrialResult.reward_valid，不能计算时为 unknown。优先使用当前、经审查且 hash 匹配的逐题 contract 中的 native success 定义；未审查来源仍保留 unknown。一题的成功定义不会认证整来源，也不把任意正数当成功。

submission 指向完整收集回执 JSON：逐文件清单和 snapshot_hash 必须与原生 Harbor 收集声明、实际 artifacts 及生成时固定的 submission spec 一致。文件缺失或被改写时，即使旧回执写着 complete 也不能通过。

audit 的 result 引用必须绑定这份回执 hash 和已验证 checker_version，结果包含 reward 与 success；缺少运行结果就是 null。同一提交 hash 不相同的 audit 不能给原 trial 认证。会构建或写库的 checker 使用已声明的干净重建流程，不以修改后的提交覆盖原始提交。

四类根因是 companion 注释，需 category=1/2/3/4、reason、evidence 和适当置信度。未知保持 null。agent 重装依赖造成的失败保留为原 trial 的行为/环境结果；诊断恢复重验单列，不覆盖原 reward。timeout 只说明可观察中止机制，不自动说明任务本来可解。

## Trace 与候选

审计分别记录 schema_valid、content_fidelity 和 model_context_capture。原生 ATIF 保留不改写；独立版本的派生转换先重现固定原生 converter，再保留经审查的 memory context 和原始事件映射。报告重算并核对转换身份与内容；未知事件、未覆盖的复杂结果或丢失内容仍标记 incomplete，schema 合法不代表内容保真。实际范围和命令见 [trace-fidelity.md](trace-fidelity.md)。

完整上下文需要经独立 conformance 验证的实际请求记录，以及逐 trial 的事件、工具调用、主/子代理请求对应关系。已审查的 ADE trial 使用固定 Qwen 原生记录器和服务端请求数量对账。SDK 内部 retry 漏记已被复现，不能把该 trial 的认证推广到重试、压缩或其他未审查轨迹。真实 system/tools 缺失时保持 unknown，不以空工具或历史消息拼接补造。

候选策略版本为 `training-candidates-v5`：reviewed_frozen 的 train split、可信运行身份、有效环境、有效原生成功、当前且可信的验证结论、经核对的完整提交、ATIF 结构和内容完整；native weak 还需 validated audit 对同一提交通过。严格 SFT-ready 再要求完整模型上下文。策略返回每条不合格原因，不删失败或 test traces。

当前 split 为 `v2-family-reviewed-2026-11-05`，93 train / 42 test，家族审查与完整语义验证分别记录。报告中的 `split` 是当前选样分配；`split_provenance.generation` 保留原实验 catalog/split hash，`selection` 记录本次选样版本和审查，`historical_exposure` 保留旧 train/test 记录。summary 同样分别列出生成身份与选样身份。把旧 trial 按新 split 选样不表示它在新 split 上重新运行，更不能消除旧训练暴露。实际可用数量见 [运行结果](run-results.md)及其绑定的报告；输出文件名称不改变资格。
