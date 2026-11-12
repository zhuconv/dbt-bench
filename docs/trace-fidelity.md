# Qwen trace 与实际请求审查

当前固定 Qwen Code 0.26.5、Node 22.23.4、Harbor 0.25.0。原始 session、原始
ATIF 和原生请求文件都保留；下面的审查不改写它们，也不产生新的 benchmark trial。

ADE job 1123746 已完成逐条请求核对：147 个 session 事件，19 次主模型生成、
4 次 memory 子代理生成、24 个主工具调用、6 个子代理工具调用。每次请求都有
原始 system prompt、实际 tools schema、完整 messages 与生成参数；全部调用 ID、
参数和工具结果与对应日志核验一致。主模型历史逐次与之前的实际输出比较，
没有通过拼接历史伪造请求。子代理使用自己实际记录的上下文，不能照抄主轨迹。

1 个 memory catalog system 事件逐字出现在 19 次主请求里，但原生 ATIF 没有保留它。
99 个 UI telemetry 和 3 个 attribution snapshot 也在原始 session 中保存、分类并索引。
因此此 trial 的 `model_context_capture=complete` 仅表示已核验的客户端实际请求级
上下文，**原生 ATIF 的 `content_fidelity` 仍为 incomplete**。这不认证训练样本、
服务端最终 token 序列或其他 trial。[审查](../validation/evidence/qwen-request-capture-review-v1.json)。

一次 read_file 的原生工具输出发生截断，实际交给模型的处理后文本与 session 完全
一致，截断事实单独记录；没有重建截断前文本。三次工具错误的实际错误文本也保留。
不能将原生工具本身的处理与后续 converter 丢失文本混为一谈。

## 原生记录器 conformance

`scripts/check_qwen_request_capture.mjs` 直接导入固定 npm 包的原生 generator、
HTTP SDK 和 logger，对一个仅监听 localhost 的合成 fixture 服务执行四项检查。
它不修改生产采样参数、生产请求或模型输出，也不是 Qwen benchmark smoke。

- 流式、非流式和 HTTP 错误：日志 request JSON 与服务端收到的 JSON 完全一致。
- system、工具定义、并行调用 ID、空结果、错误结果和 120,000 字符工具内容均保留。
- SDK retry：服务端实际收到两次请求，但原生日志只有一份 interaction。
  这个限制已复现，不能单凭 interaction 文件数量认证发生重试的轨迹。
- 日志里的 response 由解析后的模型输出重新组织；它不是原始 HTTP response 字节。

本轮执行的 1,391 个 Node/npm 包文件与生产安装 inventory 的逐文件 SHA-256 一致。
使用已安装的固定 Qwen npm 包时，可在有效 compute allocation 上运行：

```bash
node scripts/check_qwen_request_capture.mjs \
  /path/to/pinned/@qwen-code/qwen-code /path/to/new-capture-fixtures
```

Node 必须与锁定安装匹配。本仓库当前审查使用已从固定 bundle 提取并校验的实际路径：

```bash
env -u LD_PRELOAD artifacts/validation/qwen-trace-review-v1/runtime/node \
  scripts/check_qwen_request_capture.mjs \
  artifacts/validation/qwen-trace-review-v1/runtime/package \
  artifacts/validation/qwen-capture-new
```

大型 runtime 和运行证据不入 Git；上述路径需要已有本次保留的 artifacts。
[实际 fixture 结果](../artifacts/validation/qwen-trace-review-v1/capture-v2/summary.json)。

## 单条实际轨迹对账

```bash
runtime/.venv/bin/python scripts/audit_qwen_requests.py \
  --trial jobs/ade-qwen-gh-dev-1123746/ade__ade-bench-ana-eng003__6VdCbWL \
  --slurm-dir artifacts/slurm/1123746 \
  --capture-conformance artifacts/validation/qwen-trace-review-v1/capture-v2/summary.json \
  --output-dir artifacts/validation/qwen-request-reconciliation-new
```

这个入口目前支持固定版本、无压缩、成功结束、无 HTTP retry、文本工具的完整单
trial 服务生命周期。它检查事件父链、request lifecycle、response ID、主/子代理范围、
工具结果时序、主历史内容、memory catalog 和实际服务端请求数；未知事件、缺失结果、
压缩、失败的模型请求、其他子代理类型或请求计数不一致均明确失败，不补造数据。
ADE 服务端日志核对为 24 次 HTTP 200：23 次实际模型生成和独立的 1 次启动协议探测。

输出包含逐条对应索引、带原始引用的主请求 JSONL 和 context companion；完整原始
子代理请求仍单独引用。人工审查范围和原生记录器证据在
[capture review](../validation/evidence/qwen-request-capture-review-v1.json) 中固定。
报告继续独立检查 ATIF schema、ATIF 内容、split、task validation 和提交身份；
上下文核验通过不能绕过这些条件。

## 保留 memory 的派生 ATIF

离线转换器 `harbor-025-qwen-memory-preservation-v1` 已补充固定版本的 memory
catalog 表示：先用固定原生 converter 重建并核对原始 ATIF，再把已审查的 memory
snapshot 映射为实际 user context。派生文件附带全部原始事件、索引对应关系、原生
ATIF hash 和 converter 实现 hash；UI telemetry 与 attribution metadata 仍完整保存。
报告读取时重新计算转换并核对内容，不信任 manifest 中单独一个 pass 字段。

```bash
runtime/.venv/bin/python scripts/check_trace_conversion.py \
  --output-dir artifacts/validation/trace-conformance-new
runtime/.venv/bin/python scripts/reconvert_trace.py \
  --trial jobs/ade-qwen-gh-dev-1123746/ade__ade-bench-ana-eng003__6VdCbWL \
  --context artifacts/validation/qwen-trace-review-v1/ade-reconciliation-v3/context-binding.json \
  --output-dir artifacts/validation/ade-derived-new
```

输出目录必须是新目录。`--context` 是已审查的实际请求绑定；没有它时不能从 ATIF
补造请求。报告通过 companion 的 `trace_conversion` 引用派生 manifest，原生质量
仍单列为 `native_quality`，原始 session、trajectory 和 reward 均不改动。

18 项实际 converter fixture 和 4 项报告读取检查均通过，包括 error-only、空输出、
长输出、并行 call ID，以及伪造 pass、内容篡改、版本过期、绑定错误 trial 的拒绝。
ADE 1123746 的派生 ATIF 内容和客户端实际请求上下文均为 complete。下面链接的旧报告在 split 审查前有0条候选；随后按已冻结split重新选样得到1条候选、1条严格上下文候选，见[当前运行报告](run-results.md)。原生ATIF仍为incomplete，其他trace不自动通过。
[审查范围](../validation/evidence/trace-memory-conversion-review-v1.json)、
[报告](../reports/customer-qwen-1123746-derived-v1/summary.json)。

压缩、SDK 重试、异常终止和未知事件仍需补充支持。现有回归要求这些未覆盖情况
保持失败或 incomplete；一条完整轨迹不能认证其他实际 traces。

## Workday 真实轨迹的终止工具结果缺口

2026-11-05 的 Spider workday002 真实运行包含 42 次主请求和 5 次 memory 子代理请求。
请求审查发现 74 个工具调用中有 73 个结果进入了后续请求；最后一个 memory
`write_file` 仅有成功、输出长度及未截断的遥测，没有返回正文，也没有后续消费请求。
现有 auditor 因 `Unanswered/unrecorded tool result` 拒绝认证。

原始请求和 session 均保留。这个缺口不表示主请求正文丢失，也不改变 native reward；
当前 content fidelity 仍为 incomplete，context capture 未认证。没有从工具参数、
预期返回模板或源码补造结果。新增真实日志形状的回归要求“成功 + 长度”不能替代正文。
后续需要在固定 Qwen 版本中验证只记录的采集修复，现有 trial 不追补成完整。
[实际失败及 call ID](../artifacts/validation/workday-request-review-v1/summary.json)。
