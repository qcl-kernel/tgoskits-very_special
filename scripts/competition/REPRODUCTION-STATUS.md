# Task 1–3 修复状态与复测指南

此前复现发现的问题均已有对应修复。请更新到
`competition/task123-integration-20260824` 分支的 `fb98a33ae` 或更新提交后复测。

## 已修复问题

| 上次现象 | 当前状态 |
|---|---|
| `axvm` 默认库测试缺少平台链接符号 | 已修复；`cargo test -p axvm --lib --locked` 默认退出 0，无需手工添加 feature |
| `prepare` 未补齐 ncnn、pnnx、Zephyr、YOLO 等固定依赖 | 已修复；依赖放在仓库内的 `.deps/task123/`，不依赖个人绝对路径 |
| Zephyr Python 缺少 `jsonschema` | 已修复；`prepare` 创建并使用固定的专用 Python 环境 |
| `libudev`、`libclang` 缺失时到构建中途才失败 | 已修复；`doctor` 会在构建前检查并给出安装提示 |
| `build full` 不能一键生成全部产物 | 已修复；已在全新 Ubuntu 环境从零验证，退出 0 |
| Task 2、Task 3 因产物未准备而立即失败 | 已修复；完整构建后可分别运行各 suite |

仓库已有的正式结果不只是短 smoke：Task 1 实板正式矩阵为 RR 3 轮和 FP-RR
3 轮，每轮 6000 个 10 ms 样本；Task 2 实板为 3 轮、每轮 200 个完整可靠事务，
共 600/600 成功。对应结果位于 `results/task1/board-20260825/` 和
`results/task2/board-20260825/bounded-200/`；短样本仅用于修改后的快速回归。

## 建议复测命令

Ubuntu 系统依赖及完整说明见 [README-task123.md](README-task123.md)。安装系统依赖后：

```bash
git switch competition/task123-integration-20260824
git pull --ff-only

scripts/competition/task123.sh prepare
scripts/competition/task123.sh doctor
cargo test -p axvm --lib --locked
scripts/competition/task123.sh build full
```

构建完成后，各项可以独立运行，不要求一次全部连续执行：

```bash
# 无 RK3588 板卡时，推荐的最终多 vCPU 架构复现
scripts/competition/task123.sh suite task1-multivcpu

# Task 2 六种正常/故障场景
scripts/competition/task123.sh suite task2

# Task 3 YOLO smoke 与非法输出拦截
scripts/competition/task123.sh suite task3

# YOLO -> CONTROL -> Zephyr STATUS/ACK 联合闭环
scripts/competition/task123.sh run task23-integrated
```

成功时分别输出 `TASK123_FULL_BUILD_PASS`、`TASK123_SUITE_PASS` 或
`TASK123_SCENARIO_PASS`，证据保存在 `tmp/competition-task123/evidence/`。

## Task 1 拓扑说明

此前运行的旧 `suite task1` 是早期单 vCPU、AI 压力与 RTOS 竞争的对照场景，
优化幅度较大，但它不是最终物理板架构。

最终 RK3588 架构中，StarryOS 有两个 vCPU：vCPU0 在 pCPU2 负责 RKNN/NPU 推理，
vCPU1 在 pCPU1 负责 T2N1 通信；Zephyr 也运行在 pCPU1。因此真正的调度竞争是
“StarryOS 通信 vCPU 与 Zephyr”共核，优化幅度小于旧 AI-share 场景属于预期结果。

在没有 RK3588 板卡的复测环境中，新增的 `suite task1-multivcpu` 在 QEMU 中保持双 vCPU
角色和通信共核关系，并完成 RR 3 轮、FP-RR 3 轮长测。QEMU 用 ncnn CPU 推理模拟
AI 压力，只用于复现拓扑和调度对比，不代表 RK3588 NPU 的绝对性能。
