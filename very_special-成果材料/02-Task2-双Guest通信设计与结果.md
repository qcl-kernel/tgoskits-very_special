# Task 2：双 Guest 通信设计与结果

## 任务思路：把网络、协议可靠性和安全状态分成三层

赛题明确禁止把共享内存、HyperCall 或裸 MMIO 当作主数据通道，因此我们没有把“两个 Guest 能交换字节”当成完成。Task 2 分成三层：

1. **网络层**：每个 Guest 拥有独立 VirtIO-net、virtqueue、DMA carveout、stage-2 映射和 vIRQ，经 AxVisor 内部 L2 switch 交换以太网帧。
2. **传输层**：使用 UDP/IPv4，保留消息边界、实现简单，适合低频控制；UDP 不可靠性由应用协议显式处理。
3. **应用层**：T2N1 定义版本化定长头、类型化 payload、CRC32、序号、ACK、重传、去重、乱序拒绝、心跳和 Safe/恢复状态机。

这种设计让 QEMU 与实板共用同一协议和 endpoint 程序，同时把网络可达性、报文正确性与控制安全分别验证。

## 设计

Task 2 的两个 endpoint 分别在一个 2-vCPU StarryOS Linux Guest 和一个 Zephyr RTOS Guest 内。StarryOS 的通信职责由 vCPU1 承担；不存在第二个 Linux Guest。

StarryOS 和 Zephyr 拥有独立 VirtIO-MMIO 队列、DMA 区间、stage-2 映射和 vIRQ 路由，两 endpoint 加入 AxVisor 内部 L2 switch。主数据面是 UDP/IPv4；共享内存、HyperCall、裸 MMIO 和 vsock 不是控制通道，QMP 只用于故障控制和 QEMU 生命周期。

| QEMU 字段 | StarryOS | Zephyr |
| --- | --- | --- |
| IPv4 | `10.0.42.15/24` | `10.0.42.2/24` |
| UDP | `4242` | `4242` |
| MAC | `52:54:00:12:34:01` | `52:54:00:12:34:02` |

物理板统一模板把 StarryOS MAC 配置为 `52:54:00:12:34:15`、Zephyr 保持 `52:54:00:12:34:02`；IP 与端口不变。MAC 的最后一字节差异只用于区分环境，不改变 T2N1 线格式或 endpoint 语义。

T2N1 定长头包含 magic、version、kind、flags、session、sequence、acknowledgement、payload length、error code 和 CRC32，支持 CONTROL、STATUS、ACK、ERROR 和 HEARTBEAT。CONTROL/STATUS 使用 stop-and-wait ACK、有界重传、重复抑制和乱序检查。心跳超时、重试耗尽或严重协议错误进入 Safe，恢复后可靠流从 sequence 1 重新同步。

## T2N1 报文格式

一个 UDP datagram 只承载一个 T2N1 frame。所有多字节整数使用网络字节序（big-endian），固定头为 28 字节，payload 最大 1200 字节。

| 字节偏移 | 长度 | 字段 | 当前值/语义 |
| ---: | ---: | --- | --- |
| 0 | 4 | `magic` | ASCII `T2N1` |
| 4 | 1 | `version` | 当前为 1 |
| 5 | 1 | `kind` | 1 CONTROL；2 STATUS；3 ERROR；4 ACK；5 HEARTBEAT |
| 6 | 2 | `flags` | bit 0=`RELIABLE`，其余位必须为 0 |
| 8 | 4 | `session` | 会话身份；当前 endpoint 使用 `0x54525432` |
| 12 | 4 | `sequence` | CONTROL/STATUS 的非零可靠序号；回绕时跳过 0 |
| 16 | 4 | `acknowledgement` | ACK 或 ERROR 所关联的序号；无关联时为 0 |
| 20 | 2 | `payload_len` | 0–1200，必须等于 UDP 数据报剩余长度 |
| 22 | 2 | `error_code` | 0 None；1 InvalidParameter；2 OutOfOrder；3 UnsupportedMessage；4 SessionMismatch |
| 24 | 4 | `crc32` | IEEE CRC-32；计算时本字段按 0 处理，覆盖头和 payload |

类型化 payload：

| 消息 | payload 布局 | 语义 |
| --- | --- | --- |
| CONTROL（12 B） | `action:u8, reserved[3], value:i32, request_id:u32` | action 1=SetOutput、2=Stop、3=Reset；SetOutput 范围 0–1000 |
| STATUS（12 B） | `state:u8, flags:u8, reserved[2], value:i32, last_request_id:u32` | state 1=Active、2=Stopped、3=Safe；返回当前值和最近已执行请求 |
| ACK（0 B） | 无 payload | `acknowledgement` 指向已接受可靠帧 |
| ERROR（0–1200 B） | 可选诊断字节 | `error_code` 给出机器可判定原因，`acknowledgement` 关联被拒帧 |
| HEARTBEAT（8 B） | `uptime_ms:u64` | 非可靠、无序号，用于双向存活检测 |

CONTROL/STATUS 是 stop-and-wait：首次发送后 500 ms 未确认则重传，最多重传 5 次；心跳间隔 200 ms，5 s 内没有合法入站帧则进入 Safe。重复帧只重新 ACK、不重复执行；超前序号返回 OutOfOrder；重试耗尽或严重协议错误进入 Safe；合法链路恢复后序号从 1 重启。协议实现是 [`components/task2-net-protocol/`](../components/task2-net-protocol/)，不是只存在于文档中的格式。

## Task2/Task3 隔离

新的 `suite task2` 六场景统一设置 `STARRY_TASK23_SCOPE=task2`：

- 不要求、安装或校验 YOLO 模型文件；
- 不等待 `TASK3_MODEL_READY`；
- 不执行推理或检测；
- 验证器要求日志中不得出现 `TASK3_(MODEL|INFER|DETECTION|EXPERIMENT)` 活动，并给出 `TASK2_MODEL_ISOLATION_PASS`。

因此这套证据能单独回答“Task 2 自身是否通过”，不再借助 Task 3 模型活动作为 CONTROL 源。

## 新六场景 QEMU 整套：PASS（dirty）

证据根：`tmp/competition-task123/current-fix-qemu/20260824T201803Z-suite-task2-2820310/`

| 场景 | 主验行为 | 双端一致 T2N1 帧数 | 结果 |
| --- | --- | ---: | --- |
| `task2-normal` | CONTROL -> ACK/STATUS | 31 | **PASS** |
| `task2-drop-ack` | 单次丢 ACK、重传和重复抑制 | 33 | **PASS** |
| `task2-retry-exhausted` | 有界重试、Safe 和恢复 | 24 | **PASS** |
| `task2-blackout` | 双向中断、双端 Safe 和恢复 | 34 | **PASS** |
| `task2-out-of-order` | 乱序 CONTROL 显式拒绝并恢复 | 25 | **PASS** |
| `task2-invalid-parameter` | CRC 合法但越界的控制值被拒绝 | 26 | **PASS** |

每个子目录保存 `run.log`、StarryOS/Zephyr 双 pcap、`verify-scenario.log`、`verify-pcap.log`、源/运行时 TOML、产物与 rootfs 哈希、源码身份和 `SHA256SUMS.txt`。六个场景均通过语义验证、双 pcap 账本验证和模型隔离验证。

### 正常态与故障态对比

| 场景 | 正常/故障差异 | 可观察结果 |
| --- | --- | --- |
| normal | 无注入 | controller 可见 CONTROL→STATUS 3/3；RTT 45–338 ms，均值 152.0 ms |
| drop-ack | 首个 ACK 丢失 | 可靠帧重传，RTOS 对重复序号不重复执行；场景 33 帧，PASS |
| retry-exhausted | 连续丢 ACK | 5 次有界重传后进入 Safe，随后恢复；场景 24 帧，PASS |
| blackout | 双向链路中断 | `RetryExhausted` 后 Safe，链路恢复后 sequence 从 1 重启，恢复请求 RTT 59 ms；场景 34 帧，PASS |
| out-of-order | 注入超前序号 | 330 ms 观察 Safe、500 ms 恢复，恢复请求 RTT 64 ms；场景 25 帧，PASS |
| invalid-parameter | CRC 合法但 value 越界 | 返回 InvalidParameter，331 ms 观察 Safe、504 ms 恢复，恢复请求 RTT 64 ms；场景 26 帧，PASS |

这里的 RTT 是 StarryOS 同一单调时钟上从 CONTROL 发送到匹配 STATUS 收到的应用闭环时间，包含两个 Guest 调度、网络、RTOS 执行和回包；不是裸 UDP 单向延迟。当前 suite 的目标是低频控制正确性与故障恢复，尚未形成独立的饱和吞吐量基准，因此不能用帧数冒充网络吞吐量。

`task2-invalid-parameter` 还有一次独立重跑：`tmp/competition-task123/current-fix-qemu/20260824T201725Z-task2-invalid-parameter-2819432/`，其双端均观测到 28 个 T2N1 帧并通过两类验证器。

## 旧失败的真实原因

之前某次场景的 watchdog 超时不是协议或 Guest 的确定性失败。当时两个各自使用 `-m 8g` 的 QEMU 在约 15 GiB 物理内存的主机上并发，swap 已用满 `4/4 GiB`，严重内存压力使串口进度停滞，watchdog 因而产生假超时。

当前评委入口使用 `TASK123_QEMU_LOCK_FILE` 和 `TASK123_QEMU_LOCK_TIMEOUT_SEC` 统一互斥 QEMU 执行槽。真实重跑时，另一个 Task1 诊断任务被正确串行化，Task2 六场景随后整套通过。因此不应放宽 watchdog 或场景判据，而应保持资源互斥。

## 实板边界

当前分支尚未完成 Task2 正常态与五类故障的实板矩阵。v6 RR 三轮还出现 `TASK2_ERROR=RKNN event sequence does not match the frozen scene`，所以实板 Task2 状态为 **PARTIAL / FAIL**，不得由 QEMU PASS 或 Task1 periodic PASS 替代。

[`results/atk-dlrk3588-task123-20260823/task23/TASK2.md`](../results/atk-dlrk3588-task123-20260823/task23/TASK2.md) 是迁移前物理板历史证据，不能替代当前分支实板验收。

## 官方 25 分评分点对应

| 官方细则 | 当前对应内容 | 状态 |
| --- | --- | --- |
| Starry/Linux 与 RTOS IP 链路（4） | 双 VirtIO-net、L2 switch、MAC/IP/UDP 4242 | 已覆盖 |
| 应用协议字段完整（5） | T2N1 28 B 头、类型化 payload、big-endian、CRC32 | 已覆盖 |
| 控制、状态、错误消息（5） | CONTROL、STATUS、ERROR，另有 ACK/HEARTBEAT | 已覆盖 |
| 可靠性与异常恢复（4） | ACK、500 ms 超时、5 次重传、去重、乱序、5 s liveness、Safe/恢复 | 已覆盖 |
| 自动化测试数据（4） | Task2-only 正常态 + 五种故障，语义验证器与双 pcap | 已覆盖；证据仍是 dirty worktree |
| 隔离与访问控制（3） | 独立 virtqueue/DMA/stage-2/vIRQ，NPU 不暴露给 RTOS | 已覆盖设计与配置检查；仍需补实板完整故障矩阵 |

赛题任务要求还希望统计请求成功率、应用错误、超时、恢复、延迟和有效吞吐量。当前材料已覆盖成功事务、错误/超时/恢复和闭环 RTT；**有效应用吞吐量尚无专门基准**，这是提交前应补的明确缺口。
