# Vista gh-dev 单题调试

此流程用于后续明确启动的单节点 GPU 实验，Slurm 上限两小时，模型固定 Qwen3.6-35B-A3B，使用 Harbor 内置 Qwen Code adapter。当前验证阶段不发模型请求。历史 ADE 作业1123746已 COMPLETED/0:0，总占用4分18秒，模型trial约50秒、native1/audit1；该条派生ATIF与客户端上下文已审查，冻结split选样后有1条训练候选。[trace范围](trace-fidelity.md)、[历史模型证据](../validation/evidence/customer-environment-review-v1/model-summary.json)。历史smoke不自动证明当前所有任务/镜像可用。

脚本默认仍指向DEB session-attribution，但其历史采集声明已过期；新运行需明确选择具有当前声明的任务。下面使用ana-eng005控制题。ana-eng003、session和workday002需先审查新版本声明，不能只刷新hash。

## 已准备的版本

- 模型 `Qwen/Qwen3.6-35B-A3B@995ad96eacd98c81ed38be0c5b274b04031597b0`，40 个文件逐项核对 Hub 的 Git blob / LFS SHA-256 和大小。完整 inventory 在 `artifacts/inference/model.json`。
- 推理环境：[inference/uv.lock](../inference/uv.lock)，Python 3.12、vLLM 0.19.0、PyTorch 2.10.0+cu128。aarch64 的 PyPI 默认 torch 是 CPU 版，因此锁中显式选择官方 CUDA 12.8 index；不修改旧训练环境。
- 配置：[服务参数](../inference/qwen3.6-35b-a3b.json)、[Qwen Code 参数](../inference/qwen-code-settings.json)。BF16、131072 context、单并发、thinking 开启、客户端每次生成最多 32768 tokens。原生 task 的 agent/verifier budget 保留；Slurm 两小时是另一个外部上限，可能先于完整 task budget 耗尽。
- Harbor 0.25.0 / Qwen Code 0.26.5，镜像从所选 task 的固定 backend binding 读取；具体 image hash 与环境版本记录在最终配置，旧 env-v3 只是历史身份。使用原生 `singularity` 后端及已登记的环境补丁。工具请求使用 `qwen3_coder` parser、推理内容使用 `qwen3` parser。

## 准备与提交

依赖下载、模型校验在已分配 compute node 上完成：

```bash
export UV_CACHE_DIR="$PWD/.cache/uv"
uv sync --project inference --locked --no-build
runtime/.venv/bin/python scripts/prefetch_smoke_model.py
```

TACC compute-node shell 禁用 `sbatch`，应在已经认证的 **login node** 上提交；不绕过包装器。登录节点只做提交和查询，模型与容器工作在分配到的 compute node 执行：

```bash
cd ${REPO_ROOT}
mkdir -p artifacts/slurm
sbatch --parsable --export=ALL,DBT_BENCH_SMOKE_TASK=ade__ade-bench-ana-eng005 \
  scripts/slurm/gh-dev-smoke.sbatch
```

可显式提供经过逐题审查的新声明；缺失或过期时在加载模型前停止。当前控制题声明检查命令：

```bash
runtime/.venv/bin/python scripts/submission_declarations.py \
  --task ade__ade-bench-ana-eng005 \
  --spec configs/submissions/ade__ade-bench-ana-eng005.json \
  --output "$SCRATCH/ana-eng005-declaration-new.json"
```

脚本使用 account `${ALLOCATION}`、partition `gh-dev`、1 node、1 task、`02:00:00`。没有指定节点，也没有使用 Vista 不支持的 `--gres`/`--gpus-per-task`。其他账户应在提交时显式覆盖 `--account`。

```bash
squeue --me
tail -f artifacts/slurm/dbt-qwen-smoke-JOBID.out
tail -f artifacts/slurm/JOBID/vllm.log
```

实际节点、GPU/driver、torch/CUDA、Slurm 限额、服务 argv、预先生成的 Harbor manifest 和步骤日志都写入 `artifacts/slurm/JOBID/`。原始模型任务在 `jobs/<source>-qwen-gh-dev-JOBID/`。Qwen Code 原生 `enableOpenAILogging` 开启，请求/响应保存到 trial 的 `agent/openai-requests/`，不引入请求代理；完整性仍需按实际日志验证。有需要时在已运行 allocation 内用 `srun --jobid JOBID --overlap -N1 -n1 ...` 检查，不在其他用户节点启动进程。

服务只监听节点的 `127.0.0.1:8000`。脚本先核对 `/v1/models`，再要求真实模型产生一个带 call ID 的工具调用，然后才调用 `harbor run -c .../harbor.yaml`。协议请求不是 benchmark trial；oracle/nop 也不计作 Qwen smoke。所有退出路径尝试关闭服务进程组，Slurm 时限到达也会结束该 job 的进程。

## 历史调试记录

以下为各次作业发生时的状态；安装适配已经接入，当前支持范围以 README、锁定版本和逐trial证据为准。历史失败、重试和旧trace缺口全部保留。

首次作业 **1122961** 已结束：2026-11-05 00:32:59 Chicago 在 c642-012 启动，GH200 120GB、CUDA 和 GPU 张量运算通过，随后 vLLM/Triton 因继承的 `nvc` 编译器拒绝参数而在模型推理前退出。服务进程现在显式使用 `/usr/bin/gcc` 和 `/usr/bin/g++`，并增加实际 GPU 上的 Triton/模型架构导入检查。[失败证据](../validation/evidence/gh-dev-1122961-startup-failure.json)、[编译器修复检查](../validation/evidence/gh-compiler-fix-tests-v1.json)。

**1123437** 于 2026-11-05 03:10:39 Chicago 在 c642-081 启动，两小时 allocation 到 05:10:39。初始 attempt 的首个生成请求因 PATH 缺 ninja 失败；attempt-1 因 FlashInfer 将 NVHPC 的 nvcc wrapper 目录误判为 CUDA 根目录，链接不到 libcudart 而失败；attempt-2 正确设置 toolkit 后仍复用了旧缓存的错误 build.ninja。三次均未进入 Harbor 模型任务。[Ninja 证据](../validation/evidence/gh-dev-1123437-ninja-failure.json)、[CUDA 链接证据](../validation/evidence/gh-dev-1123437-attempt-1-cuda-link-failure.json)、[缓存证据](../validation/evidence/gh-dev-1123437-attempt-2-stale-jit-failure.json)。

`inference/runtime-env.json` 显式固定 GNU 编译器及 CUDA 12.5 toolkit，PATH 包含 inference venv 的 ninja。FlashInfer 缓存按这些设置及 runtime lock 的 hash 隔离。启动前实际编译、动态链接并执行 SM90 kernel，再编译和加载模型使用的 GDN 扩展；这些通过后才加载模型。attempt-3 已通过真实协议生成，但 Harbor 拒绝配置中错误的 qwen-code agent 名称。模板改为原生 qwen-coder，并增加实际 AgentFactory 检查后，attempt-4 完成真实模型任务。PyTorch 的 CUDA 12.8 与本机 toolkit 12.5 分别记录，本次模型兼容性有实际执行证据。[配置故障](../validation/evidence/gh-dev-1123437-attempt-3-agent-name-failure.json)、[139 项回归测试](../validation/evidence/gh-agent-factory-tests-v1.json)。

失败时脚本保留已请求的 allocation 供调试，直到原时限或出现 `artifacts/slurm/JOBID/release-allocation` 标记。重试指定新的 `DBT_BENCH_SMOKE_ATTEMPT`，保留每次失败日志和独立 trial 路径。Vista 的 wrapper 也要求现有 allocation 的 srun 从 login node 发起，并显式提供 partition/account/time；本次成功步骤使用 `srun --jobid=1123437 --overlap -p gh-dev -A ${ALLOCATION} -t 01:10:00 -N1 -n1 -w c642-081 bash artifacts/slurm/1123437-attempt-4/debug-run.sh`。重试上限不得超出剩余 allocation 时间。本次已主动释放剩余额度，总占用 49 分 43 秒；父 batch 保留第一次失败退出码，Slurm 为 FAILED/1:0，后续 attempt-4 成功单独记录。

本次工具位于 gg compute node，`sbatch` 被站点包装器拒绝。先前使用 `BatchMode=yes` 的 SSH 在公钥被接受后仍未完成交互认证；改用带 PTY 的普通 `ssh user@example.invalid` 后成功，无需用户提供密码或验证码。之前判断必须由用户完成提交不准确。初次失败记录保留在 preparation evidence 中，以本次成功提交为当前状态。

实际模型权重占用约 64.69 GiB。Qwen 原生安装在 DEB 和 Spider 试点成功，ADE 的原生 xz 解压失败；固定 Node/Qwen bundle 已在 ADE 干净容器运行，但正式安装适配尚未接入。成功 smoke 保留 35 次 assistant 事件、49 个工具调用、40 份原生请求（包括 5 份原生 memory 子代理请求）。ATIF schema 合法，187 个系统事件尚未由当前保真审计器覆盖；请求到事件的完整映射尚未认证，不能声称 SFT-ready。原生 artifact 导出也没有保存提交源码/数据库快照，额外 audit 未执行；下一批采样前需明确收集这些产物。Qwen 内置 ripgrep 在该节点 page size 下失败并回退 grep，原始日志保留。

两小时适合这次单题调试，是否可完成更多任务应根据实际启动耗时和模型 trial 时长测量。本脚本不会启动 135 题 baseline。
