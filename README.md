# OpenRace 2026：基于 TGOSKits 的智能实时控制系统

本仓库是 OpenRace 2026 Task 1–3 的完整成果仓库。项目基于 TGOSKits，在
ATK-DLRK3588（RK3588）物理板上组合 AxVisor、StarryOS、Zephyr 与 RKNN/NPU，
形成从实时调度、跨 Guest 通信到目标检测和安全控制的完整闭环。

## 项目成果

| 任务 | 实现内容 | 最终结果 |
| --- | --- | --- |
| Task 1：实时调度 | AxVisor bounded FP-RR、优先级抢占与共享 CPU 调度 | 压力场景 P99 从 `0.621 ms` 降至 `0.278 ms`，降低 `55.3%` |
| Task 2：可靠通信 | StarryOS 与 Zephyr 通过 VirtIO-net/T2N1 传递 CONTROL、ACK 和 STATUS | 3 轮共 `600/600` 个完整事务，0 重传、0 协议错误 |
| Task 3：AI 控制 | RK3588 NPU 执行 RKNN/YOLO 推理，安全策略执行 Stop、Confirm、Reset 和 Resume | 正式实板固定基线正确率 `66.7%`，RKNN 组 `100%` |
| CARLA 综合仿真 | 五类交通场景分别运行固定基线和 YOLO 闭环 | 固定基线 `5/5` 碰撞，YOLO 闭环 `5/5` 零碰撞 |

完整成果说明见 [成果材料总览](very_special-成果材料/README.md)。

## 整体架构

```text
image / camera
      |
      v
StarryOS vCPU0 on pCPU2
preprocess -> RKNN -> RK3588 NPU -> postprocess / safety decision
                                             |
                                             v
StarryOS vCPU1 on pCPU1 -- T2N1 CONTROL --> Zephyr vCPU0 on pCPU1
                          <-- ACK / STATUS --
```

- StarryOS vCPU0 绑定 pCPU2，负责图像预处理、NPU 推理、后处理和安全决策；
- StarryOS vCPU1 绑定 pCPU1，负责 VirtIO-net、T2N1 和控制通信；
- Zephyr vCPU0 同样绑定 pCPU1，执行 10 ms 周期实时任务和 CONTROL 处理；
- RK3588 NPU 仅透传给 StarryOS；
- 正式调度优先级为 Zephyr 90、StarryOS 89。

该布局让 AI 推理独占 pCPU2，同时让通信 vCPU 与 RTOS 在 pCPU1 上形成真实的
调度竞争，用 Task 1 的 FP-RR 调度保证 Task 2/3 控制链的实时性。

## Task 1：bounded FP-RR 实时调度

Task 1 先用 CPU 亲和性把 AI 重负载隔离到 pCPU2，再在 pCPU1 上调度通信与
RTOS。普通 RR 不理解 RTOS 的 10 ms deadline；严格固定优先级又可能让
StarryOS 通信 vCPU 长期得不到执行，导致 CONTROL/STATUS 闭环饿死。因此项目
实现 bounded FP-RR：

```text
pCPU2  +-------------------------------------------------------+
       | StarryOS vCPU0：预处理 -> RKNN/NPU -> 后处理          |
       +-------------------------------------------------------+
                              与实时共享核隔离

pCPU1  +---------------------------+   +-----------------------+
       | StarryOS vCPU1            |   | Zephyr vCPU0          |
       | VirtIO-net / T2N1         |   | 10 ms RTOS 工作       |
       | priority 89               |   | priority 90           |
       +---------------------------+   +-----------------------+
                    \                 /
                     +---------------+
                     | bounded FP-RR |
                     +-------+-------+
                             |
          +------------------+-------------------+
          |                  |                   |
   高优先级刚唤醒      同优先级 runnable    有界服务窗口到期
          |                  |                   |
          v                  v                   v
   立即抢占并运行       按 RR 公平轮转      让通信 vCPU 前进
   Zephyr vCPU          同优先级工作        防止控制链饿死
```

调度触发点覆盖 timer/vIRQ 唤醒和 IRQ-tail；vIRQ 使用有界队列与 retry slot，
避免 GIC List Register 暂不可用时静默丢失中断。正式通信共核实板 A/B 中，
P99 从 `0.621 ms` 降至 `0.278 ms`，同时通信和 RTOS 均持续存活。

## Task 2：UDP 上的 T2N1 可靠控制协议

两个 Guest 各自拥有独立的 VirtIO-net、virtqueue、DMA 区域、stage-2 映射和
vIRQ，经 AxVisor 内部 L2 switch 交换 UDP/IPv4 数据报。UDP 保留消息边界但不
提供可靠性，因此项目在其上设计 T2N1 协议。一个 UDP datagram 只承载一个
T2N1 frame，多字节字段统一使用网络字节序。

### T2N1 固定头布局

```text
字节偏移
0                   4   5   6       8          12         16         20  22      24         28
+-------------------+---+---+-------+----------+----------+----------+---+-------+----------+
| magic = "T2N1"    |ver|kind| flags | session  | sequence |   ack    |len| error |  crc32   |
|       4 B         |1 B|1 B |  2 B  |   4 B    |   4 B   |   4 B   |2 B|  2 B  |   4 B    |
+-------------------+---+---+-------+----------+----------+----------+---+-------+----------+
|<-------------------------------- 固定头：28 B ------------------------------------->|
|<--------------------------- payload：0..1200 B ----------------------------------->|
```

| 偏移 | 字段 | 作用 |
| ---: | --- | --- |
| 0–3 | `magic` | 固定为 ASCII `T2N1`，识别协议 |
| 4 | `version` | 当前协议版本 1 |
| 5 | `kind` | CONTROL、STATUS、ERROR、ACK 或 HEARTBEAT |
| 6–7 | `flags` | bit 0 表示可靠帧 |
| 8–11 | `session` | 区分通信会话，拒绝错误会话 |
| 12–15 | `sequence` | CONTROL/STATUS 的可靠序号 |
| 16–19 | `acknowledgement` | 指向已确认或被拒绝的序号 |
| 20–21 | `payload_len` | payload 长度，最大 1200 B |
| 22–23 | `error_code` | 参数、乱序、消息类型或会话错误 |
| 24–27 | `crc32` | 覆盖固定头和 payload 的 IEEE CRC-32 |

CONTROL 和 STATUS 均使用 12 B 类型化 payload：CONTROL 携带
`action/value/request_id`，STATUS 返回 `state/value/last_request_id`。ACK 没有
payload，通过头部 `acknowledgement` 精确确认可靠帧。

### 一次完整可靠事务

```text
StarryOS controller                                      Zephyr executor
        |                                                       |
        |  UDP/T2N1 CONTROL(seq=N, request=R)                   |
        |------------------------------------------------------>|
        |                                  CRC/序号/参数校验     |
        |                                  去重后执行控制动作     |
        |  ACK(ack=N)                                          |
        |<------------------------------------------------------|
        |  STATUS(seq=M, last_request=R, state/value)           |
        |<------------------------------------------------------|
        |  ACK(ack=M)                                          |
        |------------------------------------------------------>|
        |                                                       |
        +---------------- transaction complete -----------------+
```

CONTROL/STATUS 采用 stop-and-wait：500 ms 未收到 ACK 就重传，最多重传 5 次；
重复帧只重新 ACK、不重复执行；超前序号明确返回 OutOfOrder。双向心跳每 200 ms
发送一次，5 s 内没有合法入站帧或重试耗尽时进入 Safe；链路恢复后从 sequence 1
重新同步。正式实板完成 3×200 个四步事务，`600/600` 成功。

## Task 3：NPU 感知到 RTOS 执行的完整闭环

Task 3 不让 AI 直接控制执行器。StarryOS vCPU0 使用 RKNN/NPU 完成感知，安全门
检查置信度、目标面积、坐标、有限值、时效性和单步变化；只有合法决策才能交给
StarryOS vCPU1。随后必须经过 Task 2 的可靠协议，由 Zephyr 执行并返回状态。

```text
真实图像 / CARLA 代表帧
            |
            v
+-------------------------- StarryOS vCPU0 / pCPU2 --------------------------+
| 预处理 -> RKNN 提交 -> RK3588 NPU -> 后处理/NMS -> 检测结果               |
|                                                     |                      |
|                                          +----------v----------+           |
|                                          | 安全校验与状态机     |           |
|                                          | Track/Stop/Reset     |           |
|                                          +----------+----------+           |
+-----------------------------------------------------|-----------------------+
                                                      | 合法候选控制
                                                      v
+-------------------------- StarryOS vCPU1 / pCPU1 --------------------------+
| T2N1 controller：编码 CONTROL、等待 ACK、匹配 request_id 与 STATUS         |
+--------------------------------------+-------------------------------------+
                                       | Task 2：VirtIO-net + UDP/T2N1
                                       v
+------------------------------ Zephyr vCPU0 / pCPU1 ------------------------+
| 唯一控制执行者：SetOutput / Stop 锁存 / 显式 Reset                         |
| 10 ms 周期任务 -> plant/state -> ACK/STATUS 返回                           |
+-----------------------------------------------------------------------------+
             ^                                           |
             +------ Task 1 bounded FP-RR 调度保障 -------+
```

三项任务的关系不是简单拼接：

- **Task 1** 保证共享 pCPU1 上 Zephyr 能及时响应，同时通信 vCPU 不被饿死；
- **Task 2** 保证候选控制通过可校验、可确认、可重传、可恢复的网络链路传输；
- **Task 3** 使用 NPU 提升感知能力，通过安全门产生控制决策，并由 RTOS 保留最终
  执行权。

危险目标触发 Stop 后由 Zephyr 保持 Stopped，即使后续画面恢复正常也不会自动
解锁；只有显式 Reset 完成可靠事务后才恢复 Active。这使模型误检、过期结果、
链路故障或 Guest 调度竞争都不能绕过安全边界直接驱动执行器。

## CARLA 五场景 A/B 仿真

项目最终使用 **CARLA 0.9.16** 构建道路、车辆、行人、天气、车载相机和碰撞
传感器。每个场景使用相同的道路和障碍物条件，分别运行两种控制方式：

- **固定基线**：车辆保持固定控制输出，不根据前方目标避让；
- **YOLO 闭环**：识别车辆和行人，执行减速、Stop、2 秒 Confirm、Reset，随后
  恢复行驶并继续运行约 6 秒。

| 场景 | 固定基线 | YOLO 闭环 |
| --- | --- | --- |
| 晴天直路静止汽车 | 碰撞 | 安全停车、Reset、恢复，零碰撞 |
| 斑马线行人横穿 | 碰撞 | 零碰撞，最小行人净距 `2.223 m` |
| 前车切入并制动 | 碰撞 | 跟踪并停车，危险解除后恢复，零碰撞 |
| 货车遮挡行人 | 碰撞 | 零碰撞，最小行人净距 `14.104 m` |
| 多车多人复杂路口 | 碰撞 | 零碰撞，最小行人净距 `2.217 m` |

五场景代表帧还在真实 RK3588 上完成了 RKNN/NPU 推理与 T2N1 通信验证：共
15 张图、20 次状态返回，NPU 平均运行时间 `18.13 ms`，完整推理流水线平均
`62.60 ms`，验证结果为 PASS。CARLA 负责交通行为仿真，视频中显示的 NPU、
CONTROL/STATUS 和 Reset 数据来自真实板卡运行；项目不把主机仿真数据冒充为
逐帧实时上板数据。

**五个场景、十个独立 A/B 视频使用同一个 B站多 P 链接：**

https://www.bilibili.com/video/BV11XhF6REHU

## 成果与证据入口

- [总体架构与设计](very_special-成果材料/00-总体架构与设计.md)
- [Task 1 实时调度设计与结果](very_special-成果材料/01-Task1-实时调度设计与结果.md)
- [Task 2 双 Guest 通信设计与结果](very_special-成果材料/02-Task2-双Guest通信设计与结果.md)
- [Task 3 推理控制与 CARLA 仿真结果](very_special-成果材料/03-Task3-推理控制设计与结果.md)
- [复现与配置指南](very_special-成果材料/05-复现与配置指南.md)
- [证据与演示索引](very_special-成果材料/06-证据与演示索引.md)
- [任务要求与实现覆盖](very_special-成果材料/07-任务要求与实现覆盖.md)
- [CARLA 五场景代码、JSON、板卡日志与校验清单](results/task3/carla-five/)

证据目录保存仿真逐帧 JSON、真实板卡 NPU/通信日志、冻结配置及 SHA-256
校验清单。视频统一发布到上述 B站多 P 稿件，不在 Git 中重复保存 MP4。

## 快速复现

所有命令从仓库根目录执行：

```bash
scripts/competition/task123.sh prepare
scripts/competition/task123.sh doctor
scripts/competition/task123.sh build full

scripts/competition/task123.sh suite task1
scripts/competition/task123.sh suite task2
scripts/competition/task123.sh suite task3
```

Task 2 与 Task 3 的联合闭环可运行：

```bash
scripts/competition/task123.sh run task23-integrated
```

实板启动、依赖、参数和成功标志以
[复现与配置指南](very_special-成果材料/05-复现与配置指南.md)为准。

## 项目来源与许可

本成果基于 [TGOSKits](https://github.com/rcore-os/tgoskits) 开发，复用其中的
ArceOS、StarryOS、AxVisor、组件、驱动和统一构建基础设施。仓库整体采用
[Apache-2.0](LICENSE) 许可证；子项目如有独立许可证，以对应目录内文件为准。
