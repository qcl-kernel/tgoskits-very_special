# CNTV 定时器激活泄漏修复与实板验证

本页记录 Task 1 中一个直接影响实时性的底层问题：共享 pCPU 上，Zephyr 的
10 ms 周期中断曾在启动阶段停止推进。最终定位到 AArch64 虚拟定时器的激活
所有权状态没有在所有分支上正确归还，而不是周期任务、Guest 镜像或负载本身。

## 1. 可观察现象

问题出现时，Guest 已经启动，Zephyr 也进入周期任务，但宿主对应 pCPU 的
timer IRQ 计数在较小值后停止增长。其他 pCPU 的计数仍增长，说明不是整个系统
停机；周期输出中断则说明时间推进链路在该共享核上被截断。

```text
host timer IRQ
  -> acknowledge CNTV token
  -> evaluate Guest virtual timer state
  -> inject or retire
  -> deactivate / EOI host interrupt
```

诊断重点因此从“Guest 是否 runnable”收缩到“已经 acknowledge 的 host token
是否在每条返回路径上完成 retire/deactivate”。

## 2. 根因

旧路径把“Guest 当前没有 asserted virtual timer”误解为“本次不需要继续处理”。
但 host CNTV token 已经被 acknowledge；如果直接返回，GIC 仍认为该中断处于
active 状态。后续定时器边沿无法正常再次送达，最终表现为某个 pCPU 的 timer IRQ
停止增长。

这不是简单的“少调用一次 EOI”。中断路径同时涉及：

- host CNTV token 的拥有者；
- Guest virtual timer 的 asserted/pending 状态；
- GIC acknowledge、deactivate 与 EOI 顺序；
- vIRQ 是否已进入 LR、pending queue 或 retry slot；
- IRQ context 与抢占 guard 何时释放。

如果这些状态只用松散布尔值表达，早返回很容易跳过所有权归还。

## 3. 修复设计

实现把定时器激活过程拆成显式状态转换：

```text
Host CNTV acknowledged
          |
          +-- Guest timer asserted --> 生成/保留 vIRQ --> 完成 host token
          |
          +-- Guest timer not asserted -----------------> 立即 retire
          |
          +-- LR 暂不可用 -----------> pending/retry ---> 完成后再推进
```

核心原则是：一旦取得 host token，当前调用必须把它交给一个明确的后继状态，
不能无声丢弃所有权。相关实现位于：

- `virtualization/axvm/src/arch/aarch64/vtimer/mod.rs`
- `virtualization/axvm/src/arch/aarch64/vtimer/state.rs`
- `virtualization/axvm/src/arch/aarch64/vtimer/activation.rs`
- `virtualization/axvm/src/runtime/dispatcher.rs`
- `virtualization/axvm/src/architecture/ops.rs`

同时保持以下中断与调度顺序：先完成 GIC deactivate/EOI，再释放 IRQ-context 与
抢占 guard；需要唤醒或重新调度时，在宽锁之外执行通知，避免锁顺序与回调交叉。

## 4. 确定性回归

回归先构造“token 已 acknowledge，但 Guest timer 尚未 asserted”的状态。修复前，
测试观察到 token 没有退休；修复后，同一测试验证：

1. 未 asserted 分支立即 retire；
2. asserted 分支只注入一次；
3. LR 暂不可用时进入有界 pending/retry；
4. retry 成功后状态清空；
5. 重复边沿不会造成无界分配或重复执行。

随后 AxVM 单元回归、AArch64 smoke 和 GIC timer stress 都进入真实 QEMU
执行路径，证明修复不是仅在 mock 状态机中成立。

## 5. 实板验证

正式 CPU 角色映射保持不变：

```text
pCPU2: StarryOS vCPU0 -> RKNN/NPU 与后处理

pCPU1: StarryOS vCPU1 -> VirtIO/T2N1
     + Zephyr vCPU0   -> 10 ms 周期任务
```

修复后连续进行 RAM-only 实板启动和周期采样。诊断运行中，pCPU1 的 host timer
IRQ 计数分别推进到 `117117` 和 `30020`，Zephyr 均完整输出 300 个周期样本，
不再停在早期观察到的 `432`。随后 RR 组和 FP-RR 组各完成三轮：

| 调度策略 | 轮数 | 中位 P99 | 中位 P99.9 | 中位最大值 | deadline miss |
| --- | ---: | ---: | ---: | ---: | ---: |
| RR | 3 | 0.386 ms | 0.573 ms | 0.573 ms | 0 |
| FP-RR | 3 | 0.273 ms | 0.566 ms | 0.566 ms | 0 |

中位 P99 降低 `29.284%`。在此基础上，正式通信共核矩阵进一步扩展到每组
三轮、每轮 6000 个 10 ms 样本，结果见
[01-Task1-实时调度设计与结果.md](01-Task1-实时调度设计与结果.md)。

## 6. 工程意义

- 实时性不是只换调度器；timer token、GIC active 状态和 vIRQ 队列同样决定长尾。
- “Guest 没有 asserted”不代表 host 中断无需收尾；判断必须基于资源所有权。
- 连续 IRQ 计数、完整周期样本和多轮实板数据共同证明时间推进恢复，单次启动成功不足以证明修复。
- 修复与正式 CPU 映射解耦：AI 留在 pCPU2，通信与 RTOS 在 pCPU1 共核，机制结论不依赖把 AI 人为放到实时核上。
