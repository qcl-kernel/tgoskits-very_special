# Task 1：实时调度设计与结果

## 任务思路：先隔离重负载，再保证共享 CPU 上的优先级

赛题要求不仅是“启动一个 RTOS”，而是改造 AxVisor 的关键实时路径、启动不少于 2 个 vCPU 的多核 Linux/Starry Guest，并同时提供改造前后、空载/压力和原生 RTOS 基线。我们的判断是：AI 推理是否快，和 RTOS 能否按时被调度，是两个不同问题。因此采用两层设计：

1. **空间隔离**：StarryOS vCPU0 固定在 pCPU2，负责图像预处理、RKNN/NPU 提交和后处理；把重计算移出实时共享核。
2. **时间隔离**：StarryOS 通信 vCPU1 与 Zephyr vCPU0 共享 pCPU1；Zephyr 使用优先级 90，StarryOS 使用 89，在 bounded FP-RR 下允许高优先级唤醒抢占，同时给低优先级通信 Guest 保留有界服务。

这比“给 RTOS 独占一颗核”更接近赛题的混合负载：实时 Guest 和通信 Guest 真实共享处理器，AI 主路又不会直接堵住 RTOS。

## 对 AxVisor 的实质改造

- 增加可选的 bounded FP-RR：更高优先级 runnable vCPU 可在唤醒和 IRQ-tail 抢占；同优先级继续 RR；低优先级 vCPU 获得有界服务，避免严格优先级饥饿。
- 明确 vCPU/pCPU 亲和性与 `host_sched_priority`，让配置而不是启动偶然性决定实时拓扑。
- 修复 AArch64 CNTV 激活所有权泄漏：若 Guest virtual timer 未 asserted，已 acknowledge 的 host CNTV token 立即退休，避免共享 pCPU 停止产生后续 timer IRQ。
- 为 vIRQ 使用有界队列和 retry slot，处理 GIC LR 竞争，不用无界分配或静默丢边沿。
- 把唤醒、通知、IPI 和 host IRQ 完成动作移出宽锁临界区，限制 IRQ/调度关键路径的阻塞来源。

相关机制详见 [`docs/design/task1-realtime-design.md`](../docs/design/task1-realtime-design.md)、[`docs/design/axvisor-aarch64-generic-timer.md`](../docs/design/axvisor-aarch64-generic-timer.md) 和 [06-CNTV定时器激活泄漏修复与实板验证.md](06-CNTV定时器激活泄漏修复与实板验证.md)。

## 多核 Guest 与资源配置

| 对象 | 配置 | 设计理由 |
| --- | --- | --- |
| StarryOS Guest | 2 vCPU，`phys_cpu_ids=[0x200,0x100]`，priority 89 | vCPU0/pCPU2 承担 AI；vCPU1/pCPU1 承担通信 |
| StarryOS 内存 | `0x4000_0000..0x7fff_ffff`，1 GiB | 与 Zephyr 地址空间分离 |
| StarryOS 设备 | RK3588 NPU、eMMC、独立 VirtIO-net | NPU 及依赖只归 StarryOS |
| Zephyr Guest | 1 vCPU，`phys_cpu_ids=[0x100]`，priority 90 | 与通信 vCPU 共享 pCPU1并获得更高实时优先级 |
| Zephyr 内存 | `0xA000_0000..0xBfff_ffff`，512 MiB | 独立 stage-2 区间 |
| Zephyr 设备 | 独立 PL011、VirtIO-net | 控制与通信，不映射 NPU |

模板分别位于 [`starry.toml.in`](../scripts/board/task123-zephyr/starry.toml.in) 和 [`zephyr.toml.in`](../scripts/board/task123-zephyr/zephyr.toml.in)。

## 设计

物理板上，StarryOS vCPU0 在 pCPU2 执行图像、RKNN/NPU 和后处理；StarryOS vCPU1 与 Zephyr vCPU0 共享 pCPU1。简单 RR 不能表达 RTOS 优先级，无界严格优先级又可能饿死较低优先级的 StarryOS 通信 vCPU。

bounded FP-RR 允许更高优先级唤醒抢占，同优先级 runnable 工作以 RR 轮转，并为低优先级 Guest 保留有界服务。中断尾调度按优先级决策；每 vCPU 使用有界 vIRQ 队列和 retry slot，防止 LR 竞争下丢边沿。

## QEMU RR/FP-RR A/B：PASS（dirty）

这是同一代码和同一负载下的调度策略 A/B，不是旧代码与新代码对比。两臂使用同一 StarryOS ncnn/YOLO 负载、Zephyr 二进制、10 ms/300 样本探针和 CPU 映射，只改 AxVisor 调度 feature：RR 3 次，bounded FP-RR 3 次。

| 每臂三次中位指标 | RR | bounded FP-RR | 变化 |
| --- | ---: | ---: | ---: |
| P99 唤醒抖动 | 14.291 ms | 1.091 ms | 13.101x / 降低 92.37% |
| P99.9 唤醒抖动 | 14.662 ms | 2.587 ms | 降低 82.35% |
| 最大唤醒抖动 | 14.662 ms | 2.587 ms | 降低 82.35% |
| 超过 1 ms 的样本 | 299/300 | 6/300 | 降低 97.99% |

六次都执行 YOLO，用时 `20.528–22.190 s`。FP-RR 三次的 `lower_priority_services` 为 `116/127/117`，证明推理 Guest 未被饿死。

证据：[`comparison.md`](../tmp/competition-task123/evidence-worktree/20260824T175618Z-suite-full-2684544/task1-scheduler-ab/comparison.md)、[`verify.log`](../tmp/competition-task123/evidence-worktree/20260824T175618Z-suite-full-2684544/task1-scheduler-ab/verify.log) 和 `rr-01..03/`、`fp-rr-01..03/`。该证据是 dirty worktree，而且所在的旧 full suite 生成于 Task2/Task3 隔离之前；Task1 A/B 本身的判据可审计，但不能据此宣称当前十场景 clean full PASS。

## 当前 v6 实板 A/B：Task1 PASS（dirty）

CNTV 激活泄漏修复后，最终 CPU 角色映射下完成 RR 3 轮和 FP-RR 3 轮 RAM-only 采样。StarryOS 主 vCPU0 位于独占 pCPU2，通信 vCPU1 与高优先级 Zephyr 共享 pCPU1。

| 每臂三次中位指标 | RR | FP-RR | 变化 |
| --- | ---: | ---: | ---: |
| P99 唤醒抖动 | 0.386 ms | 0.273 ms | 降低 29.284% |
| P99.9 唤醒抖动 | 0.573 ms | 0.566 ms | 降低 1.35% |
| 最大唤醒抖动 | 0.573 ms | 0.566 ms | 降低 1.35% |
| deadline miss | 0 | 0 | 持平 |

六轮每轮都完成 300 个连续周期样本。原始日志、分析 CSV/MD、图和哈希清单位于 [`final-v6-evidence`](../tmp/competition-task123/board-current/final-v6-evidence/)。

### 不得被 Task1 PASS 掩盖的同日志异常

- RR 三份 boot log 均出现 `TASK2_ERROR=RKNN event sequence does not match the frozen scene`，所以这三轮不是 Task2/3 PASS。
- FP-RR 三轮出现 `TASK3_EXPERIMENT_COMPLETE events=12 statuses=12`，但仍不等于已完成 Task2/3 实板故障矩阵。
- v6 六份 boot log 都出现 ext4 `EUCLEAN`，该问题是 **UNRESOLVED**。

因此当前实板结论严格限定为“Task1 periodic RR/FP-RR 各3轮 PASS”。

## 原生 RTOS 基线：Zephyr 不经过 AxVisor

按照赛题“与裸机或原生 RTOS 对比”的要求，我们在同一 QEMU AArch64 平台直接启动 Zephyr `qemu_cortex_a53`，运行同类 10 ms 周期任务；该路径没有 AxVisor，也没有 Linux/Starry Guest。

| 300 样本指标 | 原生 Zephyr/QEMU | AxVisor `stress-rt` | 虚拟化侧相对增量 |
| --- | ---: | ---: | ---: |
| 平均抖动 | 405.783 us | 614.024 us | +51.32% |
| P99 抖动 | 599.056 us | 810.224 us | +35.25% |
| 最大抖动 | 836.048 us | 882.672 us | +5.58% |

原始日志、ELF/BIN、QEMU 命令、CSV、manifest 和哈希位于 [`results/task1/native-zephyr/`](../results/task1/native-zephyr/)。两组都使用相同周期任务和统计脚本，但原生侧没有共驻 Linux stress，因此它是“无虚拟化下界”而不是严格同负载 A/B；严格的调度策略对比仍以同拓扑 RR/FP-RR 为准。

## 迁移前实板证据

[`results/atk-dlrk3588-npu-hybrid-20260824/README.md`](../results/atk-dlrk3588-npu-hybrid-20260824/README.md) 记录迁移前 RK3588 30,000 样本矩阵，压力下 P99 从 `9.656 ms` 降至 `4.091 ms`。[`results/atk-dlrk3588-task1-yolo-sustained-20260823-202227/README.md`](../results/atk-dlrk3588-task1-yolo-sustained-20260823-202227/README.md) 另记录 3+3 长运行及调度取舍。两者均是历史证据，不是当前分支新鲜复测。

完整四格对比如下，四格都使用同一 Zephyr 二进制和每格 30,000 个连续 10 ms 样本：

| 调度器 | 负载 | mean | P99 | max | >10 ms deadline miss |
| --- | --- | ---: | ---: | ---: | ---: |
| RR | idle | 1.128 ms | 11.190 ms | 52.311 ms | 522 |
| FP-RR | idle | 0.644 ms | 3.914 ms | 10.379 ms | 3 |
| RR | RKNN/T2N1 stress | 1.497 ms | 9.656 ms | 17.803 ms | 247 |
| FP-RR | RKNN/T2N1 stress | 0.500 ms | 4.091 ms | 7.252 ms | 0 |

## 官方 30 分评分点对应

| 官方细则 | 当前对应内容 | 状态 |
| --- | --- | --- |
| 目标和关键路径分析（4） | AI/通信负载、唤醒、IRQ-tail、timer/vIRQ、锁边界均有设计说明 | 已覆盖 |
| AxVisor 关键机制实质改造（8） | bounded FP-RR、优先级抢占、CNTV 所有权、vIRQ/LR、亲和性 | 已覆盖 |
| 多核 Linux/Starry Guest（4） | 2-vCPU StarryOS、1 GiB 内存、NPU/eMMC/VirtIO 与中断归属 | 已覆盖 |
| 改造前后数据（5） | 同拓扑 RR/FP-RR QEMU 3+3 与实板 3+3 | 已覆盖；最终仍需 clean 重跑 |
| idle/stress 对比（4） | 迁移前实板四格 30,000 样本 | 历史证据，需当前分支复测以收口 |
| 原生 RTOS 基线（5） | 原生 Zephyr/QEMU 与 AxVisor Zephyr 使用同类周期任务 | 已覆盖方法和数据；原生侧无共驻 stress，需说明平台差异 |
