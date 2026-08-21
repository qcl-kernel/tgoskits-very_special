# OpenRace 2026 第二阶段交付说明

本文是本团队第二阶段提交的入口文档。评审和组内复现应以本文指定的分支、
提交、设计文档及证据目录为准，不应仅根据仓库默认分支判断完成状态。

## 1. 交付身份

| 项目 | 值 |
| --- | --- |
| 目标仓库 | `qcl-kernel/tgoskits-very_special` |
| 第二阶段交付分支 | `openrace/task3-yolo-ncnn` |
| 总结前代码提交 | `df0791fa1dc056c5fd33cfad59004bf1880671c5` |
| 第一阶段状态索引 | `dev@2d40d224b20dc8c4fab2567097594280af7161cc` |
| 官方同步审计点 | `rcore-os/tgoskits:dev@8e39cbd586a4a34ab9f522931ca4b1e7523709c7` |
| 文档日期 | 2026-08-22 |

本分支是任务一新版实时调度、任务二 T2N1 通信、任务三 CNN 控制闭环和
YOLO ncnn Guest 内推理的累计交付分支。第一阶段 `dev@2d40d224` 仅增加
`TEAM-STATUS.md`，未合并分散在功能分支上的实现；第二阶段继承了
`openrace/task3-clean` 和 `openrace/task3-realtime-virq` 的完整祖先链，
再继续完成任务一收口、任务二协议加固和任务三模型扩展。

本分支不是第一阶段仓库所有实验分支的逐提交合集。旧的
`openrace/realtime-virq-ab` 实验历史、`openrace/task2-net-clean` 最后一个
文档提交及第一阶段状态文件没有机械合并；其主要运行时实现已由后续主线继承
或替代。该边界用于避免把历史实验材料误报为当前实现。

## 2. 系统交付结构

```text
Linux Guest
  ├── CNN：嵌入模型权重，执行时序控制推理
  └── YOLO：ncnn 加载 yolo11n.param/bin，执行图像检测
          ↓
task3-model 感知安全契约
          ↓
T2N1 CONTROL / ACK / HEARTBEAT
          ↓
RTOS Guest（Zephyr）执行输出
          ↓
T2N1 STATUS / ERROR 返回 Linux Guest
          ↓
AxVisor 实时调度、vIRQ、timer 和隔离机制
```

三个任务共用同一运行系统，但评分证据分别归档，不重复使用同一结果证明不同
评分项。当前正式闭环仍使用 Linux Guest；StarryOS 替换工作位于独立分支，
不属于本分支已完成内容。

## 3. 任务一：实时 RTOS 化

### 3.1 新增与修改

- 新增 RT Guest 专用 pCPU 隔离、no-tick 运行模式和 vCPU/pCPU 拓扑校验；
- 解耦 guest vMPIDR 与物理 CPU 放置，修正 vGIC affinity 和多 vCPU 启动；
- 重构虚拟中断的有界 dispatcher、注入队列、定向唤醒和 WFI wait 路径；
- 修正 guest timer 唤醒、CPU enable 同步、设备轮询请求及 console 锁竞争；
- 新增固定优先级轮转（FP-RR）调度器、量子诊断和有界服务机制；
- 修正 IRQ 返回尾部的抢占顺序：GIC completion/EOI 完成后，再对严格更高
  优先级任务触发抢占；
- 新增 VM-exit、timer、调度和 vIRQ 运行诊断；
- 新增 Linux/Zephyr 共核竞争、四场景矩阵、原生 Zephyr 基线及故障取证脚本。

### 3.2 关键位置

- 设计说明：[`book/design/task1-realtime-design.md`](book/design/task1-realtime-design.md)
- 调度器：`components/axsched/src/priority_rr.rs`
- AxVisor 运行时：`os/axvisor/src/`
- VM/vIRQ/timer：`virtualization/axvm/src/`
- 实验脚本：`scripts/test/rt-partition/`
- 最终闭环证据：`results/task1/task1-final-closure-20260820/`

### 3.3 当前结论与限制

共核实验收尾 race 和 IRQ-tail/GIC completion 两个主要缺口已在 QEMU 验证
范围内修复。当前证据支持 FP-RR、有界服务、优先级感知抢占和 dedicated CPU
隔离的实现结论；不宣称已经证明所有架构、所有 IRQ 源或物理板上的硬实时
上界。官方最新 `dev` 的同条件可比运行和物理板 worst-case 仍待补充。

## 4. 任务二：客户机间通信

### 4.1 新增与修改

- 建立 Linux Guest 与 Zephyr RTOS Guest 的双向 VirtIO-net/UDP/IPv4 链路；
- 新增 T2N1 二进制应用协议，包含版本、消息类型、flags、session、sequence、
  acknowledgement、payload length 和 CRC32；
- 实现 `CONTROL`、`STATUS`、`ACK`、`HEARTBEAT`、`ERROR` 消息；
- 实现单 pending 可靠帧、超时重传、重复包幂等、乱序拒绝、心跳超时、Safe
  状态和链路恢复；
- 修正 session mismatch 的跨端语义，确保错误响应和恢复行为一致；
- 新增 AxVisor 内建 VirtIO-net L2 switch，以及 blackout、抓包和端口控制；
- 增加 ACK-drop、乱序、非法参数、session mismatch 和恢复测试；
- 增加 stage-2、DMA carveout、IRQ route、MAC/IP/session/CRC 隔离检查。

### 4.2 关键位置

- 最终协议设计：[`book/design/task2-dual-guest-network-final.md`](book/design/task2-dual-guest-network-final.md)
- 协议实现：`components/task2-net-protocol/`
- Linux endpoint：`apps/arceos/task2-net/`
- Zephyr endpoint：`scripts/test/net-dual-guest/zephyr-task2/`
- 构建与验证：`scripts/test/net-dual-guest/`
- 当前 HEAD 故障证据：`results/task3/fault-current-head-yolo-*/`

### 4.3 当前结论与限制

T2N1 的编码、可靠传输、错误处理和 Safe/恢复语义已有单元测试及双侧 pcap
证据。完整 QEMU 双 Guest 测试属于显式 evidence job，不在普通快速 CI 中自动
启动。第一阶段 `task2-net-clean` 最后一个纯文档提交未并入本分支，第二阶段
以 `task2-dual-guest-network-final.md` 作为 canonical 文档。

## 5. 任务三：AI 控制闭环

### 5.1 CNN 路径

- 新增确定性的虚拟被控对象、数据集生成和训练管线；
- 新增时序 CNN 模型、嵌入权重、golden vectors 和 `no_std` 前向推理；
- Linux Guest 根据状态历史执行 CNN 推理，经 T2N1 发送控制输出；
- RTOS Guest 执行控制并返回状态，形成可观察的 request-response 闭环；
- 新增 baseline/CNN 指标对比、链路故障恢复和重复运行证据。

### 5.2 YOLO ncnn 路径

- 新增 YOLO11n ONNX 到 ncnn `param/bin` 的可复现转换脚本；
- 新增 ncnn AArch64 musl 静态库构建脚本；
- 新增 `task3-ncnn` Rust/C++ FFI adapter；
- Linux Guest 从 `/usr/share/task3-yolo/` 加载模型和 PPM 图片并执行真实 forward；
- 将 class、confidence、center 和 area 归一化后交给统一感知安全契约；
- 对低置信度、小目标、越界字段、无检测及运行错误执行 hold-last-target；
- 通过 initramfs 构建脚本部署模型、图片和 controller；
- 增加独立 AArch64 QEMU ncnn smoke，使用真实图片
  `tennis-ball-plant.jpg`，检测结果为 class 75、confidence 843/1000、
  center-x 421/1000、area 63/1000。

YOLO 的正式路径不再使用 fixture replay。fixture 仅保留用于无运行时依赖的
协议、契约和故障测试。当前通用 YOLO11n 输出的是 COCO class 75，不应表述为
已经得到网球专用类别模型的识别结论。

### 5.3 关键位置

- 设计说明：[`book/design/task3-ai-design.md`](book/design/task3-ai-design.md)
- CNN/感知契约：`components/task3-model/`
- ncnn adapter：`components/task3-ncnn/`
- 模型脚本：`scripts/task3/`
- 基线/CNN 结果：`results/task3/summary.csv`
- YOLO 历史验证：`results/task3/yolo/`
- ncnn Guest smoke：[`results/task3/ncnn-in-guest-evidence-20260821.md`](results/task3/ncnn-in-guest-evidence-20260821.md)

### 5.4 当前结论与限制

CNN 的双 Guest 控制闭环和故障恢复已有完整证据。YOLO ncnn 已完成 AArch64
Guest 执行路径、真实图片推理、controller 接入和 initramfs 部署验证；尚缺一次
以 ncnn 正式路径运行的 Linux/RTOS 双 Guest T2N1 pcap，因此本阶段不把 ncnn
扩展写成“完整网络闭环已经最终收口”。

## 6. 验证摘要

| 验证项 | 结果 |
| --- | --- |
| `cargo test -p task2-net-protocol` | 21 passed |
| `cargo test -p task3-model` | 11 passed |
| CNN controller host check | passed |
| ncnn AArch64 musl compile/check | passed |
| Linux controller AArch64 musl release build | passed |
| YOLO model和图片注入 initramfs | passed |
| AArch64 QEMU ncnn 图片推理 | passed，`TASK3_NCNN_READY status=0` |
| ncnn 正式路径双 Guest pcap | pending |

总体评分映射和证据边界见
[`results/final-submission-scorecard-20260821.md`](results/final-submission-scorecard-20260821.md)。
其中分数是内部估计，不是官方评分。

## 7. 最小复现命令

快速协议与模型测试：

```bash
cargo test -p task2-net-protocol
cargo test -p task3-model
scripts/test/net-dual-guest/run-ci-regression.sh
```

ncnn 模型、输入和 AArch64 smoke：

```bash
PNNX=/path/to/pnnx scripts/task3/convert-yolo-ncnn.sh
NCNN_SOURCE=/path/to/ncnn scripts/task3/build-ncnn-aarch64.sh
scripts/task3/prepare-yolo-ncnn-input.sh
scripts/task3/run-ncnn-smoke.sh
```

构建 YOLO controller 和 Linux initramfs：

```bash
TASK3_CONTROL_LOOP=1 \
TASK3_MODEL=yolo \
TASK3_MODEL_PATH=/usr/share/task3-yolo \
scripts/test/net-dual-guest/build-linux-task2.sh

TASK3_MODEL=yolo \
scripts/test/net-dual-guest/build-linux-initramfs.sh
```

外部工具链、基础 initramfs、PNNX 和 ncnn 源码路径必须由执行环境提供。模型
和输入 SHA256 记录在 ncnn evidence 文档中。

## 8. 官方同步与交付边界

截至 2026-08-22，本分支与 `rcore-os/tgoskits:dev@8e39cbd58` 的共同祖先为
`3afb0b323`：官方侧有 112 个独有提交，本分支侧有 107 个独有提交。本次提交
不对已有证据分支执行历史重写式 rebase；官方同步应在独立 integration 分支中
逐项解决冲突，并在重新运行三任务回归后再替换交付基线。

本阶段尚未完成：

1. ncnn 正式路径的双 Guest T2N1 pcap；
2. StarryOS 替代 Linux Guest 的正式闭环；
3. 第二种 RTOS 或第二块开发板的对比实验；
4. 官方最新 `dev` 的无冲突集成和全量重放；
5. 物理板 worst-case 实时性证据。

## 9. 仓库操作要求

- 本分支只推送到本组 `qcl-kernel/tgoskits-very_special` 比赛仓库；
- `rcore-os/tgoskits` 仅作为只读 `upstream`；
- 已有比赛仓库禁止再次使用 `git push --mirror`；
- 不向其他参赛组开放 Private 仓库；
- 不提交密码、Token、SSH 私钥、评测凭据或本机私密配置；
- 推送前检查目标分支和提交 SHA，避免覆盖 `dev` 或第一阶段分支。
