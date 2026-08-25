# 上游贡献与 StarryOS 完善

本文只记录能够通过 OpenRace 官网、公开 Pull Request、Issue 和 `dev` 分支提交
交叉核验的上游工作。状态核验日期为 **2026-08-26**。

## 1. 官网口径

OpenRace 任务页面在“加分项（最多 10 分）”的“StarryOS 完善”中明确写道：

> 添加各种 syscall 完善 Starry，并最终合并入 tgoskits 的 dev 分支，根据数量，最多 4 分。

因此，syscall 工作是否“最终合并入 `dev`”必须以公开仓库的合并状态为准。
未合并的实现可以证明已经完成的工程与论证工作，但本文不会把它写成已经满足该项
合入条件，也不预先主张对应分数。

官网原文：<https://opencamp.cn/qcl/camp/OpenRace2026/stage/1>

## 2. 状态总览

| 贡献 | 类型 | 公开状态 | `dev` 核验 | 本文结论 |
| --- | --- | --- | --- | --- |
| [PR #1953](https://github.com/rcore-os/tgoskits/pull/1953) `fix(arm-vcpu): preserve HVC exception PC` | AArch64 虚拟化正确性修复 | Merged | `fd63e7525` | 已合并的上游贡献 |
| [PR #2145](https://github.com/rcore-os/tgoskits/pull/2145) `fix(axvm): avoid machine and wait queue lock inversion` | AxVM 并发/锁修复 | Merged | `28d7af827` | 已合并的上游贡献 |
| [PR #2152](https://github.com/rcore-os/tgoskits/pull/2152) `feat(starry): add sched priority range syscalls` | StarryOS syscall 完善 | Open | 尚未合入 | 实现与验证已提交，等待人工审查；不计作已合并成果 |
| [Issue #2153](https://github.com/rcore-os/tgoskits/issues/2153) `design(starry): define sched priority range syscall compatibility` | #2152 的独立设计材料 | Open | 不适用 | 已回应自动审查提出的设计材料要求，等待维护者人工审批 |

前两个 PR 是已经进入官方 `dev` 分支的真实工程贡献，体现了虚拟化正确性和
并发可靠性工作；它们不是 syscall，因而不将其直接等同于官网“syscall 最终
合并入 `dev`”这一项。第三个 PR 才是 syscall 候选，目前仍如实标为未合并。

## 3. 已合并贡献：修复 HVC 异常返回地址

[PR #1953](https://github.com/rcore-os/tgoskits/pull/1953) 修复 AArch64 vCPU
处理 HVC trap 时错误推进程序计数器的问题。HVC 异常进入 EL2 后，`ELR_EL2`
已经指向 preferred return address；PSCI HVC 和通用 HVC 路径若再次执行 `+4`，
就会跳过 Guest 的下一条指令。修复保留 SMC 路径所需的推进，只去除 HVC 路径
的重复推进。

公开 PR 给出的验证包括：

- AArch64 QEMU 回归 `2 passed; 0 failed`；
- AxVisor + QEMU + Zephyr 双 vCPU 能观察到 secondary CPU 启动；
- SGI smoke test 完成 `sent=300 received=300`；
- PR 已合并到 `rcore-os:dev`，对应提交 `fd63e7525`。

这个修复直接关系到虚拟化 Guest 的控制流正确性，避免 HVC 返回后跳过指令，
也为多 vCPU Guest 的可靠启动提供了底层保障。

## 4. 已合并贡献：消除 VM 与等待队列锁反转

[PR #2145](https://github.com/rcore-os/tgoskits/pull/2145) 修复 AxVM 生命周期
machine lock 与 runtime wait queue 之间的 ABBA 锁反转。原来的两条并发路径为：

```text
vCPU waiter                         runtime notifier
lock(runtime.wait_queue)            lock(vm.machine)
  evaluate vm.running()               runtime.notify_one/all()
    lock(vm.machine)                    lock(runtime.wait_queue)
```

修复后，在 machine lock 内只选择并克隆 `Arc<VmRuntimeHandle>`，释放 machine
lock 后再执行可能访问等待队列的回调，从结构上消除反向持锁。该边界覆盖 VM/vCPU
启动、停止、重置和销毁，也覆盖虚拟设备唤醒、中断与 IVC 通知路径。

公开 PR 给出的验证包括：

- 精确回归 `1 passed; 0 failed`；
- `cargo xtask clippy --package axvm` 六项检查全部通过；
- GitHub 页面显示 25 项检查全部通过；
- PR 已合并到 `rcore-os:dev`，对应提交 `28d7af827`。

## 5. syscall 候选：调度优先级范围查询

[PR #2152](https://github.com/rcore-os/tgoskits/pull/2152) 为 StarryOS 增加：

- `sched_get_priority_max(2)`；
- `sched_get_priority_min(2)`；
- StarryOS syscall dispatcher 接入；
- 对应 LTP 用例覆盖。

StarryOS 原先已有 `sched_setscheduler`、`sched_getscheduler`、`sched_setparam`、
`sched_getparam` 和 `sched_yield`，但缺少标准的优先级范围查询接口，使 libc helper
和 LTP 无法按策略查询有效范围。该改动只补齐查询 ABI，不新增调度算法，不实现
`SCHED_DEADLINE` 或 `SCHED_EXT`，也不改变任务状态、权限、capability、锁和既有
调度行为。

自动审查曾要求为该用户可见 Linux ABI 变更提供独立设计材料。作者随后建立
[Issue #2153](https://github.com/rcore-os/tgoskits/issues/2153)，补充了调用方与
问题定义、成功标准、非目标、方案比较、Linux/POSIX 语义依据、兼容性、风险、
回滚和验证计划，并在 PR 中请求维护者进行人工设计与测试审查。

截至 **2026-08-26**，#2152 仍为 Open，#2153 尚未获得维护者人工设计批准或
合并决定。准确状态是：**自动审查提出的材料要求已经响应，当前等待维护者人工
审查**；不能写成“没有任何审查”，也不能写成“已经合并”或已经满足官网对应条件。

## 6. 核验方式

1. 打开官网任务页，核对“StarryOS 完善”对最终合入 `dev` 的要求。
2. 打开 #1953 和 #2145，核对 Merged 状态、目标分支 `rcore-os:dev`、验证记录
   与合入提交。
3. 打开 #2152，核对 Open 状态、实现文件、讨论和自动审查意见。
4. 打开 #2153，核对独立设计论证及当前人工审批状态。
5. 在本仓库中可用 `git show fd63e7525` 和 `git show 28d7af827` 核对两个已经
   进入当前 `dev` 历史的上游提交。

这组证据把“已合并事实”“已完成但待审查的工作”和“官网条件”分层呈现，既展示
实际投入，也保留清晰、可复核的状态边界。
