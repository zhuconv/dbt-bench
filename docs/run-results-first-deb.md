# 本次运行与测试结果

日期：2026-11-04。输入仓库 `../training` 未修改。当前交付是可运行的辅助工具与完整候选账本，尚未达到计划的全部实现验收条件。

| 字段 | 实际值 |
|---|---|
| implementation_complete | **false** |
| validation_complete | **false** |
| 候选任务 | **135 = ADE 16 + Spider2 55 + DEB 64** |
| native validated / weak / broken / pending | **0 / 0 / 0 / 135** |
| audit validated / weak / broken / pending | **0 / 0 / 0 / 135** |
| 实际原生验证 trials（含旧环境失败） | **9，全部为 DEB 试点 oracle/nop** |
| 当前 env-v2 原生结果 | **oracle 1.0 / 1.0；nop 0.0 / 0.0** |
| 已完成逐题审阅的协议检查 | **0** |
| 待执行或待审阅的 native 协议项 | **1890 = 135 × 14** |
| 尚未认证真实构建见证的任务 | **135；DEB 已有实际参考运行，语义审阅未完成** |
| 可靠采样任务数 | **0** |
| 真实 Qwen smoke | **未执行** |
| 已启动 baseline / 大规模 rollout | **没有** |

机器状态：[delivery-status.json](../validation/delivery-status.json)。逐题：[validation-status.md](validation-status.md)、[ledger.jsonl](../validation/ledger.jsonl)。历史 weak/broken 问题没有未经复现就迁移为新结论。

最新 GPU 调试准备：已下载并核验 Qwen3.6-35B-A3B 的固定 revision，安装独立 vLLM 0.19.0 / PyTorch 2.10.0+cu128 环境，提供单节点两小时的 [gh-dev 脚本和操作说明](gh-dev-smoke.md)。CPU 上的模型配置与 tokenizer 检查成功。改用带 PTY 的普通 SSH 后，已从 login1 提交作业 **1122961**，当前 `PENDING (Priority)`，暂估 23:30（Chicago）启动；**GPU 实测尚未执行**。早先 BatchMode 认证失败没有证明用户必须介入。[成功提交证据](../validation/evidence/gh-dev-submission.json)。Qwen Code 0.26.5 npm tarball 已按 registry integrity 核验，原生安装的传递依赖仍待实际记录。新增执行事实回归后 **77 项测试全部通过**，见 [准备证据](../validation/evidence/gh-dev-preparation.json)、[测试记录](../validation/evidence/gh-smoke-preparation-tests.json)。下文 74 项测试是此前环境实施阶段的记录。

## 已执行并保留证据

- 根 `uv lock --offline`、`uv sync --locked --offline` 成功，使用 Python 3.13.11。辅助工具没有第三方依赖。
- 导入两份 v3 清单并核对 135 个去重 ID。旧提交与计划一致；发现并记录全部 10 个 v2 train → v3 test 的任务。重导入幂等，未知版本差异、重复或缺失 ID 有测试。
- **74 项测试全部通过，0 failure、0 error、0 skip**。[机器记录](../validation/evidence/implementation-tests.json)、[逐测试日志](../validation/evidence/implementation-tests.log) 包含实际源文件 hash。覆盖来源固定、稀疏获取、HTTP blob 完整性、快照/补丁、比较器、证据失效、proof gate、结果对账、split/训练过滤、trace/context、SIF 绑定、权限诊断，以及 root 兼容修复不吞掉原命令失败。
- 从旧源码实际提取比较函数并重现三个缺陷；新比较器得到独立预期结果。另有合成日历缺口和正常控制。[具体输入/输出](../validation/evidence/regressions.json)。这些都不是完整 benchmark 上的重验成绩。
- 修正 host doctor：除 PATH 外检查 TACC module 安装位置，实际发现 Apptainer 1.4.1。只记录工具位置、版本、Python、DNS 和 endpoint 配置是否存在，没有打印 API key。[当前 host.json](../validation/evidence/host.json)、[初次仅检查 PATH 的记录](../validation/evidence/host-initial-path-only.json)。
- 普通 compute-node 终端已通过网络、实时 Slurm 和两种实际 Apptainer 容器模式检查。节点 `i618-101`、allocation `1121806`；核对了全部日志 hash。[证据](../validation/evidence/execution-readiness.json)。这是 runtime fixture，未执行 benchmark 或 Harbor trial。
- 默认沙箱的工具进程仍被拒绝 socket/getsockopt。[历史诊断](../validation/evidence/execution-context-tool.json)。用户调整 permission mode 后，经授权的非沙箱工具调用已完成以下实际工作，不再需要用户手动运行下载命令。
- 生成并安装 Harbor 0.25.0 的 [完整 runtime 锁](../runtime/uv.lock)，核对固定 wheel hash。四份配置和三个试点均通过原生 schema 加载；确认 Qwen version pin 机制。真实原生 converter 的 error-only fixture 失败，错误文本丢失已复现。[检查记录](../validation/evidence/runtime-conformance-current.json)。
- 准备 ADE airbnb005、Spider workday002、DEB session-attribution 三个固定版本任务，重复准备成功。Git 改为稀疏获取；HF Git promisor 下载失败时，按固定 commit 的 Git OID 获取 raw 内容，LFS 数据再按 pointer SHA-256/大小校验。[来源准备记录](../validation/evidence/three-source-preparation.json)。
- 构建并绑定完整 DEB arm64 SIF。真实 Harbor 环境中复现并修复错误 Python PATH、重复 su root 和登录 shell 等待初始化问题；随后修复 dbt deps 删除只读层目录的 EIO。env-v2 初始 package 副本的 234 个文件在名称、内容和权限上逐项一致。[环境检查](../validation/evidence/harbor-deb-env-v2/result.json)、[复制等价证据](../validation/evidence/deb-env-v2-packages-equivalence.json)。
- 实际运行 **9 个原生 trial**：env-v1 的 1 次启动检查 nop，以及 2 oracle + 2 nop；env-v2 的 2 oracle + 2 nop。env-v1 两次 oracle 在 dbt deps 处失败，reward=0 保留；env-v2 两次 oracle 均 reward=1，两次 nop 均 reward=0，均无 Harbor exception。没有把这些结果升级为完整任务认证。[全部 trial 索引及日志](../validation/evidence/native-execution-trials/index.json)。
- 全量 ledger 仍覆盖 135 个候选；当前 DEB execution 关联 env-v2 四次运行，旧版本进入历史记录。catalog 报告无过期验证，训练候选=0。`report.py` 不带 jobs 参数时仅做 catalog 覆盖对账，其 observed_trials=0 不代表上述验证 trials 未执行；实际运行总量见 [执行里程碑](../validation/evidence/execution-milestone.json)。

## 干净目录验证

由于工作区 `.git` 是只读空挂载，不能声称做了实际 Git clone。改用一个新的临时导出目录，复制交付文件，**不带旧仓库、.venv、下载缓存、tasks、jobs 或 artifacts**。

最新干净导出中，根环境和 Harbor runtime 均以 `--locked --offline` 安装成功，runtime 补丁、validate 和 report 成功。74 项测试中 73 项通过，依赖旧源码的可选复现测试跳过。原生 converter 检查仍准确返回失败，不冒充通过。[当前证据](../validation/evidence/clean-export-current/summary.json)、[测试](../validation/evidence/clean-export-current/tests.json)、[converter 结果](../validation/evidence/clean-export-current/conformance.json)。此前 63 项测试版本的历史导出记录保留在 [clean-export.json](../validation/evidence/clean-export.json)。

## 尚未完成的实现验收

1. Qwen npm tarball integrity 已在 GPU smoke 准备中核实，实际 trial 的安装与传递依赖尚待记录。原生 Harbor bootstrap 仍动态安装部分 server 依赖；已记录实际安装输出，尚未将这些依赖全部固定在镜像中。
2. 原生 Qwen converter 的 error-only 文本丢失尚未修复；其他事件形态仍需完整 conformance 检查。环境补丁未修改 converter。
3. ADE/Spider 的完整 SIF 和真实运行仍未完成。ADE 原生 Dockerfile 包含 amd64 yq 下载，需要另行记录 arm64 兼容修复；不能直接运行后声称等价。DEB 已完成一个任务环境及参考/nop，但尚未运行实际 Qwen 工具上下文。
4. 135 个业务 contract 尚待审阅，另外 132 个 task snapshot 未准备。DEB 正例执行成功不等于业务语义、输出稳定性、等价答案、独立负例和评分覆盖均已验证。
5. 家族审查和新的 reviewed/frozen split 尚未完成。当前 split 保留历史分配，训练导出默认拒绝。
6. 不提供未经验证的完整请求采集器；缺模型真实上下文时严格 SFT-ready 保持 unknown/false。多 session/未知事件的 trace 继续保留并标记缺口。

当前执行条件已解决到可由工具直接运行 DEB 原生 Harbor 参考验证的程度。后续完成 ADE/Spider 镜像、Qwen converter 修复、逐题 contract 与语义验证；真实 Qwen smoke 仍需模型 endpoint。没有启动完整 baseline 或大规模 rollout。
