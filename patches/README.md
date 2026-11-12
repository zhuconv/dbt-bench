# 显式补丁

当前没有对原生任务应用任何补丁。每个补丁目录必须包含 metadata.json、替换文件、可读 diff，以及真实回归证据。

metadata 字段：id、upstream_task_hash、type（environment/reference/verifier/task_variant）、reason、files、regression_evidence；verifier/task_variant 还需要 new_version。files 每项指定 path、before_sha256、after_sha256 和 replacement。应用前先核对所有文件，防止部分修改。原始任务快照在 `.cache/raw/` 保留。

默认 prepare 只允许 environment/reference 类型；verifier/task_variant 必须进入独立 audit 或新的 task/catalog 版本，不能覆盖原生评分身份。旧修复均列在 risk-register 中等待复现。
