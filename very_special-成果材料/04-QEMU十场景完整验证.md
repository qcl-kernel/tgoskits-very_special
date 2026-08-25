# QEMU 功能与故障验证体系

“十场景”是本项目把实时调度、可靠网络和 AI 控制闭环拆成的十个自动化行为
场景，不是官网规定的数量。它们让每个机制都能独立启动、注入故障、保存证据并
由程序判定。

## 1. 十个行为场景

`ci-contracts` 是独立的源码和入口约束检查，不计入下列行为场景。

| # | 场景 | 系统层次 | 验证内容 |
| ---: | --- | --- | --- |
| 1 | `task3-yolo-smoke` | Task 3 | AArch64 ncnn/YOLO 真实加载和推理 |
| 2 | `task1-scheduler-ab` | Task 1 | 同负载 RR 组与 FP-RR 组各三次 |
| 3 | `task2-normal` | Task 2 | 无模型 CONTROL -> ACK/STATUS 正常链路 |
| 4 | `task23-integrated` | Task 2 + 3 | YOLO -> CONTROL -> Zephyr -> ACK/STATUS |
| 5 | `task2-drop-ack` | Task 2 | 丢首个 ACK、重传和重复抑制 |
| 6 | `task2-retry-exhausted` | Task 2 | 有界重试、Safe 和恢复 |
| 7 | `task2-blackout` | Task 2 | 全链路中断、双端 Safe 和恢复 |
| 8 | `task2-out-of-order` | Task 2 | 乱序 CONTROL 显式拒绝与恢复 |
| 9 | `task2-invalid-parameter` | Task 2 | CRC 合法但越界参数被拒绝 |
| 10 | `task3-model-rejected` | Task 3 | 非法模型输出不得产生 CONTROL |

## 2. Task 2 独立六场景结果

六个通信场景统一设置 `STARRY_TASK23_SCOPE=task2`，不安装、加载或运行模型。
验证器还会拒绝任何 Task 3 模型活动，因此结果能独立说明通信协议行为。

| 场景 | 双端 T2N1 帧数 | 语义验证 | 双 pcap | 模型隔离 |
| --- | ---: | --- | --- | --- |
| normal | 31 | 通过 | 通过 | 通过 |
| drop-ack | 33 | 通过 | 通过 | 通过 |
| retry-exhausted | 24 | 通过 | 通过 | 通过 |
| blackout | 34 | 通过 | 通过 | 通过 |
| out-of-order | 25 | 通过 | 通过 | 通过 |
| invalid-parameter | 26 | 通过 | 通过 | 通过 |

每个目录保存运行命令、原始日志、StarryOS/Zephyr 双 pcap、源配置、运行时
TOML、产物/rootfs 哈希、语义验证器、pcap 验证器和 SHA-256。

## 3. Task 3 与联合闭环结果

- `task3-yolo-smoke`：AArch64 用户态 ncnn 推理耗时 `12124223 us`。
- `task3-model-rejected`：观察到模型拒绝和 StarryOS Safe，拒绝点没有对应 CONTROL，双 pcap 对账通过。
- `task23-integrated`：Guest 内三次推理为 `15815184/16408084/15890595 us`；三次 CONTROL/ACK/STATUS 完整，双侧各观察到 20 个 T2N1 帧。
- `task1-scheduler-ab`：RR 组和 FP-RR 组使用相同 Guest、负载、CPU 映射与 10 ms 探针，只改变调度策略。

QEMU TCG 数值用于功能复现和同平台相对对比，不解释为 RK3588 NPU 性能。

## 4. 资源隔离修复

运行器使用 `TASK123_QEMU_LOCK_FILE` 和 `TASK123_QEMU_LOCK_TIMEOUT_SEC`
串行化高内存 QEMU。这样避免两个 `-m 8g` 实例在约 15 GiB 主机上同时运行、
swap 压力使串口 watchdog 误报。每次运行还使用独占的临时 rootfs、serial/QMP
socket 和 pcap 路径，清理时只处理本次 PID。

## 5. 运行入口

```bash
scripts/competition/task123.sh suite task1
scripts/competition/task123.sh suite task2
scripts/competition/task123.sh suite task3
scripts/competition/task123.sh run task23-integrated
scripts/competition/task123.sh suite full
```

机器成功标志为 `TASK123_SUITE_PASS name=<suite>`。场景验证不仅检查进程退出码，
还检查协议闭合、精确帧数、模型隔离、安全状态和 fatal marker。
