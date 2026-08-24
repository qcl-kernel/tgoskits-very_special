# Task 3：推理控制设计与结果

## 任务思路：AI 负责感知，RTOS 保留最终控制权

赛题要求的不是在 Guest 中“跑一下模型”，而是形成 AI 输入→推理→跨 Guest 网络→RTOS 动作→状态回传的闭环。我们的边界是：

- StarryOS 负责图像、模型推理和把检测结果转换成候选控制目标；
- 安全层检查置信度、面积、坐标、有限值和单步变化，非法或过期结果不得产生 CONTROL；
- Zephyr 是唯一控制执行者，根据 T2N1 CONTROL 更新虚拟 plant/控制状态，并回传 ACK 与 STATUS；
- AI 改善感知和控制决策，不允许绕过 Task 2 协议直接操作 RTOS，也不宣称 FP-RR 会加速张量计算。

## 实际硬件与软件栈

| 层次 | 实际配置 |
| --- | --- |
| 开发板 | 正点原子 ATK-DLRK3588，Rockchip RK3588 |
| Hypervisor | AxVisor，RR 或 bounded FP-RR；最终拓扑使用 FP-RR |
| AI Guest | 2-vCPU StarryOS；vCPU0/pCPU2 为图像/NPU/后处理，vCPU1/pCPU1 为 T2N1 通信 |
| RTOS Guest | Zephyr；vCPU0/pCPU1，优先级 90，高于 StarryOS 的 89 |
| 加速器 | RK3588 三核 NPU，设备节点 `/npu@fdab0000`，SPI 110–112，经 IOMMU/时钟/复位/电源域完整直通且只归 StarryOS |
| 实板模型路径 | YOLOv8 风格检测模型，经 RKNN runtime 在 RK3588 NPU 执行；CPU 完成解码、letterbox、颜色转换和 NMS |
| QEMU 替身 | StarryOS AArch64 Guest 内静态 ncnn + YOLO11n，使用 CPU 推理；用于可复现语义验证，不代表 NPU 性能 |
| 网络闭环 | T2N1/UDP/IPv4：CONTROL→Zephyr ACK/STATUS |

板卡资源所有权、构建和 RAM-only 启动见 [`docs/design/atk-dlrk3588-npu-hybrid.md`](../docs/design/atk-dlrk3588-npu-hybrid.md)。

## 物理板主路

同一个 2-vCPU StarryOS Linux Guest 内，vCPU0 负责图像预处理、RKNN/NPU 提交和后处理，vCPU1 负责 T2N1 通信。RK3588 NPU 独占分配给 StarryOS；Zephyr 是唯一 RTOS Guest，负责控制执行和 STATUS 回传。

```text
图像 -> StarryOS vCPU0 -> RKNN/RK3588 NPU -> Detection -> 校验/限幅
                                                        |
                                                        v
StarryOS vCPU1 -> T2N1 CONTROL -> Zephyr -> ACK + STATUS -> StarryOS vCPU1
```

检测结果必须通过置信度、面积、坐标、有限值和单步目标变化限制，才能转成 CONTROL。低置信度、面积过小、畸形/非有限输出、越界或超时均不得向 RTOS 发送 CONTROL。

## QEMU 可移植路径

QEMU 中没有 RK3588 NPU，因而在 StarryOS AArch64 Guest 内用静态 ncnn 读取 PPM，执行 YOLO11n 预处理、CPU 张量推理和后处理。这条路径证明真实模型调用、安全策略和双 Guest 闭环，不代表 RKNN/NPU 性能。

当前 Task 3 场景边界是：

- `task3-yolo-smoke`：当前 AArch64 ncnn 产物对固定输入执行真实 YOLO；
- `task23-integrated`：真实 YOLO -> CONTROL -> Zephyr ACK/STATUS，并保存双 pcap；
- `task3-model-rejected`：注入非法模型输出，必须在 CONTROL 前被拒绝并进入定义的 Safe 路径。

`suite task3` 只运行 `task3-yolo-smoke` 和 `task3-model-rejected`。联合正常闭环需要显式运行 `task23-integrated`，或通过 `suite acceptance/full` 覆盖。

## 当前证据状态

| 证据 | 状态 | 边界 |
| --- | --- | --- |
| 当前 QEMU `suite task3` | **PASS（dirty）** | `task3-yolo-smoke` 真实推理 `infer_us=12124223`；`task3-model-rejected` 在 CONTROL 前进入 Safe，双 pcap 验证通过 |
| 当前 QEMU `task23-integrated` | **PASS（dirty）** | 3 次 Guest 内推理为 `15815184/16408084/15890595 us`，CONTROL -> Zephyr ACK/STATUS 和双 pcap 均通过 |
| 旧 QEMU `task3-yolo-smoke` / `task23-normal` / `task3-model-rejected` | **PARTIAL / 历史 PASS** | 单场景判据通过，但生成于改名和 Task2/Task3 隔离之前，不能冒充当前 clean full |
| 当前分支实板 FP-RR | **PARTIAL** | 三轮有 `TASK3_EXPERIMENT_COMPLETE events=12 statuses=12` |
| 当前分支实板 RR | **FAIL** | 三轮均有 `TASK2_ERROR=RKNN event sequence does not match the frozen scene` |
| 当前分支实板 Task3 完整矩阵 | **PARTIAL** | 尚未完成正常态、模型拒绝、故障恢复和 clean-commit 重跑 |

[`results/atk-dlrk3588-task123-integrated-ab-20260824/README.md`](../results/atk-dlrk3588-task123-integrated-ab-20260824/README.md) 的 CPU ncnn 快速闭环和 [`results/atk-dlrk3588-npu-hybrid-20260824/README.md`](../results/atk-dlrk3588-npu-hybrid-20260824/README.md) 的 fixed/RKNN 3+3 都是迁移前物理板归档。其中旧配置的 vCPU 编号或 RTOS 类型可能与当前最终拓扑不同，不得作为当前分支的拓扑定义。

当前 QEMU Task 3 suite 证据根是 `tmp/competition-task123/current-fix-qemu/20260824T203457Z-suite-task3-2847136/`；联合闭环证据根是 `tmp/competition-task123/current-fix-qemu/20260824T204219Z-task23-integrated-2917173/`。两者都记录了 dirty source identity，不等于 clean full 证据。

## 对比一：固定参数手动控制 vs 真实 YOLO（实板快速闭环）

两臂使用相同五张冻结图片、Guest、FP-RR、T2N1 和 Zephyr plant。manual 固定发送 `target=500`，不运行模型；YOLO 对每张图片执行真实 ncnn，左/中/右三张接受，无目标与小目标两张应安全拒绝。

| 指标 | manual 固定参数 | YOLO | 解释 |
| --- | ---: | ---: | --- |
| 完整 CONTROL/STATUS | 5/5 | 3/3 可接受图片 | 两臂闭环均完整；YOLO 另拒绝 2 张 |
| 发送目标 MAE | 93.00 | **26.33** | YOLO 更接近图片真值，仍受单步限幅 |
| CONTROL→STATUS 平均 RTT | 250.4 ms | **219.0 ms** | 本次快速实板观测值 |
| plant 单步状态 MAE | **70.67** | 89.00 | 单步受 plant 惯性影响，不能当稳态性能 |
| 预期接受/拒绝正确率 | 不适用 | **5/5** | 两张危险输入未发 CONTROL |
| 平均推理时间 | 不适用 | 1.622 s | 该组使用 CPU ncnn，不是 NPU 性能 |

证据：[`results/atk-dlrk3588-task123-integrated-ab-20260824/`](../results/atk-dlrk3588-task123-integrated-ab-20260824/)。这是 manual 1 次 + YOLO 1 次的快速物理板验证，不应冒充 3+3 正式统计。

## 对比二：固定感知 vs RKNN/NPU（实板 3+3）

迁移前物理板归档在同一 12-event 场景、FP-RR 拓扑、Zephyr 和 T2N1 下各运行 3 次：

| 每臂三次汇总 | fixed-perception | RKNN/NPU |
| --- | ---: | ---: |
| 每轮正确动作 | 8/12 | **12/12** |
| hazard recall | 0% | **100%** |
| vehicle recall | 不适用 | **100%** |
| CONTROL→STATUS RTT | 68.278 ± 2.164 ms | 162.472 ± 9.155 ms |
| input/inference-start→STATUS | 68.285 ± 2.124 ms | 273.215 ± 2.544 ms |
| 完整模型推理 | 不适用 | 46.389 ± 0.091 ms |

这组对比展示的是取舍而不是“AI 所有指标都更好”：RKNN/NPU 把动作正确率从 66.7% 提高到 100%，并识别固定策略无法识别的 hazard；代价是图像处理、调度、通信与状态回传带来更高端到端延迟。证据：[`results/atk-dlrk3588-npu-hybrid-20260824/`](../results/atk-dlrk3588-npu-hybrid-20260824/)。它是语义迁移前的历史实板证据，当前分支仍需 clean 复测。

## 对比三：合法模型输出 vs 非法模型输出

- `task23-integrated`：真实 YOLO 输出通过校验后，3/3 完成 CONTROL→ACK/STATUS；Guest 推理分别为 15.815/16.408/15.891 s（QEMU TCG）。
- `task3-model-rejected`：注入畸形或越界模型输出，必须出现 `TASK3_MODEL_REJECTED` 和 Safe 标志，并且在拒绝点前后不发送对应 CONTROL；心跳仍存在，证明是业务安全拒绝而不是 Guest 崩溃。

这项 A/B 直接验证“模型输出不能绕过安全边界”。

## 官方 25 分评分点对应

| 官方细则 | 当前对应内容 | 状态 |
| --- | --- | --- |
| Starry/Linux Guest 神经网络推理（4） | 当前 QEMU ncnn/YOLO11n；历史实板 RKNN/RK3588 NPU | 已覆盖；当前分支实板需复测 |
| 模型输出经 Task 2 协议发送（5） | 安全校验后的目标编码为 T2N1 CONTROL | 已覆盖 |
| RTOS 执行可观察动作（5） | Zephyr 更新虚拟 plant/状态并打印动作日志 | 已覆盖 |
| 完整闭环（4） | 输入→推理→CONTROL→ACK/STATUS→量化器 | QEMU 当前单项 PASS；实板矩阵 PARTIAL |
| 端到端延迟（3） | 同侧单调时钟测 inference-start→STATUS 与 CONTROL→STATUS | 已覆盖方法和数据 |
| 固定参数基线至少两指标（4） | manual vs YOLO 的目标 MAE/RTT/安全拒绝；fixed vs RKNN 的动作正确率/recall/延迟 | 已覆盖历史对比；正式提交宜从 clean commit 复测 3+3 |
