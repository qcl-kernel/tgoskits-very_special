# Task 1 原生 Zephyr vs AxVisor Zephyr 重跑对比

## 对比身份

```text
原生 RTOS：QEMU virt -> Zephyr
虚拟化 RR：QEMU virt -> AxVisor RR -> Zephyr Guest
虚拟化 FP-RR：QEMU virt -> AxVisor FP-RR -> Zephyr Guest
```

这里的“原生”表示 Zephyr 直接运行在 QEMU 提供的 CPU、GIC 和定时器上，中间没有
AxVisor 或其他操作系统。它属于等价平台上的原生 RTOS，不等同于 RK3588 实体板裸机。

三臂均使用 Zephyr commit
`dccb09599635bdff17633fa7e9dab014b91dce90`、`qemu_cortex_a53` 应用配置、同一周期
采样源码、10 ms 周期、6000 个样本、无额外压力。QEMU CPU 型号均为
`cortex-a72`。虚拟化侧为运行 AxVisor 使用 2 个 pCPU，Zephyr 的单个 vCPU 固定在
pCPU1，pCPU0 承担 hypervisor housekeeping；这是引入虚拟层所必需的平台差异。

原生侧不存在 AxVisor host scheduler，周期线程由 Zephyr 自己调度。两个虚拟化侧
Zephyr 内部调度完全相同；RR/FP-RR 只表示 AxVisor 调度 Zephyr vCPU 的机制。RR
不依据 host priority 选择 vCPU；FP-RR 先按固定优先级选择，同一优先级内再轮转。

## 本轮结果

| 指标 | 原生 Zephyr | AxVisor RR + Zephyr | AxVisor FP-RR + Zephyr |
|---|---:|---:|---:|
| 完整样本 | 6000/6000 | 6000/6000 | 6000/6000 |
| Mean jitter | 0.178 ms | 2.641 ms | 2.593 ms |
| P99 jitter | 0.422 ms | 3.387 ms | 3.569 ms |
| P99.9 jitter | 0.474 ms | 3.876 ms | 4.016 ms |
| Max jitter | 1.748 ms | 4.030 ms | 4.255 ms |
| 超过 1 ms | 1/6000 | 5938/6000 | 5897/6000 |

“超过 1 ms”采用 `deadline_tolerance_ns=1000000` 的容忍口径。统计文件中的
`deadline_misses=6000` 是零容忍口径：只要 jitter 为正即计数，不能解释成 6000 次
实际 deadline miss。

原生证据位于：

```text
results/task1/qemu-20260825/native-zephyr-rerun-6000-02/
```

虚拟化证据位于本文档所在目录。虚拟化日志包含严格完成标记，CSV 为连续的
`0..5999`，`sha256sums` 已全部通过。

## RR 与 FP-RR 在本场景中的解释

相对 RR，FP-RR 的 Mean jitter 降低约 1.82%，超过 1 ms 的样本减少 41 个；但 P99
增加约 5.39%，P99.9 增加约 3.61%，Max 增加约 5.58%。因此本轮不能声称 FP-RR
全面改善尾延迟。

这是符合场景结构的：虚拟化侧只有一个 Zephyr vCPU，pCPU1 上没有另一个持续可运行
的竞争 vCPU，固定优先级没有充分的竞争对象。该三臂结果主要用于量化“直接运行”和
“经过虚拟层”的差异。调度优化收益应引用两侧均经过 AxVisor、且存在真实共核竞争的
RR/FP-RR A/B，不能用这个单 Guest 空载场景代替。

## 与旧轮次的差异

| 指标 | 旧原生 | 新原生 | 旧虚拟化 | 新虚拟化 |
|---|---:|---:|---:|---:|
| Mean jitter | 0.233 ms | 0.178 ms | 2.569 ms | 2.593 ms |
| P99 jitter | 0.429 ms | 0.422 ms | 3.493 ms | 3.569 ms |
| P99.9 jitter | 0.503 ms | 0.474 ms | 3.706 ms | 4.016 ms |
| Max jitter | 0.740 ms | 1.748 ms | 3.945 ms | 4.255 ms |
| 超过 1 ms | 0/6000 | 1/6000 | 5875/6000 | 5897/6000 |

虚拟化侧 Mean 增加约 0.92%，P99 增加约 2.19%；主要分位数与旧轮次接近，核心结论
没有变化。Max 对单个尾部样本敏感，不应仅凭一轮最大值判断退化。

旧轮次只记录了 Git commit，没有记录未提交虚拟定时器源码的逐文件指纹，因此不能证明
旧、新运行使用了完全相同的工作树，也不能把上述小幅变化归因于某一项实现改动。本轮已
额外保存 `virtualization-source-sha256s.txt`，用于核对本轮虚拟化实现的逐文件身份。

## 结论边界

本表用于比较同一 QEMU TCG 环境中直接运行 Zephyr 与经过 AxVisor 运行 Zephyr Guest
的端到端周期唤醒抖动。它证明当前虚拟定时器、vCPU 调度和 Guest 运行路径相对原生路径
存在额外开销。它不是 RK3588 实体板的绝对性能结论，也不能解释为整个系统慢了表中抖动
倍数。RR 与 FP-RR 的正式调度优化效果应使用两侧均经过 AxVisor、存在共核竞争的独立
A/B 数据说明。
