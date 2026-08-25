# RK3588 实板 RAM 启动包

本目录保存 ATK-DLRK3588/RK3588 实板核验所需的五个 FIT。它们是已在
物理板上运行的产物副本，通过 `fastboot stage` 只加载到 RAM，不擦写
eMMC、SPI 或其他持久存储。QEMU 复现不使用这些 FIT，而是按 Task 1、
Task 2、Task 3 的独立入口从源码构建并运行。

## 文件与用途

| 文件 | 用途 |
| --- | --- |
| `task1/axvisor-task123-zephyr-rr.fit` | Task 1 RR 调度基线 |
| `task1/axvisor-task123-zephyr-fp-rr.fit` | Task 1 有界 FP-RR 调度对照 |
| `task2/axvisor-task123-zephyr-fp-rr.fit` | Task 2 有界 200 事务通信测试 |
| `task3/fixed/axvisor-task123-zephyr-fp-rr.fit` | Task 3 固定感知输出基线 |
| `task3/rknn/axvisor-task123-zephyr-fp-rr.fit` | Task 3 RKNN/NPU 真实推理闭环 |

Task 1 两个 FIT 只改变 AxVisor 调度器。Task 3 两个 FIT 分别表示固定
感知输出和 RKNN/NPU 推理，用于对照“是否根据当前图像改变控制”。

## 冻结实板拓扑

```text
pCPU2: StarryOS vCPU0 -> 图像预处理 -> RKNN -> RK3588 NPU -> 决策
pCPU1: StarryOS vCPU1 通信 + Zephyr 10 ms 周期任务
NPU:   仅由 StarryOS 透传使用
优先级: Zephyr 90, StarryOS 89
```

StarryOS `phys_cpu_ids = [0x200, 0x100]`，Zephyr
`phys_cpu_ids = [0x100]`。此映射同时记录在 `topology.manifest`。

## 核验与启动

在仓库根目录执行：

```bash
cd very_special-成果材料/board-fits
sha256sum -c SHA256SUMS.txt
cd ../..

scripts/competition/task123.sh board task1-communication
scripts/competition/task123.sh board task2-throughput
scripts/competition/task123.sh board task3-matrix
```

启动脚本会监听 U-Boot 并执行 RAM-only stage。若显示
`BOARD_RESET_REQUIRED`，按一次板卡 RST；之后脚本继续完成装载、日志
保存和结果验证。可用 `dumpimage -l <file.fit>` 查看 FIT 内部的内核、
DTB、加载地址和内部 SHA-256。

构建配置的唯一源文件仍是：

- `scripts/board/task123-zephyr/starry.toml.in`
- `scripts/board/task123-zephyr/zephyr.toml.in`
- `scripts/board/task1-starry-rknn-pressure-init.sh`
