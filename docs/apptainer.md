# TACC / Apptainer 后端

Apptainer 可以运行 Docker/OCI 镜像。TACC 的命令默认可能不在 PATH，需要加载 module；这与未安装不同。[TACC 官方教程](https://containers-at-tacc.readthedocs.io/en/latest/singularity/01.singularity_basics.html)

本机实际为已分配的 Vista aarch64 计算节点，查到并执行成功：

```bash
module load tacc-apptainer/1.4.1
apptainer --version
# apptainer version 1.4.1-1.el9
command -v singularity
# /opt/apps/tacc-apptainer/1.4.1/bin/singularity
```

Harbor 0.25.0 原生 singularity 环境支持 Docker 镜像及预构建 SIF，通过容器内执行服务运行 agent/verifier。它从 Dockerfile 读取工作目录，但不会代替 Docker 执行整份构建。任务必须已经包含 COPY、RUN、依赖安装和 setup 的效果。[固定版本 adapter](https://github.com/harbor-framework/harbor/blob/v0.25.0/src/harbor/environments/singularity/singularity.py)

## 镜像准备

有两条路径：将按原生 Dockerfile 完整构建的 OCI 镜像转换成 SIF；或者用经过逐项审查的 Apptainer definition 重建，并验证初始化结果一致。Apptainer 官方支持 OCI→SIF 和 definition 构建，但 SIF 转换带生成元数据，需记录实际 SIF 文件 hash。[OCI 转换说明](https://apptainer.org/docs/user/1.4/docker_and_oci.html)、[definition 说明](https://apptainer.org/docs/user/1.4/definition_files.html)

```bash
# COMPLETE_TASK_IMAGE 是已包含整个任务环境的镜像，不能只是 FROM 基础镜像。
apptainer pull artifacts/images/task.sif 'docker://registry/complete-task-image@sha256:<digest>'
# 或使用已审查的定义文件，具体是否允许 fakeroot 由运行环境决定：
apptainer build --fakeroot artifacts/images/task.sif reviewed-task.def
```

此仓库不自动把任意 Dockerfile 翻译为 definition，也不把旧 training 的基础 `.def` 当完整任务镜像。旧 ADE definition 甚至显式下载 amd64 的 yq，而本机是 aarch64，不能直接照搬。

完整构建后保存 provenance JSON，包含以下实测字段：

| 字段 | 含义 |
|---|---|
| native_task_hash | catalog 中 prepared task 的 adapted_task_hash |
| dockerfile_sha256 | 对应原生 environment/Dockerfile 的 SHA-256 |
| image_sha256 | 完整 SIF 文件 SHA-256 |
| architecture | 本机为 aarch64；必须来自镜像构建/探测证据 |
| complete_native_build | 确认完整原生初始化已实现，true |
| build_kind | native_oci_image 或 reviewed_apptainer_definition |
| build_evidence | 构建日志、配方和初始化清单的 path/sha256 引用数组 |
| agent_visibility_evidence | 单个 path/sha256 引用，保存 agent 阶段隐藏材料路径的实际检查结果及范围 |

provenance 的字段不是替代实际检查的自我声明。大型材料保存在 artifacts/ 或持久挂载；路径引用以仓库根为基准。

```bash
uv run --locked python scripts/prepare.py sif \
  --task ade__ade-bench-airbnb005 \
  --image artifacts/images/task.sif \
  --provenance artifacts/images/task.provenance.json
```

该命令核对 hash、架构和证据，并调用真实 `apptainer inspect --json`。它保留 native task，在 `.cache/backend-tasks/apptainer/` 创建只改 `environment.docker_image` 的副本；绑定在 `catalog/backends/apptainer/`，含修改前后 hash。没有移植旧预算或 timeout 补丁；现有 root 执行兼容补丁来自新版本实际复现，见下文。

## 验证与运行

```bash
module load tacc-apptainer/1.4.1
export PATH="$PWD/runtime/.venv/bin:$PATH"
runtime/.venv/bin/python scripts/validate.py --backend apptainer --execute \
  --task ade__ade-bench-airbnb005

runtime/.venv/bin/python scripts/prepare.py config --backend apptainer \
  --source ade --task ade__ade-bench-airbnb005 \
  --model openai/your-model --endpoint http://your-model-host:8000/v1 \
  --deployment checkpoint-or-deployment-revision \
  --output artifacts/configs/ade-apptainer.yaml
runtime/.venv/bin/harbor run -c artifacts/configs/ade-apptainer.yaml
```

Docker 仍是默认参考配置；Apptainer 在 Harbor 配置里写作 `environment.type=singularity`。显式禁用 home/tmp/cwd/hostfs/bind-paths 的默认挂载，保留原生 shared/separate 配置，不新增 gold/solution 挂载。这不等于网络隔离，也不自动证明所有材料隐藏正确。

后端、SIF hash、派生 task hash 和证据 hash 都进入运行/验证身份。Docker 下的验证不会自动认证 Apptainer；镜像变化使旧证据失效。不同架构不能无条件互换镜像。资源限制要在实际 Slurm allocation 和 Harbor 运行记录中核查。

## 执行位置与本次真实探测

```bash
uv run --locked python scripts/doctor.py
.venv/bin/python scripts/check_execution.py --network --container --slurm \
  --output artifacts/execution/normal-shell/diagnosis.json
```

实际运行应在已经分配的 compute node 上进行。2026-11-04 已在 `i618-101.vista.tacc.utexas.edu`、allocation `1121806` 的普通终端执行上述检查，Slurm 确认 RUNNING，PyPI/GitHub/Hugging Face HTTPS 均返回 200，普通容器模式和 Harbor 所需的 fakeroot/writable-tmpfs/pid 模式均成功。

smoke 使用本机 `/bin/sh` 和必要动态库构建最小离线 rootfs，不访问镜像仓库，不执行 benchmark 或模型。原始诊断和日志已复制保存并核对 hash：[普通终端证据](../validation/evidence/execution-readiness.json)。这些是检查时该 allocation 的证据，不保证未来 allocation 或不同节点仍然可用。

同一节点上的工具进程受另一层限制，socket/getsockopt 返回 EPERM，容器因此报：

```text
FATAL: socket communication error: file file+net unix: getsockopt: operation not permitted
```

这项限制属于默认沙箱内的工具进程，不是普通终端或 TACC DNS 故障。新版 doctor 在 socket 操作被拒绝时不再把 DNS 查询结果解释为外部网络故障。没有自行修改安全设置或绕过该限制；后续非沙箱执行通过用户修改后的授权机制进行。最小容器检查不覆盖完整任务，DEB 的实际任务执行结果见下文。

工具进程失败日志仍保留：[诊断](../validation/evidence/execution-context-tool.json)、[容器日志](../validation/evidence/apptainer-smoke.json)。不要用普通终端的通过结果替代工具进程的失败结果，或反过来。

在通过检查的普通终端运行 `.venv/bin/python scripts/bootstrap_runtime.py`，安装固定 Harbor 并下载三个试点。所有步骤的日志在 `artifacts/bootstrap/`。之后审查原生 Dockerfile 和数据初始化，再构建并绑定完整 SIF。模型 endpoint 只在真正 Qwen smoke 时需要；当前容器 fixture 的成功不计作 benchmark 环境认证。

## 已完成的 DEB 原生 Harbor 集成

用户调整 permission mode 后，经授权的工具调用已经可以直接执行。Harbor 0.25.0 已安装，三个来源试点都通过原生 task 加载。DEB 基础镜像固定为 linux/arm64 manifest `sha256:44ba3dcf5690da2752aa8c0874ffe4c73b124ca0b61b8979b93fe2eb4643b5ca`；[当前 v2 definition](../environments/apptainer/deb-session-attribution-env-v2.def) 执行任务 Dockerfile 的完整 pip 安装步骤，在每次启动时运行原生 DuckDB entrypoint，并将初始 dbt packages 放入可写层，解决 dbt deps 删除只读层目录的 EIO。234 个 package 文件的名称、内容和权限逐项一致。

实测发现并修复了 server PATH 选中错误 Python、冗余 `su root` 无法设置 groups、登录 shell 等待入口 marker 三个问题。Harbor 补丁在 [metadata](../patches/harbor-025-apptainer-exec-v1/metadata.json) 和 [diff](../patches/harbor-025-apptainer-exec-v1/change.patch)，用 `runtime/.venv/bin/python scripts/apply_runtime_patches.py` 应用。不是新 agent loop。

[修复前检查](../validation/evidence/harbor-deb-native/result.json) 和 [当前 v2 检查](../validation/evidence/harbor-deb-env-v2/result.json) 保留各自结果。修复后三种命令上下文均使用 `/usr/local/bin/python3`，能导入 DuckDB 1.2.2 并读取原生库，已检查的隐藏路径不可见。v2 两次独立 oracle 原生 reward=1，两次 nop=0；旧环境失败全部保留。这不是实际 Qwen 会话，也不是完整 verifier 语义认证。原生 bootstrap 仍会动态安装部分 server 依赖；实际安装日志保留，彻底固定这些运行期依赖尚未完成。

可重跑实际环境集成检查，输出目录必须是新目录：

```bash
runtime/.venv/bin/python scripts/check_harbor_apptainer.py \
  --task tasks/deb__dbt-session-attribution \
  --image artifacts/images/deb-session-attribution-env-v2.sif \
  --db /app/database/retail.duckdb \
  --output-dir artifacts/execution/deb-new-check
```
