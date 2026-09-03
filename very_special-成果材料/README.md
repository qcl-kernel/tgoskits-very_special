# 智能化工控混合系统成果材料

本目录记录一个在 RK3588 上运行的智能控制系统：AxVisor 同时承载一个
2-vCPU StarryOS Guest 和一个 Zephyr RTOS Guest，RK3588 NPU 只归
StarryOS 使用。公开材料只陈述已经实现并有源码、配置或运行证据支持的内容。

```text
                              RK3588
+--------------------------------------------------------------------+
| pCPU2                                                              |
|   StarryOS vCPU0：图像 -> RKNN/NPU -> 检测 -> 安全决策            |
|                                      |                             |
|                                      v                             |
| pCPU1：共享实时通信域                                               |
|   StarryOS vCPU1：VirtIO/T2N1 ---- CONTROL ----> Zephyr vCPU0      |
|         priority 89             <--- ACK/STATUS --- priority 90    |
|                   \________ AxVisor FP-RR ________/                 |
|                                                                    |
| RK3588 NPU：StarryOS 独占；它是加速器，不是 CPU                    |
+--------------------------------------------------------------------+
```

正式系统把 AI 的 CPU 工作隔离在 pCPU2，把通信 vCPU 与 Zephyr 放在
pCPU1。这样既减少推理对实时控制的直接干扰，又保留真实的 CONTROL/STATUS
共享核竞争。高竞争实验只解释调度机制在重压下的作用，无竞争实验用于说明
虚拟层开销；二者都不替代上述正式架构。

正式实板选择 RKNN/NPU，而不是让 ncnn 长时间占用 CPU：现有实板观测中，
CPU+ncnn 平均推理约 `1.622 s`，RKNN/NPU 约 `50.1 ms`，呈现约 `32.4x`
的量级差异。由于模型格式、输入和实验配置不完全相同，该数字只用于解释架构
选择，不作为严格受控加速比。ncnn 继续承担实板兼容参考和 QEMU 可移植验证。

| 层次 | 完成的核心工作 | 代表结果 |
| --- | --- | --- |
| Task 1 实时底座 | bounded FP-RR、唤醒抢占、IRQ-tail、CNTV 所有权、vIRQ retry、CPU 亲和性和锁边界 | 正式实板 P99 `0.621 -> 0.278 ms`，降低 55.3%；P99.9 `7.816 -> 0.748 ms` |
| Task 2 可靠通信 | 双 VirtIO-net、L2/UDP/IP、T2N1、ACK/超时/重传/去重/乱序处理、Safe/恢复 | 3×200 共 600/600 完整事务，0 重传、0 协议错误，31.834 transaction/s |
| Task 3 AI 控制 | 正式实板使用 RKNN/RK3588 NPU；实板和 QEMU 的 ncnn/CPU 路径分别作为可行性参考与可移植替身；包含安全校验、Stop 锁存、显式 Reset 和 Zephyr 状态回传 | 正式实板固定基线组 66.7% 正确、RKNN 组 100% |
| CARLA 综合仿真 | CARLA 0.9.16 五场景 A/B、车辆与行人安全距离、停车确认、Reset 与恢复行驶 | 固定基线 5/5 碰撞、YOLO 闭环 5/5 零碰撞；10 条视频使用同一 B站多 P 链接 |
| 平台与工程 | StarryOS、Zephyr、RT-Thread 兼容路径、QEMU、RK3588 实板、RAM-only 启动、一键入口、CSV/JSON/pcap/SHA-256 | 功能、故障注入、实板性能和证据重放均有独立入口 |

Task 1、Task 2、Task 3 共同闭合以下因果链，而不是三个孤立演示：

```text
真实图像 -> StarryOS/RKNN/NPU -> 安全决策
         -> T2N1 CONTROL -> Zephyr 动作
         <- ACK/STATUS <- 状态锁存与回传
```

## 阅读入口

- [00-总体架构与设计.md](00-总体架构与设计.md)：正式物理拓扑、资源所有权和设计理由。
- [01-Task1-实时调度设计与结果.md](01-Task1-实时调度设计与结果.md)：实时机制、原生 RTOS、QEMU 与实板数据。
- [02-Task2-双Guest通信设计与结果.md](02-Task2-双Guest通信设计与结果.md)：协议字段、事务时序、可靠性、隔离与吞吐量。
- [03-Task3-推理控制设计与结果.md](03-Task3-推理控制设计与结果.md)：AGV 场景、模型、安全状态机和端到端结果。
- [04-QEMU分任务验证.md](04-QEMU分任务验证.md)：Task 1–3 分开运行的功能和故障注入场景。
- [05-复现与配置指南.md](05-复现与配置指南.md)：唯一复现入口，集中说明命令、参数、成功标志和排障。
- [06-证据与演示索引.md](06-证据与演示索引.md)：原始证据位置、核验方法和演示组织方式。
- [board-fits/README.md](board-fits/README.md)：可直接 RAM 启动的正式实板 FIT、哈希和冻结拓扑。
- [07-任务要求与实现覆盖.md](07-任务要求与实现覆盖.md)：官网要求与设计、代码、配置和结果的完整对应。
- [08-上游贡献与StarryOS完善.md](08-上游贡献与StarryOS完善.md)：两个已合并上游修复，以及等待人工审查的 syscall 工作与状态边界。
- [09-CARLA仿真与视频演示.md](09-CARLA仿真与视频演示.md)：仿真软件选型、五场景 A/B 设计、数据叠加、结果和统一视频链接。
- [10-复现问题修复与复测.md](10-复现问题修复与复测.md)：已修复问题、精简复测命令和最终多 vCPU 拓扑说明。
- [11-完成细节与代码索引.md](11-完成细节与代码索引.md)：各项完成细节对应的代码位置、当前结果和完成度。

官网任务原文：<https://opencamp.cn/qcl/camp/OpenRace2026/stage/1>。

## 分任务入口

```bash
scripts/competition/task123.sh doctor
scripts/competition/task123.sh --list
scripts/competition/task123.sh suite task1
scripts/competition/task123.sh suite task2
scripts/competition/task123.sh suite task3
scripts/competition/task123.sh run task23-integrated
```

物理板入口仅使用 RAM-only `fastboot stage`；只有脚本打印
`BOARD_RESET_REQUIRED` 后才按一次物理 RST。
