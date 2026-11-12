# 2026-11-12：用户批准独立 runtime 的 GPU 验证及重新分批

用户批准，由 Claude 转达：使用 CPU 验证通过的独立 Apptainer timeout runtime，仅补跑 repeat-purchase 外部中断的 NKMHv3F（slot1），另外三次原始提交与评分保留。新增 v3 runner/sbatch 显式绑定独立 runtime、恢复关系及实际5h/24h allocation，账户比较不区分大小写；不修改旧 runner、任务或封存身份。26项 runner 回归、真实材料 preflight/dry-run、Slurm test-only 均通过后，从 login1 提交唯一验证作业 **1139269**：`${ALLOCATION} / gh / 1节点 / 5小时`。提交后为 `PENDING / Priority`；[回执](../artifacts/difficulty/qwen36-v1/repeat-recovery-runtime-v3-20261112-v1/submission-v1/confirmed.json)与新配置记录完整，未额外安排模型 smoke。

64道未开始的题改为最多每批3题、每题4次、单并发、24小时，全部使用新 runtime，种子推导规则、原生预算与生成参数不变。将每次环境初始化、健康检查、Qwen安装、agent、采集、verifier的配置上限相加，并留20分钟模型启动及至少1小时额外收尾，35道DEB每题四次已约14.2小时，两道不能放入同一24h批次。因此生成 **35批（13批×3题、3批×2题、19批×1题）**，最长阶段预算加启动22:08:00、最小剩余1:52:00。[逐批任务和预算](../artifacts/difficulty/qwen36-v1/gh-rebatch-runtime-v3-20261112-v1/batch-plan.md)及提交命令均已准备，35批preflight/dry-run/test-only通过，**全部未提交**；须验证作业通过并获用户再次确认后才提交。旧`gh-resubmission-v2-20261112-v1`保留为历史、不再使用。预算加总不声称覆盖无限文件系统延迟。

用户同时批准在独立`dbt-bench-release`目录整理面向`zhuconv/dbt-bench`的发布版，个人配置与外部数据分离；活动封存文件不做清理或迁移。本轮不git init、不建远程、不push，许可证与跨账号结果导入工具待用户决定。测量和轨迹仅用于难度分层，不得用于训练；未抽签、未冻结新split。
