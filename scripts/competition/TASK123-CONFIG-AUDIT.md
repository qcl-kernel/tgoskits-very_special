# Task 1–3 配置与硬编码审计

本文审计当前统一入口 `scripts/competition/task123.sh`、其 QEMU 场景、四个兼容
Task 3 运行器，以及 ATK-DLRK3588 的构建、选择和 RAM-only 启动入口。历史结果
目录中的命令记录不属于可执行配置，不能据此判断当前脚本仍依赖旧机器路径。

## 审计结论

- 提交内没有开发者用户名或 `/home/<user>` 一类宿主机绝对路径。
- QEMU 的 serial、QMP、临时 rootfs 和 pcap 均在每次运行独占的
  `mktemp` 目录中；清理函数校验目录名和当前 shell 所有权，只终止本次启动的
  PID，不使用全局 `pkill`、`killall` 或按 socket 查杀其他进程。
- 生成的运行时 TOML 会包含绝对路径。这些路径由本次运行解析得到，是 QEMU 和
  AxVisor 的输入，不是提交内硬编码；源 TOML 与渲染后 TOML 会同时进入证据目录。
- 仓库内 `tmp/` 只作为可删除的构建缓存和默认证据根目录，不再承担并发运行时
  socket、可写 rootfs 或当前 pcap 的共享所有权。
- Guest 地址、端口和安装路径是协议/镜像 ABI，不能只修改某一端；它们被有意
  冻结并由协议测试、pcap 检查器和镜像构建共同验证。

## 可配置的宿主机字段

| 类别 | 字段 | 默认/来源 | 约束 |
| --- | --- | --- | --- |
| 证据 | `TASK123_EVIDENCE_DIR` | 仓库内 `tmp/competition-task123/evidence` | 可指向任意可写目录 |
| 短运行目录 | `TASK123_RUNTIME_PARENT` | `TMPDIR`，再回退到 `/tmp` | 每次通过 `mktemp` 创建独占子目录 |
| QEMU 资源互斥 | `TASK123_QEMU_LOCK_FILE`、`TASK123_QEMU_LOCK_TIMEOUT_SEC` | 仓库 `tmp/competition-task123/qemu.lock`、7200 秒 | 同机高内存 QEMU 共享执行槽，超时明确失败 |
| AArch64 工具链 | `CROSS_ROOT`、`CROSS_CC`、`CROSS_CXX`、`CROSS_AR`、`CROSS_RANLIB`、`CROSS_COMPILE` | 显式变量或 `PATH` | 不扫描用户主目录；`doctor` 检查实际可执行文件 |
| 外部源码/工具 | `NCNN_SOURCE`、`ZEPHYR_BASE`、`PNNX`、`YOLO_ONNX` | 显式变量、`PATH` 或仓库内下载缓存 | ncnn/Zephyr commit 与 ONNX SHA-256 固定 |
| ncnn smoke 输入 | `TASK3_NCNN_INPUT` | 本次生成的 `input.ppm` | 统一入口向下透传；证据记录输入 SHA-256 |
| QEMU 输入 | `STARRY_TASK23_ROOTFS`、`STARRY_TASK23_HOST_CONFIG`、`STARRY_TASK23_QEMU_CONFIG`、`STARRY_TASK23_STARRY_VM_CONFIG`、`STARRY_TASK23_RTOS_VM_CONFIG` | 仓库相对默认路径 | 运行前检查存在性并保存源/运行时配置 |
| RTOS 选择 | `STARRY_TASK23_RTOS_NAME`、`STARRY_TASK23_RTOS_IMAGE`、`STARRY_TASK23_RTOS_SOURCE_DIR` | Zephyr 默认，可选 RT-Thread | 镜像路径直接渲染进本次 VM TOML，不复制到共享槽位 |
| 运行策略 | `STARRY_TASK1_PERIODIC_REPEATS`、`STARRY_TASK23_SERIAL_SOCKET_TIMEOUT`、`STARRY_TASK23_COLLECT_RT_STAT` | 场景默认值 | 数值和枚举在启动前校验 |
| 非正式验证 | `ALLOW_DIRTY=1` | 默认关闭 | 开启后证据明确记录 dirty、patch SHA-256 和未跟踪文件清单；不得作为最终 clean-commit 证据 |

所有仓库内默认路径都从脚本位置推导出的 `repo_root` 解析，而不是依赖调用者当前
目录。`OUT_DIR`、`BUILD_DIR`、`NCNN_PREFIX` 等构建脚本字段同样可以覆盖；默认值
只指向仓库内被忽略的构建缓存。`task123.sh` 在所有子命令入口统一展开
`CROSS_ROOT`，因此 `run task3-yolo-smoke` 与 `suite full` 不依赖调用者预先修改
`PATH`。

QEMU 互斥是资源合同，不是场景参数。曾有两个各 `-m 8g` QEMU 在约 15 GiB 主机上并发，swap 用满 `4/4 GiB`，串口进度停滞后被 watchdog 误判为场景超时。现在通过统一 `flock` 串行化，而不是放宽 watchdog 或验收判据。
该合同同时覆盖 Task1 cyclictest、native Zephyr、timer-wheel A/B、StarryOS RR/FP-RR 以及 Task2/3 联合场景；回归测试会枚举这些入口，防止新增或重构时漏接互斥。

历史 QEMU 源配置中的 `127.0.0.1:12721`、`12731`、`12732` 只是渲染器识别的
模板值；正式运行会为本次进程选择空闲端口并保存 `qemu.source.toml` 与
`qemu.runtime.toml`，不会共享固定监听端口。

## 有意冻结的协议与 Guest 字段

| 字段 | 固定值 | 原因 |
| --- | --- | --- |
| Starry/Linux 控制端 | `10.0.42.15/24` | T2N1 双 Guest 拓扑契约 |
| Zephyr/RT-Thread 被控端 | `10.0.42.2/24` | T2N1 双 Guest 拓扑契约 |
| UDP 服务 | `4242` | 两端 wire protocol、pcap verifier 和故障注入器共同使用 |
| YOLO 模型目录 | `/usr/share/task3-yolo` | Starry/Linux rootfs 内安装 ABI，不是宿主机路径 |
| 连续场景临时文件 | `/tmp/scene-expected.txt` 等 | Guest 内易失文件，不与宿主机 `/tmp` 共享 |
| RKNN 控制文件 | `/rknn-control.txt` | initramfs 内 Starry 与外部 RKNN 适配层的交接契约 |

若要更改上述网络字段，必须同时修改控制端、两种 RTOS、QEMU 网络、测试 fixture、
pcap/故障验证器和文档，并重新生成 QEMU 与实板证据；不把它们做成运行时环境变量，
可以避免两端悄悄使用不同协议配置。

## 物理板字段

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `STARRY_KERNEL`、`STARRY_INITRD`、`ZEPHYR_BASE` | 仓库构建缓存 | 可显式覆盖，构建前检查并记录哈希 |
| `ATK_HOST_DTB` / `TASK123_BOARD_DTB` | 无默认值，必须显式指定 ATK-DLRK3588 DTB | 不用 OrangePi DTB 静默替代，也不引用开发机外部目录 |
| `ATK_ZEPHYR_TASK123_DIR`、`ATK_RTTHREAD_TASK123_DIR` | 仓库内 `tmp/` | 只用于选择已有 FIT，不修改目录内容 |
| `ATK_PORT` | `/dev/ttyACM0` | 板型默认串口，可覆盖 |
| `ATK_BAUD` | `1500000` | 板型 UART 参数，可覆盖 |
| `ATK_FASTBOOT_SN` / `TASK123_BOARD_FASTBOOT_SN` | 无硬编码默认值 | 单板时自动选择唯一设备；多板时必须显式指定，避免误选 |
| `ATK_LOG` | 唯一 `mktemp` 文件 | 推荐在正式取证时显式指向版本化证据目录 |
| `ATK_BREAK_WINDOW`、`ATK_POST_BOOT_CAPTURE` | `60`、`0` 秒 | U-Boot 抢占和启动后采集窗口 |
| `ATK_TASK1_TOPOLOGY` | `communication-share` | 可选最终通信共享或强竞争 `ai-share`；manifest 记录两 vCPU 的 pCPU |
| `TASK1_ZEPHYR_SAMPLE_COUNT`、`TASK1_ZEPHYR_DUMP_CHUNK_ROWS` | `300`、`256` | 实验可覆盖；正式 6000 样本运行显式传入 |
| `NATIVE_ZEPHYR_INPUT_DIR`、`NATIVE_ZEPHYR_OUTPUT_DIR` | 仓库 `results/task1/` | 原生板镜像和证据目录 |
| `NATIVE_ZEPHYR_SAMPLES`、`NATIVE_ZEPHYR_TIMEOUT_SEC` | `6000`、`180` | 原生物理板验收规模与等待上限 |
| `ATK_BOOT_FORMAT` | `fit` | 原生 Zephyr 使用 `legacy-uimage`；两者都只 stage 到 RAM |

`FASTBOOT_DOWNLOAD_BUFFER=0x00c00800` 是该 vendor U-Boot 的板级 ABI。RAM-only
脚本只允许 `fastboot stage` 和内存启动，不调用 `flash`、`erase` 或分区操作。
legacy-uImage 的 load/entry/payload size 由原生 Zephyr manifest 和实际 binary size
传入，不在 runner 内重复写死。`0x00c00840` 是固定 64 字节 uImage header 之后的
payload 地址，属于 legacy-uImage 格式与该 download buffer 的组合，不是实验参数。

串口驱动按字节发送 shell 命令并使用 2 ms 间隔，原因是 QEMU PL011 的小接收
FIFO 不能可靠消费一次性粘贴的长命令。该值是 UART 可靠性实现常量，不是宿主机
路径或评测结果字段；协议、模型和场景参数仍由各自的显式配置控制。

## 自动化约束

`test_task123_runtime.py` 会实际创建两个独占运行目录，渲染所有正式 capture
配置，验证 Task 1/2/3 一键入口，并验证旧 Task 3 入口不再出现全局进程清理或固定 `/tmp/task3-*`。正式证据
还应包含：clean commit、源/运行时 TOML、命令、原始日志、pcap、FIT/Guest/模型/
输入哈希，以及分析器输出。只有摘要 CSV 或人工摘录不能替代这些原始材料。
