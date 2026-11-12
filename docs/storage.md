# 本机数据存放位置

本文保留第一轮迁移记录；当前另有八个已完成验证目录迁到 SCRATCH，具体范围见末节。

2026-11-06 完成一次存储迁移。代码仓库和运行工作目录仍为：

```text
${REPO_ROOT}
```

大型模型、缓存、虚拟环境和 SIF 的实际数据位于：

```text
${STORAGE_ROOT}
```

原工作路径保留绝对符号链接，现有命令继续使用固定的镜像／模型路径。Scratch 目录是数据镜像，不是完整代码仓库；运行命令时仍从 Work 上的仓库进入。

| 原仓库路径 | 当前存放 |
|---|---|
| `.cache/huggingface` | Scratch，同名相对路径 |
| `.cache/uv` | Scratch，同名相对路径 |
| `.cache/apptainer` | Scratch，同名相对路径 |
| `.cache/triton`、`.cache/vllm`、`.cache/flashinfer-runtime` | Scratch，同名相对路径 |
| `.venv`、`runtime/.venv`、`inference/.venv` | Scratch，同名相对路径 |
| `artifacts/images/` 下的 86 个 SIF 文件 | Scratch，同名相对路径；其他镜像构建证据仍在 Work |
| `tasks/`、`jobs/`、`validation/` 和验证运行产物 | Work，保留原有路径校验边界 |

本次迁移 95.95 GiB 独立文件内容，涉及 139,198 个普通文件路径。复制保留权限和符号链接，并按原 inode 分组恢复硬链接。全部普通文件逐项核对 SHA-256 后，才删除 Work 副本；没有重下载或重新生成 benchmark 内容。

Work 用户配额的实际观察值从 528.02 GiB、1,014,145 个文件，降为 450.44 GiB、931,210 个文件。观察到释放 77.58 GiB 和 82,935 个文件额度；这是用户级配额快照，可能包含同时发生的其他目录活动。

兼容性检查核对了固定模型的 40 个文件、135 个任务的镜像绑定、三个 Python 环境入口、Harbor schema 导入、三来源 SIF 的原生 inspect 和容器 Python 启动。来源锁、catalog/split、冻结试点池、ledger 与交付状态文件的字节保持不变。本次没有启动任务解法、verifier 或模型请求，原验证目标继续暂停。

容器检查只执行了镜像中的 `python3 --version`。只读运行中，镜像初始化脚本尝试写入项目目录／临时目录时产生了错误，完整 stderr 已保留；这项检查仅证明迁移后的镜像可读取并执行 Python，不作为原生初始化或 benchmark 通过的证据。

后续模型与依赖缓存沿现有目录链接写入 Scratch。新增 SIF 版本也应先写入 Scratch，再在 Work 的预定镜像路径建立链接；保留对应构建证据，不覆盖已有固定版本。

详细记录：[迁移摘要](../artifacts/storage/work-to-scratch-20261106-v1/summary.json)、[完整路径清单](../artifacts/storage/work-to-scratch-20261106-v1/plan.json)、[文件校验](../artifacts/storage/work-to-scratch-20261106-v1/verification.json)、[运行兼容性检查](../artifacts/storage/work-to-scratch-20261106-v1/checks.json)。


## 第二轮：已完成的验证目录

2026-11-06 在 v11 冻结与最终一致性检查之后，将八个已完成的验证目录迁入同一个 SCRATCH 根目录。共 74,839 个普通文件、4.25 GiB；源/目标逐文件 SHA-256 相同，原 Work 路径逐项复核后才删除副本。保留目录符号链接，旧报告引用、Harbor 配置和结果发现路径继续有效。

迁移范围均位于 `artifacts/validation/`：

- `deb-session-server-reference-v1`
- `submission-capture-deb-v2`
- `intercom-input-build-pilot-v1`
- `intercom-output-cases-v1`
- `intercom-original-negatives-v3`
- `intercom-delivery-cases-v1`
- `intercom-event-fixture-pilot-v1`
- `intercom-event-fixture-cases-v1`

这八个目录实际位于 `${ARTIFACT_ROOT}/validation/`。其余任务、模型 rollout、canonical validation 与代码仍保留既有位置。

受影响的三条 Intercom 记录与 session-attribution 记录，其当前身份和全部引用复用检查通过。135题 ledger、v10/v11 冻结报告、模型成绩及原始 reward 的字节未变。此次仅迁移文件，没有新增 benchmark 或模型请求。

用户级 Work 配额观察值由 997,301 个文件降至 916,393 个文件，软限额 1,024,000；容量由 440.17 GiB 变为 435.75 GiB。该快照可能包含用户其他目录的同期活动，不将其全部归因于本次迁移。

[第二轮摘要](../artifacts/storage/validation-to-scratch-20261106-v2/summary.json)、[逐文件校验](../artifacts/storage/validation-to-scratch-20261106-v2/verification.json)、[身份与引用检查](../artifacts/storage/validation-to-scratch-20261106-v2/compatibility.json)。
