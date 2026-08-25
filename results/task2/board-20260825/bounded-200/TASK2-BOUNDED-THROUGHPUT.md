# Task 2 T2N1 有界吞吐实板结果

测试使用项目自研 T2N1 协议的完整 stop-and-wait 可靠事务：
`CONTROL → ACK`，随后 `STATUS → ACK`。每轮固定 200 个 request；controller 收到最终 STATUS、
发出对应 ACK，并确认最终 CONTROL 的 ACK 已清空自身 pending frame 后才打印 END，因此不依赖
人工停止持续 UART 流。

| 指标 | 三轮结果 |
|---|---:|
| 完整事务 | 600/600 |
| Controller 收到的 ACK | 600/600 |
| 重传 | 0 |
| 协议错误 | 0 |
| 平均吞吐 | 31.834 transactions/s |
| 单轮吞吐范围 | 31.675–32.046 transactions/s |
| 跨轮平均 RTT | 31.408 ms |
| 单轮中位 P50 / P95 / P99 | 30 / 40 / 41 ms |
| 最大 RTT | 131 ms |

吞吐口径是完整可靠控制事务，不是裸 UDP datagram/s。UART 汇聚日志中还会看到 Zephyr peer
对 STATUS 的 ACK 输出；报告中的 `acks=200` 是 controller 状态机每轮独立统计，不混入 peer
日志计数。逐轮机器可读结果为 `fp-rr-run{1,2,3}.json`，汇总为 `summary.json`；统一列口径
另见 `summary.csv`，其中显式记录 platform、topology、scheduler、success/error/
timeout/retransmission、RTT 分位数和有效事务吞吐，便于后续与 QEMU 六场景拼接。
