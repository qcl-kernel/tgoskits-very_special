# 仿真演示工作整体架构与文件索引

本文档索引 `Task 1–3 演示视频` 中"仿真方案"相关的全部代码、文档、视频与环境依赖，说明每个文件的职责与彼此关系，方便后续定位和维护。工作根目录为项目内的 `tmp/task123-video-storyboard/`。

## 1. 工作根目录结构

根目录同时容纳三部分内容：面向任务的制作文档、仿真代码脚本、以及仿真产出的视频。其余环境依赖（Python 环境、CARLA 软件本体）安装在外置盘上，不占用项目磁盘。

```text
tmp/task123-video-storyboard/
├── VIDEO-PRODUCTION-TODO.md        # 视频制作 TODO + 冻结场景合同
├── SIMULATION-EXPLORATION-LOG.md   # 仿真探索与问题存档
├── CARLA-SCENARIOS-STATUS.md       # 5 场景设计/进度简报
├── SIM-ARCHITECTURE.md             # 本文档
├── _probe/                         # 实拍素材分析/预览（中间产物）
└── sim/                            # 仿真代码 + 输出
    ├── carla_ab_demo.py
    ├── carla_scenarios.py
    ├── carla_five_scenarios.py
    ├── carla_live_demo.py
    ├── carla_client_demo.py
    ├── sim_closed_loop.py
    ├── sim_gui_loop.py
    ├── sim_visual_loop.py
    ├── obstacle_cutout.png
    └── out/                        # 视频与 JSON 指标
```

## 2. 代码文件职责

仿真代码分两条演进线：早期 pybullet/合成画面版本，以及当前主力 CARLA 版本。前者为验证感知可行性，后者用于正式的多场景 A/B 演示。

| 文件 | 职责 | 状态 |
| --- | --- | --- |
| `carla_five_scenarios.py` | 5 场景统一 A/B：车辆、行人、多目标、CARLA 碰撞传感器、Stop/2s Confirm/Reset | 当前交付 |
| `carla_scenarios.py` | 早期 5 场景框架，实际只完成两类车辆场景 | 早期 |
| `carla_ab_demo.py` | 单场景 A/B 实验（基线撞车 vs 视觉停车/恢复） | 已跑通 |
| `carla_live_demo.py` | 实时演示：桌面上两个窗口显示本车相机+鸟瞰 | 已跑通 |
| `carla_client_demo.py` | CARLA 客户端闭环早期脚本 | 早期 |
| `sim_closed_loop.py` | pybullet 3D 世界 + 合成相机 + 1920×1080 仪表盘 | 备选交付 |
| `sim_gui_loop.py` | pybullet GUI 世界示意版（蓝色方块） | 已弃用 |
| `sim_visual_loop.py` | 纯 cv2 合成前视 + 真实车剪影 + YOLO 闭环 | 早期验证 |
| `obstacle_cutout.png` | grabCut 抠出的真实车剪影（供合成相机用） | 素材 |

## 3. 视觉闭环核心设计

当前五场景交付使用车顶 `z=2.2/俯角6°` 相机和 YOLO11n，按车辆/行人的车道区域选择相关目标。车辆通过框面积与底边位置估计距离，在 8 m 视觉距离硬停车；行人使用独立框高测距，30 m 开始减速、24 m 硬停车，并要求车身外沿到行人外沿的实际最小净距不低于 2 m。视觉分支执行 RUN→STOP→CONFIRM(2s)→RESET→RESUME，并在 RESET 后继续行驶至少 6 秒；固定基线恒定输出并由 CARLA collision sensor 记录碰撞。旧脚本之间的阈值、去抖和相机参数并不完全相同，不应视为同一冻结实现。

CARLA 车辆控制分支仍由主机 ONNX Runtime 驱动，以便固定基线和视觉闭环在同一仿真 plant 上完成 A/B。每个场景此前导出的 `hazard-a`、`hazard-b`、`clear` 三张代表图，共 15 张，已在真实 RK3588 上依次完成 RKNN/NPU 推理，并经 T2N1 向 Zephyr 发送 CONTROL、接收 STATUS/ACK。视频右侧按场景状态同步叠加该次实板运行的 NPU、完整推理流水线、CONTROL→STATUS、推理→STATUS、2 秒停车保持和 RESET→Active 参考值。

这是一条“五场景代表帧实板回放 + 仿真视频同步叠加”的证据链。停车策略调整后的新视频沿用该次真实实板测量作为按场景参考，不宣称 CARLA 相机流与板卡之间存在逐帧实时网络传输，也不宣称新视频每一帧都重新上板。正式实板日志为 `logs/carla-five-board-run-final.log`，结束标志为 `TASK3_HYBRID_SCENE_END source=rknn controller_complete=1 producer_rc=0`。

实板保持项目冻结拓扑：StarryOS vCPU0/pCPU2 执行图像预处理、RKNN/NPU 和决策；StarryOS vCPU1 与 Zephyr vCPU0 共享 pCPU1，分别承担 T2N1 通信与 10 ms RTOS 工作；NPU 仅透传给 StarryOS；优先级为 Zephyr 90、StarryOS 89，调度器为 FP-RR。

## 4. 文档职责

制作类文档记录冻结合同与整体进度，探索类文档记录走过的弯路，两者互补，是恢复上下文的首选入口。

| 文档 | 内容 |
| --- | --- |
| `VIDEO-PRODUCTION-TODO.md` | 视频制作清单 + 冻结的 Task3 障碍避让场景合同（判定带、area_mass、状态机） |
| `CARLA-SCENARIOS-STATUS.md` | 5 场景设计表、视觉控制方案、进度、已排查问题 |
| `SIMULATION-EXPLORATION-LOG.md` | 从实拍转向仿真全过程的尝试、产物、问题与经验 |
| `SIM-ARCHITECTURE.md` | 本文档 |

## 5. 视频输出

`sim/out/` 下按实验分目录存放视频与指标 JSON。CARLA 场景产物命名规则为 `{场景}_{baseline|vision}.mp4` 与同名 `.json`（内含事件序列与逐帧指标）。

| 目录 | 内容 |
| --- | --- |
| `sim/out/carla_ab/` | 单场景 A/B：`baseline_comp*.mp4`（基线碰撞）、`vision_comp*.mp4`（视觉停车恢复） |
| `sim/out/carla_scenarios/` | 5 场景各自的 baseline/vision 两段视频与 JSON |
| `sim/out/carla_five_scenarios/` | 当前 5×2 原始视频、逐帧 JSON、`board-run-final.json` 和 `final-summary.json` |
10 条独立 A/B 视频已通过 README 中的同一个 B站多 P 链接发布；Git 证据目录不再重复保存视频文件。

## 6. 环境依赖（外置盘）

运行所需的 Python 环境与 CARLA 软件本体位于 WD 外置盘，避免占用系统磁盘。`sim/` 下的脚本运行时依赖这些路径。

| 项 | 路径 |
| --- | --- |
| Python 环境（pybullet/opencv/onnxruntime/pillow/carla API） | `/media/huhu/50528FAE528F977E/huhu-archive/sim/venv` |
| CARLA 0.9.16 软件 | `/media/huhu/50528FAE528F977E/huhu-archive/carla/` |
| YOLO 模型（yolo11n.onnx） | 项目内 `tmp/task3-yolo/ncnn-model/yolo11n.onnx` |

## 7. 关键依赖关系

CARLA A/B 链路为：CARLA 服务器（外置盘）→ Python API → 生成本车/车辆/行人 → 相机画面 → 主机 ONNX YOLO → 决策控制 → 独立 mp4/JSON。实板证据链为：当前 CARLA 危险/清空帧 → StarryOS vCPU0/pCPU2 → RK3588 NPU → 控制决策 → StarryOS vCPU1/pCPU1 T2N1 CONTROL → Zephyr/pCPU1 → STATUS/ACK。Git 保存代码、JSON、日志和校验清单，10 条独立视频统一通过 B站多 P 稿件发布。
