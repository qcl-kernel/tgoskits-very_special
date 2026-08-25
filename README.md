<h1 align="center">TGOSKits</h1>

<p align="center">An integrated Rust workspace for operating system and virtualization development</p>

<div align="center">

[![Build & Test](https://github.com/rcore-os/tgoskits/actions/workflows/ci.yml/badge.svg)](https://github.com/rcore-os/tgoskits/actions/workflows/ci.yml)
[![Rust](https://img.shields.io/badge/edition-2024-orange.svg)](https://www.rust-lang.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)

</div>

English | [中文](README_CN.md)

## OpenRace Task 1–3 成果导航

本分支在 **ATK-DLRK3588（RK3588）** 实板和 QEMU 上实现了一个由
AxVisor 承载 StarryOS 与 Zephyr 的智能控制系统：Task 1 提供实时调度底座，
Task 2 提供双 Guest 可靠通信，Task 3 将 RKNN/NPU 推理结果安全地交给 RTOS 执行。
完整成果入口见 [成果材料总览](very_special-成果材料/README.md)。

```text
                              RK3588
+------------------------------------------------------------------+
| pCPU2: StarryOS vCPU0                                            |
|        图像 -> RKNN Runtime -> NPU -> 后处理/安全决策            |
|                                      |                           |
| pCPU1: StarryOS vCPU1                |       Zephyr vCPU0        |
|        VirtIO-net/T2N1 -- CONTROL -->+------> 控制执行/10 ms任务 |
|                         <-- ACK/STATUS -------------------------- |
|                AxVisor bounded FP-RR 共享实时通信域               |
+------------------------------------------------------------------+
```

正式架构将 NPU 独占分配给 StarryOS，并把 AI CPU 工作放在 pCPU2；通信 vCPU
与 Zephyr 共享 pCPU1，以隔离推理计算，同时保留真实的通信/控制竞争。Task 2
使用独立 VirtIO-net endpoint、AxVisor L2 switch、UDP/IPv4 和 T2N1 协议。
T2N1 采用 28 字节定长头、CRC32、sequence/ACK、有界重传、去重、乱序拒绝及
Safe/恢复状态机，完整事务为：

```text
StarryOS controller                         Zephyr executor
        |---- CONTROL(seq, request_id) ----------->|
        |<----------------------------- ACK -------|
        |<---- STATUS(last_request_id, state) -----|
        |------------------------------ ACK ------>|
```

快速核对入口：

- [总体架构与设计](very_special-成果材料/00-总体架构与设计.md)
- [Task 1 实时调度](very_special-成果材料/01-Task1-实时调度设计与结果.md)、[Task 2 通信协议与吞吐](very_special-成果材料/02-Task2-双Guest通信设计与结果.md)、[Task 3 推理控制](very_special-成果材料/03-Task3-推理控制设计与结果.md)
- [实现范围与源码对应](very_special-成果材料/09-任务要求与实现覆盖.md)
- [上游贡献与 StarryOS 完善](very_special-成果材料/11-上游贡献与StarryOS完善.md)：两个已合并修复及一个等待人工审查的 syscall PR
- [证据与日志索引](very_special-成果材料/07-证据索引与验收状态.md)：原始串口、CSV/JSON、pcap、图片、视频和 SHA-256 的位置
- 实板结果目录：[Task 1](results/task1/board-20260825/)、[Task 2](results/task2/board-20260825/)、[Task 3](results/task3/board-20260825/)
- [最短复现导航](very_special-成果材料/10-复现导航.md)与[脚本说明](scripts/competition/README-task123.md)

所有命令从仓库根目录执行；先检查环境并查看可用入口：

```bash
scripts/competition/task123.sh doctor
scripts/competition/task123.sh --list
scripts/competition/task123.sh suite full
```

实板可分别运行 `board task1-communication`、`board task2-throughput` 和
`board task3-matrix`。脚本只使用 RAM-only `fastboot stage`；仅当终端出现
`BOARD_RESET_REQUIRED` 时按一次板卡 RST。更详细的依赖、参数和核验标志见
[复现导航](very_special-成果材料/10-复现导航.md)。

## 1. Introduction

TGOSKits is an integrated repository for operating system and virtualization development. It brings together ArceOS, StarryOS, Axvisor, shared components, platform crates, and driver infrastructure in one workspace. A unified `cargo xtask` entry point is used for build, run, debug, and test workflows, making the repository suitable for component development, cross-system integration, and system-level validation.

Project site: [https://rcore-os.cn/tgoskits/](https://rcore-os.cn/tgoskits/). To understand the project scope and system relationships, start from the [TGOSKits documentation](https://rcore-os.cn/tgoskits/docs/introduction).

## 2. Repository

TGOSKits brings multiple standalone subprojects into the root repository through Git Subtree and provides unified entry points for building, running, testing, and documentation. The main directories are:

```text
tgoskits/
├── components/                # reusable component crates
├── os/
│   ├── arceos/                # ArceOS modular kernel
│   ├── StarryOS/              # StarryOS Linux-compatible OS
│   └── axvisor/               # Axvisor Type-I Hypervisor
├── platform/                  # platform and board support crates
├── drivers/                   # reusable drivers and driver subsystems
├── test-suit/                 # system-level test cases
├── xtask/                     # unified root command entry
├── scripts/                   # repository maintenance, test, and sync scripts
└── docs/                      # Docusaurus documentation site
```

For subtree synchronization, component layering, and development conventions, see [repository structure and collaboration](https://rcore-os.cn/tgoskits/docs/contributing/repo) and the [architecture overview](https://rcore-os.cn/tgoskits/docs/architecture/overview).

## 3. Quick Experience

### 3.1 Environment Setup

For a first run, the recommended path is to use the project container image. It already includes the Rust toolchain, QEMU, and common cross-compilation dependencies, matching the CI environment:

```bash
git clone https://github.com/rcore-os/tgoskits.git
cd tgoskits

docker pull ghcr.io/rcore-os/tgoskits-container:latest
docker run -it --rm \
  -v "$(pwd)":/workspace \
  -w /workspace \
  ghcr.io/rcore-os/tgoskits-container:latest
```

If you do not use the container, prepare at least Rust, basic build tools, and common QEMU packages. The recommended QEMU version is 10.2.1, matching the container and CI environment; distribution packages are usually enough for quick trials, but switch to the container if a target is missing or behavior differs:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
sudo apt update
sudo apt install -y cmake make ninja-build pkg-config e2fsprogs fakeroot
sudo apt install -y qemu-system-arm qemu-system-riscv64 qemu-system-x86
cargo install cargo-binutils
```

See [quick start overview](https://rcore-os.cn/tgoskits/docs/quickstart/overview) and [CI and container images](https://rcore-os.cn/tgoskits/docs/build/ci) for the full environment guide.

### 3.2 QEMU Verification

First confirm that common QEMU commands are available, preferably matching QEMU 10.2.1 from the container and CI environment:

```bash
qemu-system-riscv64 --version
qemu-system-aarch64 --version
qemu-system-x86_64 --version
qemu-system-loongarch64 --version
```

Then use the unified `cargo xtask` entry point to run the three system paths:

```bash
# ArceOS: run the default Hello World
cargo xtask arceos qemu --arch aarch64

# StarryOS: prepare rootfs before the first run
cargo xtask starry rootfs --arch aarch64
cargo xtask starry qemu --arch aarch64

# Axvisor: run a Hypervisor QEMU scenario
cargo xtask axvisor qemu --arch aarch64
```

If you only want the shortest path to a successful run, start with the default ArceOS Hello World app. Pass `--package arceos-shell` when you specifically need the interactive Shell. For more systems, architecture combinations, and QEMU options, see the [quick start overview](https://rcore-os.cn/tgoskits/docs/quickstart/overview) and [run and QEMU](https://rcore-os.cn/tgoskits/docs/build/run).

## 4. Contributing

Issues and pull requests are welcome. A typical workflow is:

1. Read [repository structure and collaboration](https://rcore-os.cn/tgoskits/docs/contributing/repo).
2. Create a feature branch from `dev`.
3. Run the relevant `cargo xtask` build, test, or clippy checks after making changes.
4. Open a PR and describe the change scope, validation, and impact.

For a full development example, documentation contribution, and rootfs maintenance notes, see the [contribution docs](https://rcore-os.cn/tgoskits/docs/contributing/demo). Use [GitHub Issues](https://github.com/rcore-os/tgoskits/issues) for feedback and [GitHub Pull Requests](https://github.com/rcore-os/tgoskits/pulls) for patches.

## 5. License

TGOSKits as a whole is licensed under [Apache-2.0](./LICENSE). Some subtree components may include their own license files; if there is any difference, use the license file in the component directory as the source of truth.
