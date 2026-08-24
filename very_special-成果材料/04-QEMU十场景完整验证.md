# QEMU 十场景完整验证

> **口径说明：**“十场景”不是 OpenCamp 官方规定的数量，而是本项目把三项任务拆成的十个可自动执行行为场景，对应 `scripts/competition/task123.sh suite full`。官网要求的是实时性、网络通信和 AI 控制闭环及其测试数据；本矩阵是我们为覆盖这些要求增加的工程验收手段。

## 当前结论

当前“十场景整套 clean-commit 验收”状态是 **PARTIAL**，不是 PASS。原因是：

- 最新 Task2-only 六场景已在同一套 QEMU suite 中全部通过；
- 最新 Task 3 suite 的 YOLO smoke 和 model-rejected 已通过，当前名称的 `task23-integrated` 也已独立重跑通过；
- 旧 dirty full suite 的单项行为证据仍可审计，但它把 `ci-contracts` 算作十项之一，没有独立 `task2-normal`，且 Task2 故障场景仍加载 Task3 模型；
- 当前十个行为场景仍需在提交后以 `ALLOW_DIRTY=0` 整套重跑，才能宣称 clean full PASS。

## 当前十个行为场景

`ci-contracts` 是独立 gate，不计入下列十个行为场景。

| # | 场景 | Task | 主验目标 | 当前证据状态 |
| ---: | --- | --- | --- | --- |
| 1 | `task3-yolo-smoke` | Task 3 | 真实 AArch64 ncnn/YOLO 推理 | **PASS（dirty）**：当前 Task 3 suite |
| 2 | `task1-scheduler-ab` | Task 1 | 同负载 RR 3 次与 FP-RR 3 次 | **PARTIAL**：旧 dirty 单项 PASS，待 clean 重跑 |
| 3 | `task2-normal` | Task 2 | 无模型 CONTROL -> ACK/STATUS | **PASS**：新 Task2-only suite |
| 4 | `task23-integrated` | Task 2 + 3 | YOLO -> CONTROL -> RTOS STATUS/ACK | **PASS（dirty）**：当前名称独立重跑 |
| 5 | `task2-drop-ack` | Task 2 | 丢首个 ACK、重传与重复抑制 | **PASS**：新 Task2-only suite |
| 6 | `task2-retry-exhausted` | Task 2 | 有界重试、Safe 和恢复 | **PASS**：新 Task2-only suite |
| 7 | `task2-blackout` | Task 2 | 全链路中断、双端 Safe 和恢复 | **PASS**：新 Task2-only suite |
| 8 | `task2-out-of-order` | Task 2 | 乱序 CONTROL 显式拒绝与恢复 | **PASS**：新 Task2-only suite |
| 9 | `task2-invalid-parameter` | Task 2 | CRC 合法但越界参数被拒绝 | **PASS**：新 Task2-only suite |
| 10 | `task3-model-rejected` | Task 3 | 非法模型输出不得发 CONTROL | **PASS（dirty）**：当前 Task 3 suite |

## 独立 gate

| Gate | 目标 | 当前状态 |
| --- | --- | --- |
| `ci-contracts` | Task2/3 Rust/Python 契约、验证器、入口和 Clippy | **PASS（dirty）**，最终仍需 clean 重跑 |

## 最新 Task2-only 六场景证据

证据根：`tmp/competition-task123/current-fix-qemu/20260824T201803Z-suite-task2-2820310/`

| 场景 | T2N1 帧/端 | 语义验证 | 双 pcap | 模型隔离 |
| --- | ---: | --- | --- | --- |
| `task2-normal` | 31 | PASS | PASS | `TASK2_MODEL_ISOLATION_PASS` |
| `task2-drop-ack` | 33 | PASS | PASS | `TASK2_MODEL_ISOLATION_PASS` |
| `task2-retry-exhausted` | 24 | PASS | PASS | `TASK2_MODEL_ISOLATION_PASS` |
| `task2-blackout` | 34 | PASS | PASS | `TASK2_MODEL_ISOLATION_PASS` |
| `task2-out-of-order` | 25 | PASS | PASS | `TASK2_MODEL_ISOLATION_PASS` |
| `task2-invalid-parameter` | 26 | PASS | PASS | `TASK2_MODEL_ISOLATION_PASS` |

`task2-invalid-parameter` 的独立复现证据位于 `tmp/competition-task123/current-fix-qemu/20260824T201725Z-task2-invalid-parameter-2819432/`，其双端均有 28 个 T2N1 帧并通过验证。

Task2-only 验证器除了检查场景标志和 pcap 账本，还会拒绝任何 `TASK3_(MODEL|INFER|DETECTION|EXPERIMENT)` 日志。因此这六个 PASS 是 Task 2 自身的证据。

## 最新 Task 3 与联合闭环证据

- `tmp/competition-task123/current-fix-qemu/20260824T203457Z-suite-task3-2847136/`：YOLO user-mode QEMU 推理 `12124223 us`；model-rejected 观察到 `TASK3_MODEL_REJECTED` 和 `STARRY_T2N1_SAFE`，语义与双 pcap 验证通过。
- `tmp/competition-task123/current-fix-qemu/20260824T204219Z-task23-integrated-2917173/`：Guest 内 3 次 ncnn 推理为 `15815184/16408084/15890595 us`；3 次 CONTROL/ACK/STATUS 完整，双侧各观察到 20 个 T2N1 帧，两个验证器都 PASS。

两组证据都是 dirty source identity，因此只把对应单项标为 PASS，不将整体 clean full 状态提升为 PASS。

## 旧 full 证据的使用边界

旧证据根 `tmp/competition-task123/evidence-worktree/20260824T175618Z-suite-full-2684544/` 保存了完整原始日志、pcap、验证器、TOML、产物哈希、HEAD 和 dirty patch。它仍可支撑 Task1 A/B、YOLO smoke、联合闭环和模型拒绝的历史单项结论，但不得用来声称：

- 当前十个行为场景已在同一套新 full 中通过；
- Task2 六场景在无模型条件下通过；
- 证据对应 clean commit。

## QEMU 假超时根因与修复

之前的 watchdog 失败来自资源过载：两个 `-m 8g` QEMU 在约 15 GiB 内存主机上并发，swap 已用满 `4/4 GiB`，串口进度因内存压力长时间停滞。这是 watchdog 假超时，不是协议失败。

当前运行器通过 `TASK123_QEMU_LOCK_FILE` 和 `TASK123_QEMU_LOCK_TIMEOUT_SEC` 共享 QEMU 执行槽。真实重跑中，另一个 Task1 诊断被正确串行化，随后 Task2 六场景整套 PASS。修复保留了原 watchdog 严格性，没有放宽验收判据。

## 最终 full PASS 条件

提交代码和成果文档后，从 clean worktree 运行：

```bash
scripts/competition/task123.sh build full
scripts/competition/task123.sh suite full
```

只有新证据目录同时包含上述十个行为场景、最终 `TASK123_SUITE_PASS name=full`、clean `git-status.txt`、原始日志、双 pcap 和哈希时，才能把本文的整体状态改为 PASS。
