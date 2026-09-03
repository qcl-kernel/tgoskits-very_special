# Task 1–3 分任务复现入口

复测前可先阅读精简版的[修复状态与复测指南](REPRODUCTION-STATUS.md)。

## 评委复现 Task 1

安装下文列出的 Ubuntu 系统软件后，在仓库根目录按要复现的实验执行一套完整流程。
由于评审老师当前没有 RK3588 物理板，`task1-multivcpu` 把最终实板的
CPU 角色、StarryOS 双 vCPU 以及通信 vCPU/Zephyr 共核竞争关系迁移到
QEMU，使多 vCPU 架构和调度 A/B 可在无板环境中重新构建与验证。

Ubuntu 24.04 可直接安装完整的系统前置软件；`rustup` 会根据仓库的
`rust-toolchain.toml` 下载固定 nightly、组件和目标：

```bash
sudo apt-get update
sudo apt-get install build-essential cmake ninja-build qemu-system-arm qemu-user \
  e2fsprogs device-tree-compiler python3 python3-pil python3-venv git curl rustup \
  xz-utils pkg-config libudev-dev clang libclang-dev
```

复现老师已经运行过的原 Task 1：

```bash
scripts/competition/task123.sh prepare
scripts/competition/task123.sh doctor
scripts/competition/task123.sh build task1
scripts/competition/task123.sh suite task1
```

复现新增的物理板卡拓扑映射实验：

```bash
scripts/competition/task123.sh prepare
scripts/competition/task123.sh doctor
scripts/competition/task123.sh build task1-multivcpu
scripts/competition/task123.sh suite task1-multivcpu
```

两套 Task 1 使用同一条显式流程：“准备依赖 → 检查环境 → 按实验构建 →
运行 suite → 输出结果”，build 和 suite 使用相同的实验名称。`suite` 不会隐式下载或重新构建。原 `task1`
命令的含义保持不变；`task1-multivcpu`
运行 RR 3 轮与 bounded FP-RR 3 轮（每轮 6000 个 10 ms 样本）。评委不需要
修改 JSON/TOML、填写个人路径或设置环境变量。首次运行需要联网，并会因下载、
编译和 QEMU 实验耗时较长；再次运行会复用下载缓存。

如果要一次构建全部 Task 1–3 场景，使用全量入口：

```bash
scripts/competition/task123.sh prepare
scripts/competition/task123.sh doctor
scripts/competition/task123.sh --list
scripts/competition/task123.sh build full
```

准备完成后按需运行对应 suite；以下命令彼此独立，不要求全部连续执行：

```bash
scripts/competition/task123.sh suite task1
scripts/competition/task123.sh suite task1-multivcpu
scripts/competition/task123.sh suite task2
scripts/competition/task123.sh suite task3
```

`prepare` 下载固定的 AArch64 musl 工具链、Zephyr、ncnn、pnnx 和 YOLO11n
模型，并根据该 Zephyr revision 自带的 `scripts/requirements-base.txt` 和仓库的
`scripts/competition/requirements-task123.txt` 创建专用 Python 虚拟环境；后者
固定全量构建门禁使用的 `pytest` 版本。仓库的 `rust-toolchain.toml` 同时声明了
StarryOS endpoint 所需的 `aarch64-unknown-linux-musl` target，空 Rustup 环境在
首次执行 Rust 构建时会自动下载对应的标准库组件。
它们默认位于仓库内被忽略的 `.deps/task123/`。后续 `doctor` 和 `build` 会自动
发现这些依赖，无需再次设置 `CROSS_ROOT`、`ZEPHYR_BASE` 或 Python 路径。
`doctor` 只检查和给出安装提示，不会静默安装系统软件；它还会拒绝本地修改过的
Zephyr 树，并在进入 CMake 或 Python 回归门禁前检查 `jsonschema`、`pytest` 等
专用 Python 环境依赖。
`build full` 可以复用已下载的源码、模型、rootfs、工具链和专用 Python 环境，
但会删除本项目固定输出目录内的 ncnn、Zephyr、StarryOS、AxVisor 编译结果并从
当前 checkout 重新生成。运行证据默认写入 `tmp/competition-task123/evidence/`。
各 suite 分别创建 `suite-task1`、`suite-task1-multivcpu`、`suite-task2`、
`suite-task3` 证据目录，不会把不同实验的数据混在一起；每个场景保存
commit、日志、pcap、命令和哈希。

## 可选：使用已有依赖

正常使用一键入口时无需执行本节。已有下载内容也可以放在任意目录，通过环境变量
传入；不要把个人主目录写进脚本。下面使用仓库相对路径作为示例：

```bash
mkdir -p tmp/competition-task123/downloads

git clone https://github.com/Tencent/ncnn.git \
  tmp/competition-task123/downloads/ncnn
git -C tmp/competition-task123/downloads/ncnn checkout \
  946fe3fb14a8dff8c06df763f67be522167b2f00

```

如果不使用 `prepare`，还需自行准备：

- pnnx Linux `20260526`，并设置 `PNNX=/path/to/pnnx`；
- YOLO11n ONNX，SHA256 必须是 `634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b`，设置 `YOLO_ONNX=/path/to/yolo11n.onnx`；
- `prepare` 已提供 AArch64 musl 工具链与固定 Zephyr 源码；若改用自备副本，
  再设置 `CROSS_ROOT` 或 `ZEPHYR_BASE`；
- 如果 ncnn 源码没有放在上述默认位置，设置 `NCNN_SOURCE`。

可复制的相对路径配置示例：

```bash
export NCNN_SOURCE="$PWD/tmp/competition-task123/downloads/ncnn"
export PNNX="$PWD/tmp/competition-task123/downloads/pnnx-20260526-linux/pnnx"
export YOLO_ONNX="$PWD/tmp/competition-task123/downloads/yolo11n.onnx"
```

如果需要显式指定 `prepare` 生成的目录，可使用：

```bash
export CROSS_ROOT="$PWD/.deps/task123/aarch64-linux-musl-cross"
export ZEPHYR_BASE="$PWD/.deps/task123/zephyr-dccb09599635bdff17633fa7e9dab014b91dce90"
export TASK123_PYTHON="$PWD/.deps/task123/zephyr-python-dccb09599635bdff17633fa7e9dab014b91dce90/bin/python3"
```

`TASK123_PYTHON` 是显式覆盖项：一旦设置，`doctor` 和两类 Zephyr 构建都会使用
该解释器，并在缺少依赖时直接失败，不会悄悄退回系统 Python。通常应直接运行
`prepare`，不要用全局 `pip install jsonschema` 修补宿主环境。

## 分任务验收顺序

构建完成后，按希望核验的任务单独执行。每个入口只汇总本任务的数据：

```bash
# Task 1：4 组运行——空载 RR、空载 FP-RR、压力 RR、压力 FP-RR
scripts/competition/task123.sh suite task1

# Task 1 multi-vCPU：三 pCPU、StarryOS 双 vCPU 的 RR/FP-RR 配对矩阵
scripts/competition/task123.sh suite task1-multivcpu

# Task 2：6 个 Task2-only 正常/故障通信场景
scripts/competition/task123.sh suite task2

# Task 3：2 个场景——真实 YOLO smoke、非法模型输出拒绝
scripts/competition/task123.sh suite task3
```

各命令以与 suite 同名的 `TASK123_SUITE_PASS name=...` 结束。Task 2 的
六个场景均使用真正的 Task2-only 模式：不要求或加载 YOLO，且
验证器拒绝任何 Task 3 模型活动。

`build task1` 和 `build task1-multivcpu` 都构建各自实验所需的
StarryOS/YOLO 共同产物，但只构建所选实验的 RTOS 负载；对应的 `suite` 只消费
这些已构建产物并运行。`build full` 会额外构建另一套 Task 1 以及 Task 2、Task 3，
只适合需要全量产物的场景。
`task1-multivcpu` 启动 3 个 QEMU pCPU，并固定以下映射：StarryOS Guest CPU 0
绑定 pCPU2 运行 ncnn/YOLO 压力；StarryOS Guest CPU 1 绑定 pCPU1 运行 T2N1；
Zephyr vCPU0 也绑定 pCPU1。Zephyr 优先级为 90，StarryOS 为 89。RR 与 FP-RR
两臂复用相同的 StarryOS、rootfs、Zephyr、VM 配置、样本数和 workload；矩阵
验证器拒绝这些不可变产物的哈希差异。

这个入口复现的是 CPU 数量、vCPU/pCPU 放置、Guest 内角色绑定和调度竞争关系。
QEMU 中的 ncnn CPU 推理只是实板 RKNN/NPU 路径的压力替身；它不复现 RK3588 NPU、
设备直通、DMA/IRQ、SoC 内存带宽或绝对时延，因此结果不能解释成 NPU 性能或实板
硬实时性能。一键 `suite` 默认每臂运行 3 轮、每轮采集 6000 个 10 ms 样本，可用
`TASK1_TOPOLOGY_SAMPLE_COUNT` 和 `TASK1_TOPOLOGY_DUMP_CHUNK_ROWS` 调整构建参数。

该入口已完成一次正式 RR 3 轮 + bounded FP-RR 3 轮长测。三轮中位
P99 从 `2.314 ms` 降到 `1.781 ms`（降低 23.05%），P99.9 降低
42.05%，max 降低 53.56%，超过 1 ms 的样本数降低 90.96%。六轮均保持
YOLO 压力与 T2N1 通信存活，并通过拓扑、pcap 和不变产物哈希校验。

Task 2+3 的真实联合闭环单独运行，不并入 Task 2 或 Task 3 的独立统计：

```bash
scripts/competition/task123.sh run task23-integrated
```

QEMU TCG 用于功能、故障和同平台调度机制对照，其绝对推理时间不代表 RK3588 NPU
性能。仓库中的 `very_special-成果材料/board-fits/` 只供物理板 RAM 启动，不作为
QEMU suite 的预编译替代品。

`suite acceptance` 和 `suite full` 仅保留给自动化回归使用，不是人工核验的推荐入口。
`ci-contracts` 也是独立 gate。单独构建 Task 2 endpoint 时可设置
`STARRY_TASK23_BUILD_SCOPE=task2`（也兼容 `STARRY_TASK23_SCOPE=task2`），此时不
检查、链接或安装 ncnn/YOLO；默认的 `build full` 仍使用 `integrated`。模型目录可
通过 `TASK3_YOLO_ASSETS` 覆盖；`TASK3_NCNN_MODEL_DIR` 作为兼容字段继续可用。

单个失败不会被包装成成功。脚本非零退出，失败现场保留在输出目录。每次 QEMU 运行通过 `mktemp` 获得独占目录：serial/QMP socket 保留在短路径临时目录，较大的临时 rootfs 默认放在仓库 `tmp/task123-runtime/`，两者都只由该次运行清理；可分别用 `TASK123_RUNTIME_PARENT` 和 `TASK123_ROOTFS_RUNTIME_PARENT` 覆盖。所有提交内路径均从仓库根目录解析。每次双 Guest 运行会复制一个临时 rootfs，结束后删除，避免超时退出污染后续场景的基础镜像或其他并发任务。

高内存 QEMU 运行还通过 `TASK123_QEMU_LOCK_FILE` 串行化，等待上限由 `TASK123_QEMU_LOCK_TIMEOUT_SEC` 设置。这避免在内存受限主机上同时启动两个 `-m 8g` QEMU，防止 swap 耗尽造成的串口 watchdog 假超时。

也可以在 VS Code 的 `Terminal -> Run Task` 中直接选择：

- `Competition: Run Task 1`
- `Competition: Run Task 2`
- `Competition: Run Task 3`
- `Competition: Run Full Validation`

前三项是人工核验的推荐入口，分别对应 `suite task1`、`suite task2`、
`suite task3`；`suite full` 只用于可选的自动化回归。
这些入口只调用同一个配置与证据实现，不复制工具链路径或场景参数。

## 物理板分任务入口

`task123.sh --list` 同时列出 RAM-only 实板命令。它们统一使用
`scripts/board/atk-dlrk3588-ram-boot.sh` 的 `fastboot stage`，不会调用
`flash`、`erase` 或写 eMMC：

```bash
# 原生 Zephyr，默认三轮
scripts/competition/task123.sh board native

# 冻结架构：通信 vCPU 与 RTOS 共核，RR/FP-RR 各 3 轮
scripts/competition/task123.sh board task1-communication

# Task 2 有界事务和 Task 3 fixed/RKNN 3+3
scripts/competition/task123.sh board task2-throughput
scripts/competition/task123.sh board task3-matrix

# 从当前源码与外部 RKNN bundle 构建，再 RAM 启动并完成 Task 3 3+3
TASK123_BUSYBOX_STATIC=/path/to/aarch64-busybox \
TASK123_RKNN_BUNDLE=/path/to/rknn-bundle \
TASK123_BOARD_DTB=/path/to/atk-dlrk3588-starry.dtb \
  scripts/competition/task123.sh board task3-all

# 从严格验证的原始日志生成 HTML 动画、SVG、PNG 和哈希索引
scripts/competition/task123.sh board demo

# 在同一证据目录内录制 MP4 并刷新 artifact index
TASK123_FFMPEG=/path/to/ffmpeg scripts/competition/task123.sh board demo-video
```

通用字段为 `TASK123_BOARD_PORT`、`TASK123_BOARD_BAUD`、
`TASK123_BOARD_FASTBOOT_SN`、`TASK123_BOARD_RUNS`、
`TASK123_BOARD_TIMEOUT_SEC`、`TASK123_BOARD_BREAK_WINDOW`、
`TASK123_BOARD_OUTPUT_DIR`。未指定 fastboot
序列号时只接受系统中恰好一块 fastboot 设备；多板环境必须明确指定，防止误选。

每轮启动都会先独占串口、开始保存 console，并在重启前启动 Ctrl-C flood。如果板子
无法由 ADB、fastboot 或串口 shell 自动重启，脚本会输出：

```text
BOARD_RESET_REQUIRED: press the physical RST button once now.
press the RST button now; the Ctrl-C flood is already running.
```

看到这两行后只需由现场操作者按一次 RST；不需要手工发送 Ctrl-C。脚本默认等待
300 秒，随后自动捕获 U-Boot、执行 `fastboot stage`、从 RAM 启动并等待完成标记。
原生 Zephyr runner 默认直接使用输入构建目录中的 `zephyr-periodic.dtb`，因此正式
构建产物齐全时无需再手工拼 DTB 路径；需要替换时仍可使用 `NATIVE_ZEPHYR_DTB`。

Task 1 还可设置 `TASK123_BOARD_ARTIFACT_DIR`、`TASK123_BOARD_SAMPLES`、
`TASK123_TASK1_PERIOD_MS`、`TASK123_TASK1_RUNTIME_SEC`、
`TASK123_TASK1_INFERENCES` 和 `TASK123_TASK1_DUMP_CHUNK_ROWS`。Task 2 使用
`TASK123_BOARD_TASK2_FIT` 与 `TASK123_TASK2_TRANSACTIONS`；事务数是编译期合同，
必须与 FIT 内 controller 一致，否则 ready marker 和严格量化器都会拒绝。Task 3
使用 `TASK123_TASK3_FIXED_ARTIFACT_DIR` 与 `TASK123_TASK3_RKNN_ARTIFACT_DIR`。
Demo 录制可设置 `TASK123_BROWSER`、`TASK123_FFMPEG`、`TASK123_DEMO_FRAMES`、
`TASK123_DEMO_CAPTURE_FPS` 和 `TASK123_TASK3_DEMO_SAFE_FRAMES`。
`task3-build`/`task3-all` 还要求 `TASK123_BUSYBOX_STATIC` 和
`TASK123_RKNN_BUNDLE`，并要求 `TASK123_BOARD_DTB`（或 `ATK_HOST_DTB`）明确指定
ATK-DLRK3588 host DTB；构建目录保存 DTB、BusyBox、RKNN runtime、模型、输入图片、cpio
和 FIT 的 SHA-256。

宿主机可覆盖字段、板卡字段、协议固定值、Guest 内路径和 Task 3 兼容入口的完整
梳理见 [`TASK123-CONFIG-AUDIT.md`](TASK123-CONFIG-AUDIT.md)。正式场景默认拒绝
dirty worktree；仅在显式设置 `ALLOW_DIRTY=1` 时允许运行，并把 patch 与未跟踪
文件哈希写入证据目录，防止把未提交修复误写成当前 HEAD 的证据。

AI vCPU 与 RTOS 共核的 `task1-ai` 只保留为非正式消融兼容入口，默认拒绝运行。
只有明确进行并单独标记消融实验时才可设置 `TASK123_ALLOW_AI_SHARE_ABLATION=1`；
其结果不得进入正式统计、报告或 Demo。完整启动、配置、成功标志和排障顺序见
[`very_special-成果材料/05-复现与配置指南.md`](../../very_special-成果材料/05-复现与配置指南.md)。

## 录屏建议（8–12 分钟）

1. 展示分支与 commit：`git status -sb && git rev-parse HEAD`。
2. 展示入口：`task123.sh --list`，随后运行 `task123.sh doctor`。
3. Task 1：运行 `task1-scheduler-ab`，展示 RR/FP-RR 两组相同负载和最终比较结果。
4. Task 2 正常链路：展示 Linux/StarryOS 发出 CONTROL、RTOS 收到并返回 STATUS/ACK，以及 pcap verifier 的 PASS。
5. Task 2 故障：展示 blackout 开启、两端进入 Safe、链路恢复、控制循环继续。
6. Task 3：突出 `TASK3_MODEL_READY` 中模型哈希、真实 `TASK3_INFER`/`TASK3_DETECTION`，随后展示 CONTROL 到 RTOS STATUS 的闭环。
7. 展示 `task3-model-rejected` 的拒绝与 Safe 行为，证明错误输出不会驱动控制量。
8. 结尾展示 `TASK123_SUITE_PASS`、证据目录、`git-head.txt`、日志和两份 pcap。

正式录屏前可以完成下载和 `build full`，避免把大部分视频浪费在编译上；但正式视频中的运行场景、PASS 标志和证据目录应现场生成，并展示它们对应的 commit。终端字号建议 18–22，窗口只保留命令和关键日志，长编译过程可剪辑但不要剪掉运行开始、故障注入、恢复和最终 PASS。

## RK3588 NPU 混合拓扑（实板）

上面的 `task123.sh` 是可移植 QEMU 验收入口。实板 NPU 场景使用独立入口，
避免在没有板卡时让 `doctor` 或 CI 错误地要求 Rockchip SDK：

```bash
# 组装 fixed/RKNN 的 Starry /proc/initrd 原始 cpio
scripts/task3/build-hybrid-scene-payload.sh --help

# 用相同 StarryOS/Zephyr 拓扑生成 RR 与 FP-RR 两个 RAM-boot FIT
STARRY_INITRD=tmp/hybrid-rknn.cpio \
  scripts/board/build-atk-zephyr-task123-unified.sh tmp/hybrid-board

# 严格采集与分析 30,000 个 10 ms 样本
python3 scripts/test/rt-partition/run-hybrid-latency.py \
  tmp/hybrid-rr-stress.log --samples 30000
python3 scripts/test/rt-partition/analyze-hybrid-latency.py \
  tmp/hybrid-rr-stress.log --samples 30000 \
  --output tmp/hybrid-rr-stress-analysis
```

完整拓扑、外部 RKNN 运行包的边界和 RAM-only 回滚方式见
`docs/design/atk-dlrk3588-npu-hybrid.md`。场景策略和同侧时钟测量方法见
`docs/design/task3-continuous-scene-ab.md`。实板 30k 与 fixed/RKNN 3+3 的
精简量化结果见 `results/atk-dlrk3588-npu-hybrid-20260824/README.md`。
