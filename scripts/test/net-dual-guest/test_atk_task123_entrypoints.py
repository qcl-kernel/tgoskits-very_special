from pathlib import Path
import os
import subprocess
import tempfile
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD = REPO_ROOT / "scripts/board/build-atk-zephyr-task123-unified.sh"
TASK123_TOOLS = REPO_ROOT / "scripts/lib/task123-tools.sh"
RAM_BOOT = REPO_ROOT / "scripts/board/atk-dlrk3588-ram-boot.sh"
NATIVE_ZEPHYR_BOARD = REPO_ROOT / "scripts/board/run-atk-native-zephyr.sh"
TASK123_ENTRYPOINT = REPO_ROOT / "scripts/competition/task123.sh"
DEMO_RENDERER = REPO_ROOT / "scripts/competition/render-task123-demo.py"
DEMO_RECORDER = REPO_ROOT / "scripts/competition/record-task123-demo.py"
TASK3_MATRIX_BUILD = REPO_ROOT / "scripts/board/build-atk-task3-matrix.sh"
SELECT = REPO_ROOT / "scripts/board/select-atk-task123-rtos.sh"
STARRY_BUILD_CONFIG = (
    REPO_ROOT
    / "apps/starry/orangepi-5-plus-uvc-rknn/build-aarch64-unknown-none-softfloat.toml"
)
STARRY_VM_TEMPLATE = REPO_ROOT / "scripts/board/task123-zephyr/starry.toml.in"
RKNN_SCENE_INIT = REPO_ROOT / "scripts/task3/hybrid-scene-rknn-init.sh"
FIXED_SCENE_INIT = REPO_ROOT / "scripts/task3/hybrid-scene-fixed-init.sh"
TASK1_PRESSURE_INIT = REPO_ROOT / "scripts/board/task1-starry-rknn-pressure-init.sh"


def run(
    *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def fixture_environment(root: Path) -> dict[str, str]:
    zephyr = root / "zephyr"
    rtthread = root / "rtthread"
    zephyr.mkdir()
    rtthread.mkdir()
    (zephyr / "axvisor-task123-zephyr-rr.fit").write_bytes(b"zephyr-rr")
    (zephyr / "axvisor-task123-zephyr-fp-rr.fit").write_bytes(b"zephyr-fp-rr")
    (rtthread / "axvisor-task123-integrated-fp-rr.fit").write_bytes(b"rtthread-fp-rr")
    environment = os.environ.copy()
    environment["ATK_ZEPHYR_TASK123_DIR"] = str(zephyr)
    environment["ATK_RTTHREAD_TASK123_DIR"] = str(rtthread)
    return environment


def test_unified_builder_is_syntax_valid_and_builds_both_schedulers() -> None:
    assert run("bash", "-n", str(BUILD)).returncode == 0
    source = BUILD.read_text()
    assert "build_scheduler_variant rr rr-scheduler RR" in source
    assert "build_scheduler_variant fp-rr fp-rr-scheduler FP-RR" in source
    assert source.count("build_unified_zephyr_guest") == 2
    assert "fastboot stage" not in source
    assert "fastboot flash" not in source
    assert "fastboot erase" not in source
    assert "TASK2_ZEPHYR_VIRTIO_FDT_PATH=/virtio_mmio@b000000" in source
    assert "TASK2_ZEPHYR_VIRTIO_HOST_HWIRQ=1" in source
    assert "TASK2_ZEPHYR_VIRTIO_GUEST_IRQ=33" in source
    assert 'TASK1_ZEPHYR_DUMP_CHUNK_ROWS="$dump_chunk_rows"' in source
    assert 'host_dtb="${ATK_HOST_DTB:-${TASK123_BOARD_DTB:-}}"' in source
    assert "tmp/board/atk-dlrk3588-starry.dtb" not in source


def test_board_task1_runner_acknowledges_each_evidence_chunk() -> None:
    runner = (REPO_ROOT / "scripts/board/run-atk-task1-yolo-arm.py").read_text()
    assert "PERIODIC LATENCY CHUNK end=" in runner
    assert "dump_chunk_rows" in runner


def test_legacy_linux2_runner_parameterizes_board_and_sample_contract() -> None:
    runner = (REPO_ROOT / "scripts/board/run-atk-task1-linux2-matrix.py").read_text()
    assert 'parser.add_argument("--baud"' in runner
    assert 'parser.add_argument("--expected-samples"' in runner
    assert 'parser.add_argument("--periodic-timeout"' in runner
    assert "serial.Serial(path, baud" in runner
    assert 'samples=300"' not in runner


def test_ram_boot_can_wait_for_a_guest_ready_contract() -> None:
    assert run("bash", "-n", str(RAM_BOOT)).returncode == 0
    source = RAM_BOOT.read_text()
    assert 'ready_regex="${ATK_READY_REGEX:-}"' in source
    assert 'ready_timeout="${ATK_READY_TIMEOUT:-180}"' in source
    assert 'wait_for_console "$boot_output_mark" "$ready_regex" "$ready_timeout"' in source


def test_ram_boot_supports_legacy_uimage_without_writing_storage() -> None:
    source = RAM_BOOT.read_text()
    assert 'boot_format="${ATK_BOOT_FORMAT:-fit}"' in source
    assert "legacy-uimage" in source
    assert "bootm start $FASTBOOT_DOWNLOAD_BUFFER" in source
    assert 'legacy_load_address="${ATK_LEGACY_LOAD_ADDRESS:-}"' in source
    assert 'legacy_payload_size="${ATK_LEGACY_PAYLOAD_SIZE:-}"' in source
    assert 'legacy_entry_address="${ATK_LEGACY_ENTRY_ADDRESS:-}"' in source
    assert "cp.b 0x00c00840" in source
    assert "send_uboot_command 'dcache flush'" in source
    assert "send_uboot_command 'dcache off'" in source
    assert "send_uboot_command 'icache off'" in source
    assert "bootm loados" not in source
    assert 'send_console "go $legacy_entry_address"' in source
    assert "fastboot flash" not in source
    assert "fastboot erase" not in source


def test_ram_boot_does_not_hardcode_one_fastboot_device() -> None:
    source = RAM_BOOT.read_text()
    assert 'fastboot_sn="${ATK_FASTBOOT_SN:-}"' in source
    assert "expected exactly one fastboot device" in source
    assert "resolve_fastboot_serial" in source
    assert "8d4bd3e013e56633" not in source


def test_native_zephyr_board_runner_archives_strict_evidence() -> None:
    assert run("bash", "-n", str(NATIVE_ZEPHYR_BOARD)).returncode == 0
    source = NATIVE_ZEPHYR_BOARD.read_text()
    assert "mkimage" in source
    assert "ATK_BOOT_FORMAT=legacy-uimage" in source
    assert 'samples="${NATIVE_ZEPHYR_SAMPLES:-6000}"' in source
    assert "expected {expected_samples} native board Zephyr samples" in source
    assert "rt_latency_stats.py" in source
    assert "sha256sums" in source


def test_unified_entrypoint_lists_board_matrices_and_demo() -> None:
    result = run("bash", str(TASK123_ENTRYPOINT), "--list")
    assert result.returncode == 0, result.stderr
    for command in (
        "native",
        "task1-communication",
        "task1-ai",
        "task2-throughput",
        "task3-build",
        "task3-matrix",
        "task3-all",
        "demo",
        "demo-video",
    ):
        assert command in result.stdout
    source = TASK123_ENTRYPOINT.read_text()
    assert "fastboot flash" not in source
    assert "fastboot erase" not in source
    assert "8d4bd3e013e56633" not in source


def test_demo_renderer_is_syntax_valid_and_retains_raw_logs() -> None:
    result = run("python3", "-m", "py_compile", str(DEMO_RENDERER))
    assert result.returncode == 0, result.stderr
    source = DEMO_RENDERER.read_text()
    assert '"raw-log" if path in inputs' in source
    assert "rejected request {request} unexpectedly emitted CONTROL" in source
    assert "dashboard-safe.png" in source

    recorder = run("python3", "-m", "py_compile", str(DEMO_RECORDER))
    assert recorder.returncode == 0, recorder.stderr
    recorder_source = DEMO_RECORDER.read_text()
    assert "TASK123_FFMPEG" in recorder_source
    assert 'role = "demo-video"' in recorder_source
    assert "TASK123_DEMO_VIDEO_PASS" in recorder_source


def test_task3_matrix_builder_hashes_external_inputs_and_never_touches_board() -> None:
    assert run("bash", "-n", str(TASK3_MATRIX_BUILD)).returncode == 0
    source = TASK3_MATRIX_BUILD.read_text()
    assert "INPUT-SHA256SUMS.txt" in source
    assert "TASK3_MODEL=fixed-perception" not in source
    assert 'build_arm fixed fixed-perception' in source
    assert 'build_arm rknn rknn' in source
    assert "build-hybrid-scene-payload.sh" in source
    assert "build-atk-zephyr-task123-unified.sh" in source
    assert "fastboot" not in source


def test_unified_builder_resolves_relative_artifact_inputs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture_root = Path(directory)
        (fixture_root / "kernel.bin").write_bytes(b"kernel")
        (fixture_root / "initrd.cpio").write_bytes(b"initrd")
        (fixture_root / "board.dtb").write_bytes(b"dtb")
        (fixture_root / "zephyr").mkdir()

        command = f"""
source {BUILD!s}
cd {fixture_root!s}
starry_kernel=kernel.bin
starry_initrd=initrd.cpio
host_dtb=board.dtb
zephyr_base=zephyr
normalize_input_paths
printf '%s\n' "$starry_kernel" "$starry_initrd" "$host_dtb" "$zephyr_base"
"""
        result = run("bash", "-c", command)

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            str(fixture_root / "kernel.bin"),
            str(fixture_root / "initrd.cpio"),
            str(fixture_root / "board.dtb"),
            str(fixture_root / "zephyr"),
        ]


def test_source_archive_revision_does_not_inherit_parent_repository_head() -> None:
    (REPO_ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=REPO_ROOT / "tmp") as directory:
        source = Path(directory)
        expected_revision = "dccb09599635bdff17633fa7e9dab014b91dce90"
        (source / ".task123-source-revision").write_text(expected_revision + "\n")

        result = run(
            "bash",
            "-c",
            f"source {TASK123_TOOLS!s}; task123_source_revision {source!s}",
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected_revision


def test_hybrid_starry_build_includes_virtual_network_driver() -> None:
    with STARRY_BUILD_CONFIG.open("rb") as config_file:
        config = tomllib.load(config_file)

    assert "ax-driver/virtio-net" in config["features"]
    assert config["max_cpu_num"] == 2


def test_starry_boot_cpu_is_dedicated_while_communication_cpu_is_shared() -> None:
    template = STARRY_VM_TEMPLATE.read_text()
    builder = BUILD.read_text()

    assert "phys_cpu_ids = [@STARRY_VCPU0_PCPU@, @STARRY_VCPU1_PCPU@]" in template
    assert 'topology="${ATK_TASK1_TOPOLOGY:-communication-share}"' in builder
    assert "communication-share)" in builder
    assert "starry_vcpu0_pcpu=0x200" in builder
    assert "starry_vcpu1_pcpu=0x100" in builder
    assert "ai-share)" in builder
    assert "starry_vcpu0_pcpu=0x100" in builder
    assert "starry_vcpu1_pcpu=0x200" in builder
    assert "topology=%s" in builder

    rknn_init = RKNN_SCENE_INIT.read_text()
    fixed_init = FIXED_SCENE_INIT.read_text()
    assert "communication_cpu=1 ai_cpu=0" in rknn_init
    assert 'taskset -c 1 "${payload_root}/bin/task2-net"' in rknn_init
    assert 'taskset -c 0 \\\n' in rknn_init
    assert "TASK3_HYBRID_SCENE_END source=rknn" in rknn_init
    assert "communication_cpu=1 ai_cpu=0" in fixed_init
    assert 'taskset -c 1 "${payload_root}/bin/task2-net"' in fixed_init


def test_rknn_scene_has_backpressure_and_a_bounded_evidence_window() -> None:
    rknn_init = RKNN_SCENE_INIT.read_text()

    assert "/rknn-control.ack" in rknn_init
    assert "--control-ack /rknn-control.ack" in rknn_init
    assert 'wait "$control_pid"' not in rknn_init
    assert ">/tmp/task3-controller.log 2>&1" in rknn_init
    assert "TASK3_HYBRID_SCENE_END source=rknn controller_complete=1" in rknn_init


def test_task1_pressure_retries_communication_startup_race() -> None:
    pressure_init = TASK1_PRESSURE_INIT.read_text()

    assert "TASK1_COMMUNICATION_ATTEMPT" in pressure_init
    assert "TASK1_COMMUNICATION_RETRY" in pressure_init
    assert 'taskset -c 1 "${payload_root}/bin/task2-net"' in pressure_init
    assert 'kill -0 "$controller_pid"' in pressure_init
    assert "supervisor_pid" not in pressure_init


def test_selector_resolves_frozen_zephyr_rr_and_fp_rr() -> None:
    with tempfile.TemporaryDirectory() as directory:
        environment = fixture_environment(Path(directory))
        for scheduler, expected_name in (
            ("rr", "axvisor-task123-zephyr-rr.fit"),
            ("fp-rr", "axvisor-task123-zephyr-fp-rr.fit"),
        ):
            result = run(str(SELECT), "zephyr", scheduler, environment=environment)
            assert result.returncode == 0, result.stderr
            assert f"scheduler={scheduler}" in result.stdout
            assert expected_name in result.stdout
            assert "sha256=" in result.stdout


def test_selector_keeps_rtthread_and_rejects_unbuilt_full_rr_arm() -> None:
    with tempfile.TemporaryDirectory() as directory:
        environment = fixture_environment(Path(directory))
        result = run(str(SELECT), "rtthread", "fp-rr", environment=environment)
        assert result.returncode == 0, result.stderr
        assert "axvisor-task123-integrated-fp-rr.fit" in result.stdout

        unsupported = run(str(SELECT), "rtthread", "rr", environment=environment)
        assert unsupported.returncode == 2
        assert "no frozen full Task 1/2/3 RT-Thread RR FIT" in unsupported.stderr
