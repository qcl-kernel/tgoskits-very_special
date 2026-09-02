# QEMU 分任务功能与故障验证

QEMU 验证按 Task 1、Task 2、Task 3 分别启动、分别保存证据、分别给出 PASS，
避免把不同任务的数据混在同一份汇总里。Task 2+3 联合闭环另设独立入口。

## 1. Task 1：调度矩阵

`suite task1` 完成四组同配置对照，仅改变 AxVisor 调度策略和是否施加推理压力。
每组都采集 6000 个 10 ms 周期样本：

| 负载 | 调度器 | mean | P99 | P99.9 | max | `>1 ms` |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 空载 | RR | 1.096 ms | 3.153 ms | 4.624 ms | 5.279 ms | 1991/6000 |
| 空载 | bounded FP-RR | 0.868 ms | 1.654 ms | 2.899 ms | 3.579 ms | 861/6000 |
| ncnn/YOLO 强竞争 | RR | 16.118 ms | 117.714 ms | 229.279 ms | 243.835 ms | 5994/6000 |
| ncnn/YOLO 强竞争 | bounded FP-RR | 0.893 ms | 5.517 ms | 15.277 ms | 16.502 ms | 134/6000 |

空载时 P99 从 3.153 ms 降到 1.654 ms，降低 47.55%（约
1.907 倍）；强竞争时从 117.714 ms 降到 5.517 ms，降低
95.31%（约 21.337 倍）。这说明无竞争时两种调度已较接近，竞争越强，
有界优先级调度对周期长尾的改善越明显。

这一 QEMU 强竞争组是为放大调度竞争而保留的机制消融，不是正式实板拓扑。
正式 RK3588 主线始终是：pCPU2 运行 StarryOS AI/RKNN 角色并调用 NPU，
pCPU1 共享 StarryOS 通信 vCPU 与 Zephyr。QEMU 数据用于同平台机制对照，
不替代正式实板 RR/FP-RR 统计。

### Task 1 multi-vCPU：无板条件下的最终实板拓扑复现

`suite task1-multivcpu` 不是另一个强竞争消融。它的用途是解决评审老师
没有 RK3588 板卡时无法亲自验证多 vCPU 架构的问题。QEMU 启动 3 个
pCPU：StarryOS Guest CPU 0 绑定 pCPU2 运行 ncnn/YOLO 压力，Guest CPU 1
绑定 pCPU1 运行 T2N1 通信；Zephyr vCPU0 也绑定 pCPU1 运行 10 ms
周期工作。优先级仍为 Zephyr 90、StarryOS 89。

已完成的正式矩阵为 RR 3 轮 + bounded FP-RR 3 轮，每轮 6000 个
10 ms 样本。以三轮中位数对比：

| 指标 | RR | bounded FP-RR | 改善 |
| --- | ---: | ---: | ---: |
| mean | 1.132 ms | 0.809 ms | 降低 28.55% |
| P99 | 2.314 ms | 1.781 ms | 降低 23.05% |
| P99.9 | 3.718 ms | 2.155 ms | 降低 42.05% |
| max | 6.294 ms | 2.923 ms | 降低 53.56% |
| `>1 ms` | 3562/6000 | 322/6000 | 降低 90.96% |

六轮均验证 YOLO 压力和 T2N1 通信在采样窗口内持续推进，且通过
vCPU/pCPU 映射、Guest 内 affinity、双侧 pcap、调度计数和不变产物
哈希校验。验证器的矩阵结论为：

```text
PASS: Task 1 QEMU topology matches the frozen board CPU-role contract
```

“匹配”只指 CPU 数量、角色放置和调度竞争关系。QEMU 不模拟 RK3588 NPU
直通、DMA/IRQ 和 SoC 带宽，所以不使用该矩阵推导 NPU 性能或与实板绝对
时延一致。

## 2. Task 2：独立通信场景

六个通信场景统一设置 `STARRY_TASK23_SCOPE=task2`，不安装、加载或运行模型。
验证器还会拒绝任何 Task 3 模型活动，因此结果能独立说明通信协议行为。

| 场景 | 每侧 T2N1 帧 | 本轮实测结果 | 双 pcap/模型隔离 |
| --- | ---: | --- | --- |
| normal | 30 | 3 次事务 RTT 为 330/83/50 ms | 通过 |
| drop-ack | 30 | 首次 ACK 丢失，重传 1 次；RTT 315/74 ms | 通过 |
| retry-exhausted | 24 | 重传 attempt 1..5，3041 ms 进入 `RetryExhausted/Safe` | 通过 |
| blackout | 35 | 重传 1..5，5483 ms 进入 Safe；恢复 RTT 64 ms，闭环恢复用时 14557 ms | 通过 |
| out-of-order | 25 | 304 ms 检出乱序，1640 ms 完成 Safe→恢复；恢复 RTT 74 ms | 通过 |
| invalid-parameter | 23 | 310 ms 拒绝非法参数，1628 ms 完成 Safe→恢复；恢复 RTT 74 ms | 通过 |

每个目录保存运行命令、原始日志、StarryOS/Zephyr 双 pcap、源配置、运行时
TOML、产物/rootfs 哈希、语义验证器、pcap 验证器和 SHA-256。

## 3. Task 3：独立模型场景

- `task3-yolo-smoke`：AArch64 用户态真实 ncnn/YOLO 推理耗时
  `13434943 us`（约 13.435 s）；输出 class 75、confidence 0.843、
  center-x 0.421、area 0.408。
- `task3-model-rejected`：观察到 `InjectedInvalidOutput` 模型拒绝和
  StarryOS Safe。双侧各 13 个 T2N1 帧全为 HEARTBEAT，没有 CONTROL，
  说明错误模型输出没有进入控制链。双 pcap 对账通过。

## 4. Task 2+3：联合闭环

- `task23-integrated`：Guest 内三次真实推理为
  `16885823/17179718/16680599 us`，平均 `16915380 us`（约 16.915 s）。
- 三次推理结果均生成 CONTROL，CONTROL/ACK/STATUS 完整 RTT 为
  `111/775/724 ms`，平均约 536.7 ms。
- 双侧各观察到 20 个 T2N1 帧：8 HEARTBEAT、3 CONTROL、6 ACK、3 STATUS；
  StarryOS 与 Zephyr 两份 pcap 分别验证通过。

QEMU TCG 数值用于功能复现和同平台相对对比，不解释为 RK3588 NPU 性能。

## 5. 复现稳定性

运行器使用 `TASK123_QEMU_LOCK_FILE` 和 `TASK123_QEMU_LOCK_TIMEOUT_SEC`
串行化高内存 QEMU。这样避免两个 `-m 8g` 实例在约 15 GiB 主机上同时运行、
swap 压力使串口 watchdog 误报。每次运行还使用独占的临时 rootfs、serial/QMP
socket 和 pcap 路径，清理时只处理本次 PID。

抓包收尾先执行 `virtnet capture off` 并等待确认，再流式导出 pcap。
这避免了网络路径仍在写入抓包队列时，导出路径持有同一把锁而卡在
`CAPDUMP_BEGIN`。联合闭环重跑时先停止了 1140 个缓冲帧的抓包，
随后完整输出 `CAPDUMP_END`并生成双 pcap。

## 6. 运行入口

```bash
scripts/competition/task123.sh suite task1
scripts/competition/task123.sh suite task1-multivcpu
scripts/competition/task123.sh suite task2
scripts/competition/task123.sh suite task3
scripts/competition/task123.sh run task23-integrated
```

各个 suite 分别创建独立的 `suite-task1`、`suite-task1-multivcpu`、
`suite-task2`、`suite-task3` 目录，并输出对应的
`TASK123_SUITE_PASS name=<task>`。联合闭环输出自己的 scenario 证据目录。
验证器不仅检查进程退出码，还检查协议闭合、精确帧数、模型隔离、安全状态和
fatal marker。`suite acceptance/full` 只保留为自动化回归入口。
