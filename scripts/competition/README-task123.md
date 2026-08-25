# Task 1–3 一键复现入口

所有命令都从仓库根目录执行。复现者不需要理解内部几十个实验脚本，只使用：

```bash
scripts/competition/task123.sh doctor
scripts/competition/task123.sh --list
scripts/competition/task123.sh build full
scripts/competition/task123.sh suite acceptance
```

`doctor` 只检查和给出安装提示，不会静默安装系统软件。`build full` 可以复用已下载的源码、模型、rootfs 和工具链，但会删除本项目固定输出目录内的 ncnn、Zephyr、StarryOS、AxVisor 编译结果并从当前 checkout 重新生成。运行证据默认写入 `tmp/competition-task123/evidence/`，每个场景保存 commit、日志、pcap、命令和哈希。

## 下载依赖

下载内容可以放在任意目录，通过环境变量传入；不要把个人主目录写进脚本。下面使用仓库内被忽略的 `tmp/competition-task123/downloads/` 作为示例：

```bash
mkdir -p tmp/competition-task123/downloads

git clone https://github.com/Tencent/ncnn.git \
  tmp/competition-task123/downloads/ncnn
git -C tmp/competition-task123/downloads/ncnn checkout \
  946fe3fb14a8dff8c06df763f67be522167b2f00

git clone https://github.com/zephyrproject-rtos/zephyr.git \
  tmp/competition-task123/downloads/zephyr-dccb09599635bdff17633fa7e9dab014b91dce90
git -C tmp/competition-task123/downloads/zephyr-dccb09599635bdff17633fa7e9dab014b91dce90 \
  checkout dccb09599635bdff17633fa7e9dab014b91dce90
```

另外准备：

- pnnx Linux `20260526`，并设置 `PNNX=/path/to/pnnx`；
- YOLO11n ONNX，SHA256 必须是 `634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b`，设置 `YOLO_ONNX=/path/to/yolo11n.onnx`；
- AArch64 musl 工具链，设置 `CROSS_ROOT=/path/to/aarch64-linux-musl-cross`，或把其 `bin` 加入 `PATH`；
- 如果源码没有放在上述默认位置，设置 `NCNN_SOURCE` 和 `ZEPHYR_BASE`。

可复制的相对路径配置示例：

```bash
export CROSS_ROOT="$PWD/tmp/competition-task123/downloads/aarch64-linux-musl-cross"
export NCNN_SOURCE="$PWD/tmp/competition-task123/downloads/ncnn"
export ZEPHYR_BASE="$PWD/tmp/competition-task123/downloads/zephyr-dccb09599635bdff17633fa7e9dab014b91dce90"
export PNNX="$PWD/tmp/competition-task123/downloads/pnnx-20260526-linux/pnnx"
export YOLO_ONNX="$PWD/tmp/competition-task123/downloads/yolo11n.onnx"
```

## 推荐验收顺序

时间有限时运行：

```bash
scripts/competition/task123.sh build full
scripts/competition/task123.sh suite acceptance
```

`acceptance` 包含 Task 1 调度器 A/B、Task 2 无模型正常链路和 blackout、Task 2/3 联合正常闭环，以及 Task 3 模型输出拒绝。全部十个行为场景使用：

```bash
scripts/competition/task123.sh suite full
```

十个行为场景严格为：

1. `task3-yolo-smoke`
2. `task1-scheduler-ab`
3. `task2-normal`
4. `task23-integrated`
5. `task2-drop-ack`
6. `task2-retry-exhausted`
7. `task2-blackout`
8. `task2-out-of-order`
9. `task2-invalid-parameter`
10. `task3-model-rejected`

`ci-contracts` 是独立 gate，不计入十个行为场景。`suite task2` 的六个场景均使用真正的 Task2-only 模式：不要求或加载 YOLO，且验证器拒绝任何 Task 3 模型活动。联合正常闭环是 `task23-integrated`。

单个失败不会被包装成成功。脚本非零退出，失败现场保留在输出目录。每次 QEMU 运行通过 `mktemp` 获得独占的短路径目录，serial/QMP socket 与临时 rootfs 只由该次运行清理；所有提交内路径均从仓库根目录解析。每次双 Guest 运行会复制一个临时 rootfs，结束后删除，避免超时退出污染后续场景的基础镜像或其他并发任务。

高内存 QEMU 运行还通过 `TASK123_QEMU_LOCK_FILE` 串行化，等待上限由 `TASK123_QEMU_LOCK_TIMEOUT_SEC` 设置。这避免在内存受限主机上同时启动两个 `-m 8g` QEMU，防止 swap 耗尽造成的串口 watchdog 假超时。

也可以在 VS Code 的 `Terminal -> Run Task` 中直接选择：

- `Competition: Run Task 1`
- `Competition: Run Task 2`
- `Competition: Run Task 3`
- `Competition: Run Full Validation`

对应命令分别是 `suite task1`、`suite task2`、`suite task3` 和 `suite full`。
这些入口只调用同一个配置与证据实现，不复制工具链路径或场景参数。

## 物理板一键入口

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

宿主机可覆盖字段、板卡字段、协议固定值、Guest 内路径和历史 Task 3 入口的完整
梳理见 [`TASK123-CONFIG-AUDIT.md`](TASK123-CONFIG-AUDIT.md)。正式场景默认拒绝
dirty worktree；仅在显式设置 `ALLOW_DIRTY=1` 时允许运行，并把 patch 与未跟踪
文件哈希写入证据目录，防止把未提交修复误写成当前 HEAD 的证据。

AI vCPU 与 RTOS 共核的 `task1-ai` 只保留为非正式消融兼容入口，默认拒绝运行。
只有明确进行并单独标记消融实验时才可设置 `TASK123_ALLOW_AI_SHARE_ABLATION=1`；
其结果不得进入正式统计、报告或 Demo。完整启动、证据和排障导航见
[`very_special-成果材料/10-复现导航.md`](../../very_special-成果材料/10-复现导航.md)。

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
