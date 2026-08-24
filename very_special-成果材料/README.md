# very_special Task 1–3 成果材料

本目录是 `competition/task123-integration-20260824` 分支的评审导航。当前物理板混合拓扑只包含一个 2-vCPU StarryOS Linux Guest 和一个 Zephyr RTOS Guest，不是两个 Linux Guest：

- StarryOS vCPU0：图像预处理、RKNN/NPU 提交和后处理；
- StarryOS vCPU1：virtio-net、T2N1、CONTROL/STATUS 和通信 IRQ；
- Zephyr vCPU0：10 ms 周期任务、控制执行和 STATUS 回传；
- RK3588 NPU：独占分配给 StarryOS，不分配给 Zephyr 或 AxVisor host。

赛题原文与评分标准见 [OpenCamp：智能化工控中基于虚拟化的混合系统部署及联动实现](https://opencamp.cn/qcl/camp/OpenRace2026/stage/1)。本成果的主线不是把三个任务拼在一起，而是逐层闭合一条工业控制链路：

| 任务 | 要解决的问题 | 我们的核心做法 | 主要对比 |
| --- | --- | --- | --- |
| Task 1 | AI/通信 Guest 与 RTOS 共享 CPU 时，如何保持实时确定性 | AxVisor bounded FP-RR、优先级唤醒抢占、IRQ-tail 调度、定时器与 vIRQ 所有权修复、明确的 vCPU/pCPU 绑定 | 同负载 RR vs FP-RR；AxVisor Zephyr vs 原生 Zephyr |
| Task 2 | 两个 Guest 如何通过真正的 IP 网络可靠通信 | 独立 VirtIO-net endpoint + AxVisor L2 switch + UDP/IPv4 + 28 字节 T2N1 定长头 + stop-and-wait 可靠性 | 正常态 vs 丢 ACK、重试耗尽、断网、乱序、非法参数 |
| Task 3 | AI 输出如何安全地变成 RTOS 可执行控制 | StarryOS 内运行 ncnn/YOLO 或 RKNN/NPU，检测结果先校验和限幅，再经 T2N1 发给 Zephyr，最后由 STATUS 闭环 | 固定参数/固定感知 vs AI；合法模型输出 vs 非法模型输出 |

建议评审先读 [09-赛题要求与评分点对照.md](09-赛题要求与评分点对照.md)，再进入三个任务分篇。该对照表逐项列出官方 100 分评分细则、当前实现、证据和仍需补齐的内容，不以“有代码”等同于“已验收”。

## 状态定义

- **PASS**：指定范围的所有判据和验证器通过。
- **PARTIAL**：已有部分可用证据，但尚未完成当前分支的全部验收矩阵。
- **FAIL**：已观察到明确失败标志，不得用其他子项的 PASS 掩盖。
- **UNRESOLVED**：问题已被观察，但尚未修复或重验。

## 当前证据状态

| 证据层级 | 状态 | 声明边界 |
| --- | --- | --- |
| Task 2 无模型 QEMU 六场景 | **PASS** | 六场景整套通过，均有双 pcap 和 `TASK2_MODEL_ISOLATION_PASS`；证据是 dirty worktree |
| Task 3 QEMU suite | **PASS** | 真实 AArch64 ncnn/YOLO smoke 和 model-rejected Safe 路径均通过；证据是 dirty worktree |
| Task 2+3 QEMU 联合闭环 | **PASS** | 当前名称 `task23-integrated` 重跑通过，3 次 Guest 推理、CONTROL/ACK/STATUS 和双 pcap 都完整；证据是 dirty worktree |
| 旧 QEMU full suite | **PARTIAL / 历史** | 旧十项行为曾通过，但在 Task2/Task3 隔离之前生成，不能证明新的 Task2-only 语义 |
| 当前分支实板 Task 1 periodic | **PASS** | RR 3 轮 + FP-RR 3 轮，每轮 300 个连续周期样本，零 deadline miss；dirty worktree、RAM-only |
| 当前分支实板 Task 2/3 | **PARTIAL / FAIL** | FP-RR 三轮有 Task3 完成标志，但 RR 三轮均有 RKNN sequence error，且尚未完成 Task2/3 实板故障矩阵 |
| 实板 ext4 | **UNRESOLVED** | v6 六份启动日志均出现 `EUCLEAN: Structure needs cleaning` |
| clean-commit 最终证据 | **PARTIAL** | 当前新证据记录 HEAD、patch 和未跟踪文件哈希；提交后仍需 `ALLOW_DIRTY=0` 重跑 |

Task 2 新六场景证据位于 `tmp/competition-task123/current-fix-qemu/20260824T201803Z-suite-task2-2820310/`；Task 3 suite 位于 `tmp/competition-task123/current-fix-qemu/20260824T203457Z-suite-task3-2847136/`；联合闭环位于 `tmp/competition-task123/current-fix-qemu/20260824T204219Z-task23-integrated-2917173/`。实板 Task 1 证据位于 `tmp/competition-task123/board-current/final-v6-evidence/`。

## 本轮对比口径

- Task 1：在相同 Guest、负载、CPU 映射和周期任务下比较 RR 与 bounded FP-RR。
- Task 2：比较同一 T2N1 实现的正常态与五类故障/恢复行为；六个场景均不加载 YOLO，用于单独验收 Task 2。
- Task 3：比较合法真实模型输出与注入的非法模型输出，并验证 `task23-integrated` 推理到控制的联合闭环。

## 分篇文档

- [00-总体架构与设计.md](00-总体架构与设计.md)：物理混合拓扑、QEMU 替身拓扑和任务边界。
- [01-Task1-实时调度设计与结果.md](01-Task1-实时调度设计与结果.md)：RR 与 FP-RR 的 QEMU/实板 A/B。
- [02-Task2-双Guest通信设计与结果.md](02-Task2-双Guest通信设计与结果.md)：无模型通信、故障恢复和六场景新证据。
- [03-Task3-推理控制设计与结果.md](03-Task3-推理控制设计与结果.md)：CPU ncnn QEMU 与 RKNN/NPU 实板路径。
- [04-QEMU十场景完整验证.md](04-QEMU十场景完整验证.md)：项目自定义的十个 QEMU 行为场景和独立 CI gate；“十个”不是官网规定数量。
- [05-复现入口与配置审计.md](05-复现入口与配置审计.md)：一键任务、环境字段、互斥和硬编码边界。
- [06-CNTV定时器激活泄漏修复与实板验证.md](06-CNTV定时器激活泄漏修复与实板验证.md)：启动竞态根因、修复和实板边界。
- [07-证据索引与验收状态.md](07-证据索引与验收状态.md)：当前唯一状态源和已知失败索引。
- [08-提交与演示清单.md](08-提交与演示清单.md)：clean-commit 重跑、实板收口、打包和录屏清单。
- [09-赛题要求与评分点对照.md](09-赛题要求与评分点对照.md)：OpenCamp 任务要求、100 分评分项、加分项与当前成果逐项映射。

## 一键验收

在仓库根目录运行 `scripts/competition/task123.sh doctor`，然后使用 VS Code `Terminal -> Run Task` 中的 `Competition: Run Task 1`、`Task 2`、`Task 3` 或 `Full Validation`。联合正常闭环可单独运行：

```bash
scripts/competition/task123.sh run task23-integrated
```
