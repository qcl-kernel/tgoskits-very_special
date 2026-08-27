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
| AI Guest | 2-vCPU 类 Linux StarryOS；vCPU0/pCPU2 为图像/NPU/后处理，vCPU1/pCPU1 为 T2N1 通信 |
| RTOS Guest | Zephyr；vCPU0/pCPU1，优先级 90，高于 StarryOS 的 89 |
| 加速器 | RK3588 三核 NPU，设备节点 `/npu@fdab0000`，SPI 110–112，经 IOMMU/时钟/复位/电源域完整直通且只归 StarryOS |
| 实板模型路径 | YOLO 检测模型，经 RKNN runtime 在 RK3588 NPU 执行；CPU 完成解码、letterbox、颜色转换和 NMS |
| QEMU 替身 | StarryOS AArch64 Guest 内静态 ncnn + YOLO11n，使用 CPU 推理；用于可复现语义验证，不代表 NPU 性能 |
| 网络闭环 | T2N1/UDP/IPv4：CONTROL→Zephyr ACK/STATUS |

板卡资源所有权、构建和 RAM-only 启动见 [`docs/design/atk-dlrk3588-npu-hybrid.md`](../docs/design/atk-dlrk3588-npu-hybrid.md)。

## 三种推理路径各自证明什么

系统实际运行过三种模型路径。它们的目的不同，不能把耗时直接拼成同模型、
同输入、同拓扑的加速比。

| 路径 | 推理执行位置 | 场景与用途 | 实测效果 | 在系统中的定位 |
| --- | --- | --- | --- | --- |
| RK3588 实板 RKNN/NPU | RKNN Runtime 调用 RK3588 NPU | 厂区 AGV 跟车、行人急停、Stop 锁存和 Reset；验证正式物理架构 | 当前正式实验的 RKNN 推理中位数 `50.1 ms`；动作正确率 `100%`，hazard recall `100%` | **正式主线** |
| RK3588 实板 ncnn/CPU | StarryOS Guest 内 AArch64 静态 ncnn，使用 CPU | 五张冻结图片的目标位置控制；验证真实图片→YOLO→安全门→T2N1→RTOS plant 的早期实板闭环 | 平均推理 `1.622 s`；三张有效图片原始感知 MAE `0`，两张危险输入均被拒绝；接受样本 CONTROL→STATUS 平均 `219.0 ms` | **CPU 可行性与安全参考，不是正式 NPU 架构** |
| QEMU ncnn/CPU | StarryOS AArch64 Guest 在 QEMU TCG CPU 上运行静态 ncnn | 无 NPU 环境下复现真实模型调用、协议闭环、模型拒绝和故障注入 | smoke 推理 `12.124 s`；完整 Guest 闭环三次推理 `15.815/16.408/15.891 s`，CONTROL/ACK/STATUS 与双 pcap 闭合 | **可移植功能替身，不是性能基准** |

实板 ncnn 实验使用的是 COCO 花瓶/花盆类目标位置场景和 RTOS 虚拟 plant，
不是当前 AGV 的 car/person 连续安全场景。它的价值是较早证明 StarryOS 中确实
运行了神经网络，并且模型输出能经过安全校验和 Task 2 网络驱动 RTOS；当前正式
物理演示则由 RKNN/NPU 路径承担。

从绝对耗时看，实板 CPU ncnn 的 `1.622 s` 明显高于当前 RKNN/NPU 的约
`50.1 ms`，QEMU TCG 又更慢。但三组实验使用的模型格式、图片、场景和系统配置
并不完全相同，因此只能说明 NPU 主线更适合实时实板部署，不能声称为严格的
`32.4x` 受控加速测试。

### 为什么正式架构选择 NPU，而不选择 CPU+ncnn

现有两组实板观测中，CPU+ncnn 平均推理约 `1.622 s`，RKNN/NPU 推理中位数约
`50.1 ms`，呈现约 `32.4x` 的量级差异，对应单次推理耗时约降低 `96.9%`。
这不是同模型、同图片和同软件栈下的严格加速比，但足以支持以下工程选择：

1. **连续感知能力**：秒级 CPU 推理只能低频更新控制目标；约 50 ms 的 NPU
   推理更适合连续图片输入和危险目标检测。
2. **减少 CPU 竞争**：NPU 承担张量计算后，StarryOS CPU 主要处理图片预处理、
   输出解码、NMS、安全状态机和 T2N1 通信，不再长时间执行卷积计算。
3. **保护实时共享域**：正式拓扑把 AI vCPU 放在 pCPU2，把通信 vCPU 与 Zephyr
   放在 pCPU1；NPU进一步缩短 AI 侧 CPU burst，避免为了得到更大的调度改善数字
   而人为让 AI 计算长期挤占 RTOS 所在 CPU。
4. **保留可移植替身**：ncnn 仍然有价值。实板 CPU 路径证明无 NPU 时完整闭环
   仍能工作，QEMU 路径让没有 RK3588 的环境也能复现真实神经网络与安全语义。

因此系统不是“删除 ncnn、只剩 NPU”，而是明确分层：RKNN/NPU 是正式物理板
执行路径，ncnn/CPU 是实板兼容参考和 QEMU 可移植验证路径。

## 物理板主路

同一个 2-vCPU 类 Linux StarryOS Guest 内，vCPU0 负责图像预处理、RKNN/NPU
提交和后处理，vCPU1 负责 T2N1 通信。RK3588 NPU 独占分配给
StarryOS；Zephyr 是唯一 RTOS Guest，负责控制执行和 STATUS 回传。

```text
                         类 Linux StarryOS Guest
                 +------------------------------------+
真实图像 -------->| vCPU0 / pCPU2                     |
                 | 预处理 -> RKNN -> RK3588 NPU      |
                 |          -> 检测 -> 安全校验/限幅 |
                 |                       |            |
                 |                       v            |
                 | vCPU1 / pCPU1：T2N1 controller  |
                 +-----------------------+------------+
                                         |
                                  CONTROL|  UDP/IP + T2N1
                                         v
                 +------------------------------------+
                 | Zephyr RTOS vCPU0 / pCPU1          |
                 | 执行 SetOutput / Stop / Reset       |
                 | 10 ms 周期任务 -> plant/state       |
                 +-----------------------+------------+
                                         |
                              ACK/STATUS |
                                         +-----> StarryOS vCPU1
```

这个拆分是有意的：AI 只提供带不确定性的感知和候选决策，不拥有执行器；
Zephyr 保留最终控制状态、Stop 锁存和 Reset 解锁权。即使模型输出错误、过期
或越界，也不能绕过 Task 2 的可靠协议直接改写控制状态。

## 演示所对应的物理故事

完整系统对应“厂区自动物流 AGV 的视觉跟车与行人安全制动”，不是
“AI 控制一个抽象数字”。

```text
前车距离变化
      |
      v
YOLO/RKNN 检测 car ----> 跟车目标 ----> T2N1 SetOutput ----> Zephyr 调整驱动状态

行人进入受限通道
      |
      v
YOLO/RKNN 检测 person -> hazard ----> T2N1 Stop -------> Zephyr 锁存 Stopped
                                                                   |
危险消失、后续又看到车 -----------------------------------+-- 仍保持停车
                                                                   |
操作员显式 Reset -------> T2N1 Reset ------------------------------+-- 解除锁存
                                                                      恢复跟车
```

因此演示必须同时呈现真实图像、检测结果、固定基线与 RKNN/NPU 决策、
T2N1 事务以及 Zephyr 状态；不用一个脱离画面语义的数字代替整条因果链。

这里串口不在业务数据面中。UART2/1.5 Mbaud 只承担 U-Boot RAM 启动、AxVisor
shell、Guest console 汇聚和证据采集；真正的 CONTROL/ACK/STATUS 走两个 Guest 的
VirtIO-net 和 T2N1/UDP/IP。即使演示画面从 UART 日志提取数字，也不能写成“串口完成
跨 Guest 控制”。

检测结果必须通过置信度、面积、坐标、有限值和单步目标变化限制，才能转成 CONTROL。低置信度、面积过小、畸形/非有限输出、越界或超时均不得向 RTOS 发送 CONTROL。

## QEMU 可移植路径

QEMU 中没有 RK3588 NPU，因而在 StarryOS AArch64 Guest 内用静态 ncnn 读取 PPM，执行 YOLO11n 预处理、CPU 张量推理和后处理。这条路径证明真实模型调用、安全策略和双 Guest 闭环，不代表 RKNN/NPU 性能。

当前 Task 3 场景边界是：

- `task3-yolo-smoke`：当前 AArch64 ncnn 产物对固定输入执行真实 YOLO；
- `task23-integrated`：真实 YOLO -> CONTROL -> Zephyr ACK/STATUS，并保存双 pcap；
- `task3-model-rejected`：注入非法模型输出，必须在 CONTROL 前被拒绝并进入定义的 Safe 路径。

`suite task3` 只运行 `task3-yolo-smoke` 和 `task3-model-rejected`。联合正常闭环需要显式运行 `task23-integrated`，并写入独立证据目录。

## 已完成验证

| 证据 | 已验证内容 |
| --- | --- |
| QEMU `suite task3` | `task3-yolo-smoke` 真实推理 `infer_us=12124223`；`task3-model-rejected` 在 CONTROL 前进入 Safe，双 pcap 对账通过 |
| QEMU `task23-integrated` | 3 次 Guest 内推理为 `15815184/16408084/15890595 us`，CONTROL -> Zephyr ACK/STATUS 和双 pcap 完整 |
| 实板固定基线/RKNN 对比 | 两组各三轮，六轮均完成 12/12 CONTROL→STATUS；RKNN 决策、vehicle recall 和 hazard recall 均为 100% |
| 实板安全路径 | hazard 触发 Stop 并锁存，显式 Reset 后才恢复 SetOutput；六轮无 panic、ESR、segfault 或 ACK timeout |

QEMU 证据位于 `tmp/competition-task123/current-fix-qemu/`，实板证据位于
`results/task3/board-20260825/`。公开结论只使用上表的当前正式架构结果。

## 实板固定基线组与 RKNN/NPU 组：各三轮

固定基线组和 RKNN 组的 controller、payload 与 FIT 均由当前源码构建。两组
使用冻结的正式架构：AI/RKNN vCPU0→pCPU2，通信 vCPU1→pCPU1，
Zephyr→pCPU1，NPU 只直通 StarryOS。

两组同为 FP-RR，使用同一 Zephyr binary、T2N1、12-event 清单、CPU 映射、
优先级和板级 DTB；唯一 A/B 变量是 fixed perception 或 RKNN/NPU 感知来源。
每组独立运行 3 次，每轮必须精确闭合 12 CONTROL 和 12 STATUS。

| 指标 | fixed perception | RKNN/NPU |
| --- | ---: | ---: |
| 完整轮次 | 3/3 | 3/3 |
| 每轮正确场景决策 | 8/12（66.7%） | 12/12（100%） |
| vehicle recall | N/A | 100% |
| hazard recall | 0% | 100% |
| 3 轮共完整 CONTROL→STATUS | 36/36 | 36/36 |
| 每轮平均 CONTROL→STATUS RTT 中位数 | 35.6 ms | 57.8 ms |
| 单轮平均 RTT 范围 | 34.7–37.2 ms | 55.5–62.0 ms |
| 每轮平均 inference-start→STATUS 中位数 | 35.6 ms | 219.9 ms |
| 每轮平均 RKNN inference 中位数 | N/A | 50.1 ms |
| protocol error / ACK timeout / panic / ESR / segfault | 0 | 0 |

证据根：[`results/task3/board-20260825/`](../results/task3/board-20260825/)。
这组数据同样体现真实取舍：RKNN 把安全决策正确率提升到 100%，但增加了图像推理、
发布/轮询、调度和网络闭环时间；不能声称 AI 让所有延迟指标也变好。

## 从零复现实板 Task 3

### 1. 准备固定基线组和 RKNN 组 payload

```bash
export BUSYBOX_STATIC=/path/to/aarch64/busybox
export TASK2_BINARY=/path/to/matching/task2-net
export RKNN_BUNDLE=/path/to/rknn-bundle

scripts/task3/build-hybrid-scene-payload.sh \
  fixed tmp/task3-demo/fixed.cpio
scripts/task3/build-hybrid-scene-payload.sh \
  rknn tmp/task3-demo/rknn.cpio
```

RKNN bundle 必须包含 benchmark、RKNN 模型、labels、glibc/RKNN runtime 和
冻结图片；构建器逐一检查架构、接口 marker、图片 SHA-256，并给 payload 生成哈希。

### 2. 用同一拓扑构建 RR/FP-RR FIT

```bash
STARRY_INITRD="$PWD/tmp/task3-demo/rknn.cpio" \
  scripts/board/build-atk-zephyr-task123-unified.sh \
  tmp/task3-demo/rknn-board
```

fixed 臂只替换 `STARRY_INITRD` 和输出目录；StarryOS/Zephyr CPU 映射、调度器、
Zephyr binary、T2N1 和 12-event 清单保持不变。

### 3. RAM-only 启动并采集

```bash
ATK_LOG="$PWD/tmp/task3-demo/rknn-run1.log" \
ATK_READY_REGEX='TASK3_HYBRID_SCENE_END source=rknn' \
ATK_READY_TIMEOUT=240 \
  scripts/board/atk-dlrk3588-ram-boot.sh \
  tmp/task3-demo/rknn-board/axvisor-task123-zephyr-fp-rr.fit
```

fixed 臂把 ready regex 改为 `source=fixed`，并启动 fixed FIT。启动器只执行
`fastboot stage` 和 RAM boot；禁止 flash/erase。每轮接受条件是最新完整
`TASK3_HYBRID_SCENE_BEGIN`→`END` 窗口、12 条关联链、无 panic/ESR/segfault/
协议错误，不是“看见几行检测输出”。

### 4. 量化 A/B

```bash
python3 scripts/task3/quantify-hybrid-scene.py \
  --fixed tmp/task3-demo/fixed-run1.log \
  --rknn tmp/task3-demo/rknn-run1.log \
  --out tmp/task3-demo/report-run1
```

输出 `metrics.json` 和 `REPORT.md`。CONTROL→STATUS 与
inference-start→STATUS 都使用 StarryOS `CLOCK_MONOTONIC`，避免跨 Guest 时钟同步
误差；前者不含推理，后者包含推理、文件发布/轮询、Guest 调度、VirtIO/T2N1 和
Zephyr 执行。时间戳字段为 ns，但实际精度受 controller poll 和调度粒度限制，不能
把“字段精确到 ns”写成“系统测量精度为 1 ns”。

上述构建、RAM 启动、fixed/RKNN 各 3 轮和逐轮量化已串为一个入口：

```bash
TASK123_BUSYBOX_STATIC=/path/to/aarch64-busybox \
TASK123_RKNN_BUNDLE=/path/to/rknn-bundle \
TASK123_BOARD_DTB=/path/to/atk-dlrk3588-starry.dtb \
TASK123_BOARD_OUTPUT_DIR="$PWD/results/task3/board-fresh" \
  scripts/competition/task123.sh board task3-all
```

`task3-all` 先分别从当前源码编译 `fixed-perception` 与 `rknn` controller，组装两份
cpio，再分别构建含 RR/FP-RR 的 FIT，最后只用 FP-RR FIT 做 3+3 对比。构建目录的
`INPUT-SHA256SUMS.txt` 逐项记录 BusyBox、RKNN runtime、模型和输入图片；每个 arm 的
`SHA256SUMS.txt` 再覆盖 controller、payload、配置和 FIT。任何 bundle 资产或冻结图片
缺失/哈希不符都会在启动板卡前失败。

## 图片/视频演示设计

### CARLA 仿真另册

五场景 A/B 交通仿真、软件选型、场景设计、结果、视频选集与统一播放链接已从
Task 3 实现文档中独立出来，见
[09-CARLA仿真与视频演示.md](09-CARLA仿真与视频演示.md)。本页继续聚焦模型、
安全状态机、真实板卡 NPU 和跨 Guest 控制链。

最终 Demo 使用一个屏幕同时展示可追溯的五段链路：

```text
+----------------------+----------------------+---------------------------+
| 输入帧 + SHA-256      | RKNN detection       | 安全策略                   |
| frame/image id       | class/conf/bbox      | SetOutput / Stop / Reset  |
+----------------------+----------------------+---------------------------+
| T2N1: req/seq/ACK/STATUS/RTT                | Zephyr: state/value       |
+---------------------------------------------+---------------------------+
| 累计: correct/total, hazard recall, P95 RTT, errors/retries            |
+------------------------------------------------------------------------+
```

- 普通道路帧：画出检测框，显示映射 target，随后 request ID 相同的 STATUS 数字变化；
- hazard 帧：显示 Stop，Zephyr state 从 Active 进入 Stopped 并保持锁存；
- Reset：显示显式 Reset ACK 后才恢复 SetOutput；
- 低置信度/小目标/非法输出：显示 `REJECTED/SAFE`，并用“无对应 CONTROL”证明安全门；
- 画面每个字段都来自原始日志/manifest，不手填“漂亮数字”。

建议 1920×1080、终端 18–22 pt。录像开始先展示源码身份和输入哈希，
结束展示 `TASK3_HYBRID_SCENE_END`、量化报告和证据 SHA-256。视频与关键截图
进入 artifact index；视频是直观展示，原始串口和机器验证器仍是验收依据。

该设计现已有可直接运行的实现：

```bash
scripts/competition/task123.sh board demo
TASK123_FFMPEG=/path/to/ffmpeg scripts/competition/task123.sh board demo-video
```

正式输出位于 [`results/task3/board-20260825/`](../results/task3/board-20260825/) 的
`demo/` 子目录：
`dashboard.html` 循环重放 RKNN 决策、T2N1 request/RTT 与 Zephyr state；
`dashboard.png` 展示正常闭环，`dashboard-safe.png` 固定展示 SmallArea 安全拒绝和
`NO CONTROL → SAFE`。生成器严格检查拒绝 request 没有对应 CONTROL，并把 fixed、
RKNN、拒绝日志复制进 `raw/`；`artifact-index.json` 记录全部输出 SHA-256。

现场录像可直接打开 HTML 全屏录制；建议先停留正常 SetOutput，再等待 hazard Stop，
最后用 `?safe=1` 打开安全拒绝画面。自动录制入口已从同一 HTML 和当前日志
生成 6 s、1480×720、30 fps H.264 `dashboard.mp4`，其 SHA-256 为
`bc823317ad42d063215c3e1111b4340c08736648d305f85d4d413ede565523b4`。最后三帧使用
`?safe=1` 展示 `NO CONTROL→SAFE`；视频、截图、JSON 和原始 console 已一起写入
`artifact-index.json`。它是可核验的证据重放，不冒充当场重跑实板的完整长视频。

## 对比一：固定参数手动控制 vs 真实 YOLO（实板快速闭环）

固定参数组和 YOLO 组使用相同五张冻结图片、Guest、FP-RR、T2N1 和
Zephyr plant。固定参数组始终发送 `target=500`，不运行模型；YOLO 组对每张
图片执行真实 ncnn，左/中/右三张接受，无目标与小目标两张被安全拒绝。

| 指标 | manual 固定参数 | YOLO | 解释 |
| --- | ---: | ---: | --- |
| 完整 CONTROL/STATUS | 5/5 | 3/3 可接受图片 | 两组闭环均完整；YOLO 另拒绝 2 张 |
| 发送目标 MAE | 93.00 | **26.33** | YOLO 更接近图片真值，仍受单步限幅 |
| CONTROL→STATUS 平均 RTT | 250.4 ms | **219.0 ms** | 本次快速实板观测值 |
| plant 单步状态 MAE | **70.67** | 89.00 | 单步受 plant 惯性影响，不能当稳态性能 |
| 预期接受/拒绝正确率 | 不适用 | **5/5** | 两张危险输入未发 CONTROL |
| 平均推理时间 | 不适用 | 1.622 s | 该组使用 CPU ncnn，不是 NPU 性能 |

证据：[`results/atk-dlrk3588-task123-integrated-ab-20260824/`](../results/atk-dlrk3588-task123-integrated-ab-20260824/)。这是 manual 1 次 + YOLO 1 次的快速物理板验证，不应冒充 3+3 正式统计。

## 对比二：固定感知 vs RKNN/NPU（实板 3+3）

迁移前物理板归档在同一 12-event 场景、FP-RR 拓扑、Zephyr 和 T2N1 下各运行 3 次：

| 三次运行汇总 | fixed-perception | RKNN/NPU |
| --- | ---: | ---: |
| 每轮正确动作 | 8/12 | **12/12** |
| hazard recall | 0% | **100%** |
| vehicle recall | 不适用 | **100%** |
| CONTROL→STATUS RTT | 68.278 ± 2.164 ms | 162.472 ± 9.155 ms |
| input/inference-start→STATUS | 68.285 ± 2.124 ms | 273.215 ± 2.544 ms |
| 完整模型推理 | 不适用 | 46.389 ± 0.091 ms |

这组补充对比展示的是取舍而不是“AI 所有指标都更好”：RKNN/NPU 把动作
正确率从 66.7% 提高到 100%，并识别固定策略无法识别的 hazard；代价是图像
处理、调度、通信与状态回传带来更高端到端延迟。

## 对比三：合法模型输出 vs 非法模型输出

- `task23-integrated`：真实 YOLO 输出通过校验后，3/3 完成 CONTROL→ACK/STATUS；Guest 推理分别为 15.815/16.408/15.891 s（QEMU TCG）。
- `task3-model-rejected`：注入畸形或越界模型输出，必须出现 `TASK3_MODEL_REJECTED` 和 Safe 标志，并且在拒绝点前后不发送对应 CONTROL；心跳仍存在，证明是业务安全拒绝而不是 Guest 崩溃。

这项 A/B 直接验证“模型输出不能绕过安全边界”。

## 实现范围与证据映射

| 能力 | 已完成内容 | 证据 |
| --- | --- | --- |
| StarryOS 神经网络推理 | 实板 ncnn/CPU 参考、QEMU ncnn/CPU 替身、正式实板 RKNN/RK3588 NPU | 推理日志、检测结果、模型与输入哈希 |
| 模型输出跨 Guest 发送 | 安全校验后的目标编码为 T2N1 CONTROL | CONTROL/ACK/STATUS 逐事务日志 |
| RTOS 可观察动作 | Zephyr 更新虚拟 plant/状态并输出动作日志 | action/state/value 与 request ID |
| 完整闭环 | 输入→推理→CONTROL→ACK/STATUS→量化器 | QEMU 与实板闭环报告 |
| 端到端延迟 | 同侧单调时钟测 inference-start→STATUS 与 CONTROL→STATUS | 每轮 CSV/JSON 与汇总 |
| 固定基线对比 | 动作正确率、vehicle/hazard recall、RTT 和端到端延迟 | 固定基线组与 RKNN 组各三轮 |
