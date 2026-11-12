# Apptainer timeout runtime v2

2026-11-12：用户批准仅做 CPU 实现与测试。GPU 验证、NKMHv3F 补跑、重新分批及提交仍待决定。本次未修改现有 `runtime/.venv`、`sources.lock.yaml`、原生 verifier、测量配置或历史结果。

## 行为

此前 Qwen adapter 的 `exec_as_agent()` 未传命令超时，Harbor Singularity backend 将整个 CLI 的本地 HTTP 请求限制为600秒；Trial 又把内层 `TimeoutError` 误标成原生4000秒预算耗尽。原生 verifier 的调用路径也存在同一隐藏限制。

新补丁仅改变四个 Harbor 文件：

- `environments/singularity/singularity.py`：HTTP read 不设隐藏时间上限，connect/write/pool 仍有有限超时。每条命令使用独立 exec ID；取消或通信故障后必须收到对应命令已停止的确认。确认失败则阻止后续 exec，避免对仍在变化的提交评分。
- `environments/singularity/server.py`：异步执行及按 exec ID 取消，保留命令 stdout/返回码。追踪子进程的 PID 与启动时间，并通过独立、继承的执行归属标记识别在两次采样之间重归属的子进程。先 TERM、必要时 KILL，确认停止后才应答。取消先于启动到达时保留 tombstone，避免稍后的请求又启动命令。
- `trial/trial.py`：只有本阶段 deadline 真正到期才生成 `AgentTimeoutError` / `VerifierTimeoutError`，内层超时保留其来源。agent/verifier 的原生预算数值不变。
- `models/trial/error_policy.py`：`ExecTransportError` / `ExecCleanupError` 为环境故障，reward-invalid。实验的零自动重试配置不变。

`timeout_sec` 显式命令上限在服务端执行；真正 phase 预算到期后允许有界停止确认和证据采集时间，不将其算成新的模型生成预算。取消确认不能联系服务端时，不宣称进程已经停止或提交已经安全采集。

这不是通用的恶意进程隔离方案：主动移除执行归属并快速 daemonize 的进程不属于已验证保证。容器 PID namespace 和原有整体 teardown 仍是环境退出边界。实际锁定 Qwen shell 的 pipe/PTY 路径单独验证。

## 独立安装与 CPU 验证

从已经安装并应用历史补丁的锁定 Harbor0.25.0 基础环境创建新副本：

```bash
PYTHONDONTWRITEBYTECODE=1 runtime/.venv/bin/python scripts/prepare_runtime_timeout_v1.py \
  --output "$SCRATCH/dbt-bench-storage/runtime/apptainer-timeout-v2-new"
```

输出必须是新的 SCRATCH 目录。准备器先核对四个 preimage hash，再复制完整 Harbor package 到新 venv；依赖使用原有只读基础环境，不联网安装/升级。新 venv 的 package 优先于共享依赖路径。manifest 记录新 runtime ID、逐文件 hash、基础解释器、依赖版本、旧来源锁和准备器。不得用旧的 `apply_runtime_patches.py` 对原 runtime 原地部署本补丁，也不得仅替换旧路径下的二进制。

本次最终候选：

`${STORAGE_ROOT}/runtime/apptainer-timeout-v2-candidate02/`

```bash
runtime_dir="$SCRATCH/dbt-bench-storage/runtime/apptainer-timeout-v2-candidate02"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src:scripts \
  "$runtime_dir/venv/bin/python" -m unittest tests.test_runtime_timeout_v2 -v

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src:scripts \
  "$runtime_dir/venv/bin/python" scripts/check_runtime_timeout_v2.py \
  --task tasks/deb__dbt-fix-repeat-purchase-cohort-revenue \
  --image artifacts/images/deb-base-env-v4.sif \
  --long-seconds 610 --output "$SCRATCH/timeout-v2-cpu-new"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src:scripts \
  "$runtime_dir/venv/bin/python" scripts/check_qwen_timeout_v2.py \
  --task tasks/deb__dbt-fix-repeat-purchase-cohort-revenue \
  --image artifacts/images/deb-base-env-v4.sif \
  --output "$SCRATCH/timeout-v2-qwen-shell-new"
```

这些测试仅运行 CPU 容器、合成命令与锁定 Qwen shell service，不调用模型，不作为 benchmark reward。第二项真实等待超过600秒，agent/verifier并行，约11分钟。生命周期回归另复用 `scripts/check_harbor_lifecycle.py`。结果与已知限制见对应 SCRATCH `result.json` 和本次 release report。

candidate01 曾通过常规取消，但额外反例发现快速重归属的孙进程可能在采样间隙遗漏；candidate02 增加继承执行归属并通过该反例。candidate01仅保留为开发调查，不可部署。它原 manifest 引用的初版 patch 内容保存在 `metadata.candidate01.json`；最终 patch 内容与candidate02 manifest 对应。

## repeat-purchase schema 归因

锁定 DuckDB1.2.2 镜像里原有 schema 实际拼写是 `ANALYTICS`。DuckDB SQL 名称匹配不区分大小写，但 dbt relation cache 的 approximate-match 判断区分大小写。M9LQhDE 和 LbbUR3M 选择小写 target schema，首次建表可成功，再次查找已存在的关系时发生冲突。

两次诊断仅把提交副本 `profiles.yml` 的 `schema: analytics` 改为 `schema: ANALYTICS`：原 SQL、输入、输出值/类型、构建命令均经 hash/逐行核对不变；两个连续 `dbt run` 均成功、诊断 native 都为1。参考解本来就通过 `+schema: ANALYTICS` 与保留 custom schema 的宏显式适配。原生测试在 rebuild 前 DROP 目标表，因而掩盖了这个重复构建错误。

因此应归因为**提交配置与任务环境的兼容问题**，不能声称任务环境没有诱因，也不是本次 timeout 补丁引入。它不是独立的业务 SQL 错误证据。原始评分、提交和审计记录不改；两份提交另外存在冻结契约的 BIGINT/INTEGER 类型失败，其中一份还有独立数值失败，仍足以判定 audit0。

## GPU 验证建议（未准备、未提交）

建议 `${ALLOCATION} / gh / 1节点 / 1 GPU / 5小时`，只做 **1次** repeat-purchase attempt，模型加载一次、单并发、agent4000s/verifier3000s不变。它可以在获批后直接作为 NKMHv3F 的 slot1 补跑，同时验证完整 Qwen Code + Qwen3.6-35B-A3B 路径，另外三次结果保留，不额外做不计数的模型尝试。

选择 gh 的原因：agent+verifier单次上限已达1h56m40s，模型启动上限20分钟后超过默认gh-dev2小时。加环境、安装、采集、健康检查重试，单次配置预算和约3h33m；加模型启动约3h53m，5小时留有收尾余量。这是预算加总，不是对所有文件系统/网络延迟的硬实时保证。

CPU 已验证真实超过600秒；GPU attempt 自然在600秒前完成也可以验证实际链路，不应人为拖慢/扩大预算来制造模型长轨迹。若 GPU attempt 遇外部故障，保留记录，不计入四次有效尝试。

真正提交前仍需获批建立新运行身份及一个 attempt 的恢复配置：现有 v2 批次 runner 按四次成员对账，不能原样用来补一个 slot。该工作不在本轮范围。本次不生成 sbatch/重提材料、不改 split、不冻结、不训练。
