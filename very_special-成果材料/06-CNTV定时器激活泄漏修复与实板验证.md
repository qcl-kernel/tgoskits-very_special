# CNTV 定时器激活泄漏修复与实板验证

> 验证日期：2026-08-25
>
> 分支：`competition/task123-integration-20260824`
>
> 基线 HEAD：`b1a9d2375ec46232b78be388783957a672014db1`
>
> 证据性质：当前 dirty worktree 的修复验证，不冒充 clean-commit 结果

## 1. 结论

相同 FP-RR FIT 偶发出现“Zephyr 无输出、StarryOS 停在早期初始化”的直接原因，是 AArch64 宿主 CNTV PPI 已被 acknowledge，却在没有发布 Guest virtual-timer PPI 时仍保持 active。因为 Guest 没有对应 PPI 可以 EOI，宿主 token 永远无法退役，承载共享 Guest 的 pCPU1 随后不再收到 timer IRQ。

本次修复补全了 CNTV 激活的所有权规则：

- Guest virtual-timer PPI 已发布：保留宿主激活，等待对应 Guest EOI 退役。
- Guest virtual-timer PPI 未发布：立即退役宿主激活。
- timer level 发布失败：尝试退役宿主激活，再把原始发布错误返回给调用方。
- Guest physical timer 的电平不再被误当成 CNTV 激活的退役路径。

修复后，原 v4 双 Guest、共享 pCPU1、FP-RR 配置连续三次 RAM-only 实板启动均成功；StarryOS 与 Zephyr 每轮均到达 Ready。两轮周期采样均完整输出 300 条结果，CPU1 的宿主 timer IRQ 分别达到 `117117` 和 `30020`，未再出现失败样本中的 `432` 后停止增长。

## 2. 现象与排除项

### 2.1 成功与失败来自同一构建产物

原 v4 FP-RR FIT 的 SHA256 为：

```text
801894342f6adc14572b7f885656979bf66569af0c2d6a3f071f2dc7a75fb23a
```

它曾成功启动，也曾在下一次启动中卡死，因此 FDT、cpio、feature、Guest 二进制和设备配置等确定性输入不能解释这次波动。

成功日志：

```text
tmp/competition-task123/board-current/board-evidence/fp-rr-v4-console.log
```

失败日志与诊断：

```text
tmp/competition-task123/board-current/board-evidence/fp-rr-v4-task1-run2-boot.log
tmp/competition-task123/board-current/board-evidence/fp-rr-v4-task1-run2-rt-stat.log
```

失败时的关键状态：

```text
cpu=0 irqs=33887
cpu=1 irqs=432
cpu=2 irqs=33473
VM1 Free:1,Rdy:1
VM2 Run:1
vm1 output_enqueued=528
vm2 output_enqueued=0
```

CPU0、CPU2 的 timer IRQ 已达到约 3.3 万次，CPU1 却停在 432；同时高优先级 Zephyr 保持 Run，低优先级 StarryOS 主 vCPU 无法继续。这是共享 pCPU1 的硬件 timer 激活没有完成的直接运行态特征。

### 2.2 ext4 `EUCLEAN` 不是本次根因

成功与失败样本都出现过相同的 ext4 `EUCLEAN`。它需要独立治理，但不能解释“同一 FIT 一次成功、一次失败”，也不能解释 CPU1 timer IRQ 单独停止。

### 2.3 归档仓库也存在同一泄漏路径

对照的下午归档仓库为
`/media/huhu/50528FAE528F977E/tgoskits-task123-home-archive-20260825/tgoskits-atk-review-20260824`，HEAD 是 `eaa2bf9061e35c70378d3ed5c1f5bb70f64224fb`。当前干净基线是
`b1a9d2375ec46232b78be388783957a672014db1`。

两个 HEAD 的整文件哈希并不全部相同，因为中间确实合入了其他 timer、wake 和调度改动；因此不以“整文件一致”作为根因证据。可核验的直接证据是，两边的 CNTV 所有权路径一致：

```rust
pub fn synchronize(...) -> VgicResult {
    self.invalidate_wait();
    self.publish_levels(snapshot, physical_counter()).map(|_| ())
}
```

归档 HEAD 和当前基线都把 `publish_levels()` 的结果丢弃，而且都返回
`virtual_level || physical_level`，没有在 `virtual_level == false` 时退役已 acknowledge 的 host CNTV token。这就是本次泄漏的必要代码条件。

因此，这个竞态在下午归档代码中已经潜伏；成功运行只是没有命中该启动时序窗口，不能反证旧所有权逻辑正确。

## 3. 根因链路

1. `accept_host_irq()` acknowledge 宿主 CNTV PPI，并把 token 保存到 `host_activation`，有意延迟 deactivate。
2. vCPU 退出后，`synchronize()` 发布 Guest virtual/physical timer 电平。
3. 旧实现丢弃 `publish_levels()` 的布尔结果。
4. 如果这一次没有发布 Guest virtual-timer PPI，Guest 就没有对应 PPI 可 EOI。
5. `retire_emulated_interrupt()` 永远不会被调用，宿主 CNTV token 一直保持 active。
6. 该 pCPU 后续 timer IRQ 停止，FP-RR 调度无法继续推进共享 Guest。

> 注意：宿主 token 来自 CNTV，所以只有 Guest virtual-timer PPI 能构成对应的 EOI 退役路径。即使 Guest physical timer 同时为 asserted，也不能因此保留 CNTV token。

## 4. 修复设计

新增内部状态 `HostActivationDisposition`，把所有权决定表达为两个显式状态：

```rust
pub(crate) enum HostActivationDisposition {
    HoldForGuestRetirement,
    RetireImmediately,
}
```

`publish_levels()` 不再返回混合后的单一布尔值，而是返回分别记录 virtual/physical 电平的 `PublishedTimerLevels`。这样：

- `arm_wait()` 仍可使用 `any_asserted()` 判断是否需要立即唤醒；
- `synchronize()` 只依据 `virtual_timer` 决定 CNTV token 的归属；
- 两种语义不再共享一个容易误用的布尔值。

修复入口位于：

- `virtualization/axvm/src/arch/aarch64/vtimer/activation.rs`
- `virtualization/axvm/src/arch/aarch64/vtimer/state.rs`
- `virtualization/axvm/src/arch/aarch64/vtimer/mod.rs`
- `virtualization/axvm/src/lib.rs`（让纯策略测试可在 host-test 中运行）

## 5. 回归测试：先红后绿

新增两个最低层策略测试：

- `retires_host_activation_when_guest_virtual_timer_is_disabled`
- `holds_host_activation_while_guest_virtual_timer_is_asserted`

在修复实现之前，旧策略测试按预期失败：

```text
assertion `left == right` failed
  left: HoldForGuestRetirement
 right: RetireImmediately
```

实现修复后，同一测试转绿：

```text
running 2 tests
test ...retires_host_activation_when_guest_virtual_timer_is_disabled ... ok
test ...holds_host_activation_while_guest_virtual_timer_is_asserted ... ok

test result: ok. 2 passed; 0 failed
```

完整 `axvm` host-test：

```bash
cargo test -p axvm --features host-test
```

结果：

```text
test result: ok. 374 passed; 0 failed; 0 ignored
```

针对 crate 的 clippy：

```bash
cargo xtask clippy --package axvm
```

结果：11 组 base/feature/host-test 检查全部通过。

格式化：

```bash
cargo fmt -p axvm
```

### 5.1 AxVisor AArch64 QEMU 端到端验证

timer-stress 需要先按 CI 的标准流程准备 Linux Guest 镜像：

```bash
cargo xtask image pull qemu-aarch64 --extract-dir tmp/axbuild/images
cargo xtask axvisor test qemu \
  --arch aarch64 \
  --test-group normal \
  --test-case gicv3-timer-stress
```

第一次单独运行 timer-stress 时在启动 QEMU 之前失败，原因是
`tmp/axbuild/images/qemu-aarch64/linux/linux-qemu` 未准备；这是漏跑 CI 明确列出的 image-pull 前置步骤，不是 Guest 或 CNTV 运行失败。补齐标准前置后，真实进入 AxVisor 和四 vCPU Linux Guest，并通过 GICv3/ITS timer stress：

```text
AXVISOR_GICV3_ITS_TIMER_STRESS_PASSED
PASS gicv3-timer-stress (30.69s)
result: 1/1 case(s) passed
```

同一工作树的 AArch64 smoke 也通过，覆盖 AxVisor 启动、四核初始化、NVMe rootfs 读写和 shell 判据：

```bash
cargo xtask axvisor test qemu \
  --arch aarch64 \
  --test-group normal \
  --test-case smoke
```

```text
PASS smoke (2.72s)
result: 1/1 case(s) passed
```

## 6. 真实 AArch64 构建

使用原 v4 的 FP-RR board config 和双 Guest config 构建，不采用 v5 CPU swap：

```bash
cargo xtask axvisor build \
  --config tmp/competition-task123/board-current/current-branch-fit-v4/configs/atk-task123-zephyr-fp-rr-board.toml \
  --vmconfigs tmp/competition-task123/board-current/current-branch-fit-v4/configs/atk-task123-zephyr-starry.toml \
  --vmconfigs tmp/competition-task123/board-current/current-branch-fit-v4/configs/atk-task123-zephyr.toml
```

构建成功，真实 AArch64 AxVisor 产物为：

```text
target/aarch64-unknown-linux-musl/release/axvisor
```

修复验证 FIT 独立保存在：

```text
tmp/competition-task123/board-current/timer-activation-fix-v1/
```

产物哈希：

| 产物 | SHA256 |
| --- | --- |
| 修复后 AxVisor raw binary | `b4da7e83b4a7cb2d475e2561ee3ce95a43581846d5ffc0bea92d85648476a182` |
| 修复后 FP-RR FIT | `1d9aa8766ac538ef05f934a8e5c6286e812b3f237b03ad31c192574181f9c5eb` |
| 沿用的 v4 Zephyr Guest | `c7f9b85c5f76dc3d7b20bfc30c002b7e671ba33d4b08e372487f6348f519cd29` |

## 7. 实板验证

### 7.1 安全边界

每轮均通过以下入口启动：

```bash
scripts/board/atk-dlrk3588-ram-boot.sh <fixed-fit>
```

脚本只执行 `fastboot stage`，随后在 U-Boot 中 `booti`；没有执行 `flash`、`erase` 或 GPT/eMMC 写入。

### 7.2 拓扑与调度配置

本轮保持 v4 的原共享拓扑：

```text
StarryOS vCPU0 -> pCPU1
StarryOS vCPU1 -> pCPU2
Zephyr vCPU0   -> pCPU1
StarryOS priority = 89
Zephyr priority   = 90
scheduler          = FP-RR
```

本轮只验证 timer 激活泄漏修复，不把后来工作区的 v5 CPU swap 当作根因修复。

### 7.3 三轮连续启动

| 轮次 | Zephyr managed Ready | StarryOS controller Ready | Periodic Ready | 观察期双向心跳 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| run1 | 1 | 1 | 1 | 801 | 通过 |
| run2 | 1 | 1 | 1 | 182 | 通过 |
| run3 | 1 | 1 | 1 | 209 | 通过 |

对应日志：

```text
tmp/competition-task123/board-current/timer-activation-fix-v1/run1-console.log
tmp/competition-task123/board-current/timer-activation-fix-v1/run2-console.log
tmp/competition-task123/board-current/timer-activation-fix-v1/run3-console.log
```

三个日志中出现的 `Exception Handling Framework` 是 ARM Trusted Firmware 的正常启动文本，不是 panic/exception 故障。

### 7.4 CPU1 timer IRQ 与周期任务证据

run1 诊断：

```text
RT host hardware timer IRQ counters:
  cpu=0 irqs=14460
  cpu=1 irqs=117117
  cpu=2 irqs=27261
...
PERIODIC LATENCY COMPLETE samples=300
```

run3 诊断：

```text
cpu=1 irqs=30020
PERIODIC LATENCY COMPLETE samples=300
```

诊断日志：

```text
tmp/competition-task123/board-current/timer-activation-fix-v1/run1-diagnostic.log
tmp/competition-task123/board-current/timer-activation-fix-v1/run3-diagnostic.log
```

相对于失败样本中 CPU1 停在 432，本轮 CPU1 timer IRQ 能持续增长，并且 Zephyr 两轮都完整完成 300 个周期样本，证明该 pCPU 的时间推进已恢复。

### 7.5 最终 v6 拓扑 RR/FP-RR 3+3

根因修复验证后，最终 v6 拓扑又完成 RR 3 轮和 FP-RR 3 轮实板 RAM-only 采样。每轮都有连续 300 个周期样本、完成标记和零 deadline miss；六份启动日志也都包含 `UVC_RKNN_VALIDATE_PASS`。

| 调度器 | 轮数 | 中位 P99 | 中位 P99.9 | 中位最大值 | deadline miss |
| --- | ---: | ---: | ---: | ---: | ---: |
| RR | 3 | 0.386 ms | 0.573 ms | 0.573 ms | 0 |
| FP-RR | 3 | 0.273 ms | 0.566 ms | 0.566 ms | 0 |

中位 P99 降低 `29.284%`。原始串口日志、周期 CSV、汇总、SVG 和全部 SHA256 位于：

```text
tmp/competition-task123/board-current/final-v6-evidence/
tmp/competition-task123/board-current/final-v6-evidence/task1-analysis/SUMMARY.md
tmp/competition-task123/board-current/final-v6-evidence/SHA256SUMS.txt
```

### 7.6 v6 证据的任务边界

上述六轮的 **PASS 只针对 Task 1 periodic 判据**。启动日志同时保留了不得忽略的 Task 2/3 和文件系统状态：

| 范围 | 观测 | 状态 |
| --- | --- | --- |
| Task 1 periodic | RR 3 轮 + FP-RR 3 轮，每轮 300 连续样本，零 deadline miss | **PASS** |
| Task 3 FP-RR 主路 | 三份日志都有 `TASK3_EXPERIMENT_COMPLETE events=12 statuses=12` | **PARTIAL**，未覆盖完整故障矩阵 |
| Task 2/3 RR 主路 | 三份日志都有 `TASK2_ERROR=RKNN event sequence does not match the frozen scene` | **FAIL** |
| ext4 | 六份 boot log 均有 `EUCLEAN: Structure needs cleaning` | **UNRESOLVED** |

因此，不得把 `final-v6-evidence/` 整体称为“Task 1/2/3 全部实板 PASS”，也不得用 FP-RR 三轮的 Task3 完成标志掩盖 RR 三轮的明确失败。

## 8. 当前结论边界与后续工作

本轮已经证明：

- 失败并非相同配置的确定性输入变化，而是首次 CNTV/Guest timer assert 的启动竞态。
- 修复后的所有权状态机通过了先红后绿的确定性测试。
- AxVisor AArch64 smoke 和 GICv3/ITS timer stress 都真正进入 QEMU 并通过。
- 真实 AArch64 FP-RR 双 Guest 构建成功。
- 原共享拓扑连续三轮实板 RAM 启动成功，且 CPU1 timer IRQ 与 Guest 周期任务持续推进。
- 最终 v6 拓扑 RR/FP-RR 3+3 共六轮实板采样通过，每轮 300 样本且无 deadline miss。

本轮尚不声称：

- 已解决 ext4 `EUCLEAN`；它是独立问题。
- 已完成 Task 2/3 所有正常态和故障注入的当前分支实板矩阵；本文的新鲜实板证据聚焦 CNTV、Task 1 和集成 RKNN 验证。
- 当前 dirty worktree 已形成可提交的 clean commit；本轮没有 commit、没有切分支。
- 已解决 RR 三轮的 RKNN frozen-scene sequence error；它仍是 Task 2/3 实板失败项。

后续提交前仍需：

1. 完成 Task 2/3 正常态与故障场景的当前分支实板复测。
2. 将 QEMU 原始控制台输出与已归档的实板日志一起收纳到最终提交包。
3. 保持 CPU 角色映射为 Starry vCPU0 独占 pCPU2、通信 vCPU1 与 Zephyr 共享 pCPU1，不再回退到主启动 vCPU 与 RTOS 共核。
