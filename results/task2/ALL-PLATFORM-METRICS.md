# Task 2 统一机器可读指标

`all-platform-summary.csv` 使用同一 schema 保存当前 QEMU 六类可靠性场景和
ATK-DLRK3588 3×200 有界吞吐基准。

- `bounded-throughput` 行的 request/success/error/timeout/retry、RTT 分位数和
  transaction/s 都来自严格的 BEGIN→END 事务窗口。
- `reliability-scenario` 行用于验证重传、Safe 和恢复，并非饱和基准；没有直接测量的
  request 数、分位数或 throughput 保持空值，禁止用 pcap 帧数填充。
- QEMU normal 的 3 个闭环 RTT 范围为 45–338 ms、均值 152 ms；故障场景的 max
  字段只在文档明确给出“恢复请求 RTT”时记录该值。
- `errors=0` 表示没有非预期应用错误；故障注入所期待的 OutOfOrder、
  InvalidParameter 或 RetryExhausted 属于场景输入/预期状态转换，不改写成实验失败。

CSV 的 `evidence` 是仓库相对路径或被最终证据 manifest 索引的历史证据路径。
QEMU 六场景当前仍是 dirty-worktree PASS，最终 clean suite 完成前不得写成最终 PASS。
