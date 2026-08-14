# 团队状态说明（OpenRace 2026）

本文件是团队的**第一版提交说明**。三个任务的工作分布在多条功能分支上，尚未合并回 `dev`。
查看本仓库时，请以本文件的分支指引为准。

## 1. 分支总览

| 分支 | 对应任务 | 内容 | 状态 |
| --- | --- | --- | --- |
| `openrace/realtime-virq-ab` | 任务一（实时化改造） | 实时 vIRQ A/B：per-vCPU 有界 dispatcher、定向唤醒、GIC 注入队列修复、HVC/SMC 异常 PC 修复、双 vCPU 启动修复、Zephyr 周期延迟基线 | 正确性修复与隔离性已验证；延迟优势在 QEMU/TCG 下未复现。证据见 `docs/my/openrace-realtime-progress.md` |
| `openrace/task2-net-clean` | 任务二（客户机间通信） | Linux 与 RTOS 双 Guest 双向 UDP/IP 链路、T2N1 可靠消息协议（CONTROL/STATUS/ACK/HEARTBEAT/ERROR）、QEMU 双向验证 | 已完成。证据见 `components/task2-net-protocol` 与 `book/design/task2-net-migration-checklist.md` |
| `openrace/task3-clean` | 任务三（AI 控制闭环） | AI 控制回路 + 模型训练管线（`components/task3-model`）、SIL 设计文档、基线与链路故障恢复证据 | 已完成。证据见 `book/design/task3-ai-design.md` 与 `results/task3/` |
| `openrace/task3-netb` | 任务三（内建 vSwitch 变体） | 双 Guest 链路移入 Axvisor 内建 virtio-net L2 交换（blackout/capture/port control）、第三次 AI 运行 | 已完成，两种 Zephyr 拓扑均可复现。证据见 `results/task3/switch/` |
| `openrace/task3-realtime-virq` | 任务一（重做，基于任务三基线） | 把任务一的实时 vIRQ 模型迁移到任务三基线上，定向 vCPU 唤醒走 aarch64 wait 路径 | 进行中，任务一重做的主线 |

## 2. 当前工作重点

1. **重做任务一**：初版 A/B 实验证明了正确性修复（HVC/SMC ELR、GIC 注入、park 路径）有效，但延迟优势在 QEMU 下未体现；现正基于最新 `dev` 与任务三基线重做任务一的实时改造，目标是形成可对比的正式基线。
2. **StarryOS 替换 Linux Guest**：当前网络与 AI 闭环的 Guest 是官方 Linux initramfs；正在替换为本仓库原生的 StarryOS，以形成更完整的工程闭环。
3. **物理板验证**：QEMU 验证完成后，将在真实板卡上复跑同一套实验，目前尚未开始。

## 3. 查看方式

```bash
# 查看某条分支的工作
git fetch origin
git checkout openrace/task3-realtime-virq   # 例如任务一重做主线

# 各分支的文档与证据
# 任务一：docs/my/ 、os/axvisor/src/realtime_probe.rs
# 任务二：components/task2-net-protocol/ 、book/design/task2-net-migration-checklist.md
# 任务三：components/task3-model/ 、book/design/task3-ai-design.md 、results/task3/
```
