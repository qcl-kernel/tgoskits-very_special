# very_special 成果材料

## 提交信息

- 团队名称：`very_special`
- 私有仓库：`qcl-kernel/tgoskits-very_special`
- 最终成果分支：`competition/task123-integration-20260824`
- 原型系统：AxVisor + StarryOS + Zephyr Task 1–3 虚拟化集成

## 成果概述

本分支集成了三项核心能力：

1. Task 1：RR 与 bounded FP-RR 调度对比，支持周期延迟和抖动验证。
2. Task 2：StarryOS 与 Zephyr 之间的 virtio-net 控制、ACK 和状态回传闭环。
3. Task 3：真实 ncnn/YOLO 推理以及推理结果到 RTOS 控制链路的集成。

## 一键验收

在仓库根目录执行：

```bash
scripts/competition/task123.sh doctor
scripts/competition/task123.sh build full
scripts/competition/task123.sh suite acceptance
```

可用场景可通过以下命令查看：

```bash
scripts/competition/task123.sh --list
```

## 核心验证结果

- Task 1：P99 wake-up jitter 从 RR 的 `11.829 ms` 降至 FP-RR 的 `1.867 ms`，降低 `84.21%`。
- Task 2：三轮通信均完成 `CONTROL_SENT -> ACK -> STATUS_DELIVERED`，双端 pcap 账本一致。
- Task 3：QEMU AArch64 下真实 YOLO smoke 推理耗时 `11.562334 s`，完整闭环三次平均约 `15.797 s`。该时间用于功能验证，不代表物理板性能。
- 三个核心场景总运行时间约 `5 分 16 秒`。

## 材料说明

完整源代码、构建脚本和验收入口位于本分支原目录结构中。设计文档、演示视频链接和其他补充材料将继续放在本目录下，不改变最终成果分支名。

