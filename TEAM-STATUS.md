# 团队状态说明（OpenRace 2026 第二阶段）

本文是仓库默认分支的审核导航页，更新时间为 **2026-08-22**。第二阶段实现
没有直接合并到 `dev`；请审核人员切换到下列唯一交付分支查看代码、文档和证据。

## 1. 第二阶段审核入口

| 项目 | 内容 |
| --- | --- |
| 交付分支 | `openrace/phase2-delivery` |
| 当前交付提交 | `96b694f23bf89f44643b666c8c901ad74ea6d715` |
| 完整阶段说明 | [`openrace/phase2-delivery/TEAM-STATUS.md`](https://github.com/qcl-kernel/tgoskits-very_special/blob/openrace/phase2-delivery/TEAM-STATUS.md) |
| 第一阶段状态提交 | `dev@2d40d224b20dc8c4fab2567097594280af7161cc` |

```bash
git fetch origin
git checkout openrace/phase2-delivery
git rev-parse HEAD
# 预期：96b694f23bf89f44643b666c8c901ad74ea6d715
```

`openrace/phase2-delivery` 是任务一、任务二、任务三和 YOLO ncnn 扩展的累计
交付，不是单独的 Task 3 功能分支。

## 2. 本周新增与收口内容

| 任务 | 本周主要工作 | 当前状态 | 入口 |
| --- | --- | --- | --- |
| 任务一：实时 RTOS 化 | 完成 FP-RR 固定优先级轮转、有界服务、量子诊断、共核竞争实验；修复实验收尾 race 和 IRQ-tail/GIC completion 抢占顺序 | QEMU 验证范围内主要缺口已收口；物理板和官方最新 `dev` 同条件结果仍待补充 | `book/design/task1-realtime-design.md`、`results/task1/task1-final-closure-20260820/` |
| 任务二：Guest 通信 | 加固 T2N1 的 session mismatch、ACK、重传、乱序、Safe/恢复语义；补齐当前 HEAD 故障注入、双侧 pcap 和 CI 契约检查 | 协议及 Linux/Zephyr 双 Guest 闭环已完成 | `book/design/task2-dual-guest-network-final.md`、`components/task2-net-protocol/` |
| 任务三：CNN 闭环 | 保留 Linux Guest CNN 真实推理、T2N1 CONTROL、RTOS 执行与 STATUS 回传；量化 baseline/CNN/YOLO 模式并复跑故障恢复 | CNN 正式双 Guest 闭环已完成 | `book/design/task3-ai-design.md`、`results/task3/` |
| 任务三：YOLO ncnn | 将 YOLO11n 转换为 ncnn；新增 AArch64 musl runtime、Rust/C++ adapter、initramfs 部署和真实图片推理 | AArch64 Guest 图片 forward 已完成；ncnn 正式路径的双 Guest pcap 尚待最终补齐 | `components/task3-ncnn/`、`results/task3/ncnn-in-guest-evidence-20260821.md` |
| 交付工程 | 建立最终评分映射、证据清单、可复现构建脚本和阶段二统一说明 | 已纳入交付分支 | `results/final-submission-scorecard-20260821.md`、`TEAM-STATUS.md` |

## 3. 第二阶段系统结构

```text
Linux Guest 内 CNN 或 YOLO ncnn 真实推理
                    ↓
        task3-model 感知安全契约
                    ↓
       T2N1 CONTROL / ACK / HEARTBEAT
                    ↓
         Zephyr RTOS Guest 执行控制
                    ↓
             T2N1 STATUS / ERROR
                    ↓
 AxVisor FP-RR、vIRQ、timer、隔离与运行诊断
```

YOLO 的正式运行路径不再使用 fixture replay。fixture 仅用于不依赖 ncnn 的协议、
感知契约和故障测试。AArch64 smoke 使用真实
`tennis-ball-plant.jpg` 图片完成 forward；通用 YOLO11n 输出 COCO class 75，
不将其表述为网球专用模型识别结果。

## 4. 审核顺序

切换到 `openrace/phase2-delivery` 后，建议依次查看：

1. `TEAM-STATUS.md`：完整变更、提交边界、验证结果和未完成项；
2. `book/design/task1-realtime-design.md`：任务一机制和实验方法；
3. `book/design/task2-dual-guest-network-final.md`：T2N1 字段与状态机；
4. `book/design/task3-ai-design.md`：CNN 控制闭环；
5. `results/task3/ncnn-in-guest-evidence-20260821.md`：YOLO ncnn 真实图片推理；
6. `results/final-submission-scorecard-20260821.md`：官网评分项与证据映射。

快速的软件回归命令：

```bash
cargo test -p task2-net-protocol
cargo test -p task3-model
scripts/test/net-dual-guest/run-ci-regression.sh
```

完整双 Guest、实时性和 ncnn 构建需要文档中列出的 QEMU、交叉工具链、基础
initramfs、PNNX 及 ncnn 源码环境。

## 5. 尚未计入完成的工作

以下内容不应从当前提交说明中推断为已经完成：

1. YOLO ncnn 正式路径的 Linux/RTOS 双 Guest T2N1 pcap；
2. StarryOS 替代 Linux Guest 的正式任务二/任务三闭环；
3. 第二种 RTOS 或第二块开发板的对比实验；
4. 与 2026-08-21 官方最新 `dev` 的无冲突集成及全量重放；
5. 物理板 worst-case 实时性证明。

这些限制已在阶段二完整说明中保留，避免把 smoke、历史 fixture 证据或独立实验
分支误写为正式闭环。

## 6. 第一阶段历史分支

第一阶段代码分散在下列功能分支，继续保留用于历史审计：

| 分支 | 第一阶段用途 |
| --- | --- |
| `openrace/realtime-virq-ab` | 任务一初版实时 vIRQ A/B |
| `openrace/task2-net-clean` | 任务二双 Guest 通信与 T2N1 |
| `openrace/task3-clean` | 任务三 CNN 控制闭环 |
| `openrace/task3-netb` | AxVisor 内建 VirtIO-net switch 变体 |
| `openrace/task3-realtime-virq` | 基于任务三基线重做任务一 |
| `openrace/task1-rt-partition` | 第二阶段任务一收口过程分支 |

审核第二阶段时无需逐个拼接上述分支；以 `openrace/phase2-delivery` 为准。
