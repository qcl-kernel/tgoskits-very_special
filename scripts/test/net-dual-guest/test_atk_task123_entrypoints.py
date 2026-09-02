from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD = REPO_ROOT / "scripts/board/build-atk-zephyr-task123-unified.sh"
TASK123_TOOLS = REPO_ROOT / "scripts/lib/task123-tools.sh"
RAM_BOOT = REPO_ROOT / "scripts/board/atk-dlrk3588-ram-boot.sh"
NATIVE_ZEPHYR_BOARD = REPO_ROOT / "scripts/board/run-atk-native-zephyr.sh"
TASK123_ENTRYPOINT = REPO_ROOT / "scripts/competition/task123.sh"
TASK123_README = REPO_ROOT / "scripts/competition/README-task123.md"
CI_REGRESSION = REPO_ROOT / "scripts/test/net-dual-guest/run-ci-regression.sh"
RUST_TOOLCHAIN = REPO_ROOT / "rust-toolchain.toml"
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


def write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(0o755)


def write_archive_manifest(directory: Path) -> None:
    manifest_lines = []
    for source in sorted(directory.iterdir()):
        if source.name == ".task123-tree-sha256" or not source.is_file():
            continue
        digest = subprocess.check_output(["sha256sum", source], text=True).split()[0]
        manifest_lines.append(f"{digest}  ./{source.name}\n")
    (directory / ".task123-tree-sha256").write_text("".join(manifest_lines))


def test_fresh_ubuntu_install_instructions_include_rustup() -> None:
    entrypoint = TASK123_ENTRYPOINT.read_text()
    install_hint = entrypoint.split(
        "Install common Ubuntu dependencies with:", 1
    )[1].split("\n\n", 1)[0]
    readme = TASK123_README.read_text()

    assert "rustup" in install_hint
    assert "rustup" in readme
    assert "pkg-config" in install_hint
    assert "pkg-config" in readme
    assert "libudev-dev" in install_hint
    assert "libudev-dev" in readme
    assert "libclang-dev" in install_hint
    assert "libclang-dev" in readme


def test_doctor_rejects_selected_zephyr_python_without_jsonschema() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture_root = Path(directory)
        fake_bin = fixture_root / "bin"
        fake_bin.mkdir()

        python = fake_bin / "python3"
        write_executable(
            python,
            """#!/usr/bin/env bash
case "${2:-}" in
    *jsonschema*) exit 1 ;;
    *) exit 0 ;;
esac
""",
        )

        for command in (
            "cargo",
            "rustup",
            "pkg-config",
            "clang",
            "cmake",
            "ninja",
            "qemu-system-aarch64",
            "qemu-aarch64",
            "debugfs",
            "e2fsck",
            "dtc",
            "flock",
        ):
            write_executable(fake_bin / command, "#!/usr/bin/env bash\nexit 0\n")

        fake_libclang = fixture_root / "libclang.so"
        fake_libclang.write_bytes(b"fixture")
        write_executable(
            fake_bin / "ldconfig",
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' 'libclang.so (libc6) => {fake_libclang!s}'\n",
        )

        ncnn = fixture_root / "ncnn"
        ncnn.mkdir()
        (ncnn / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n")
        real_git = shutil.which("git")
        assert real_git is not None
        write_executable(
            fake_bin / "git",
            f"""#!/usr/bin/env bash
if [[ "$1" == "-C" && "$2" == "{ncnn}" && "$3" == "rev-parse" ]]; then
    printf '%s\\n' 946fe3fb14a8dff8c06df763f67be522167b2f00
    exit 0
fi
exec {real_git} "$@"
""",
        )

        zephyr = fixture_root / "zephyr"
        zephyr.mkdir()
        (zephyr / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n")
        (zephyr / ".task123-source-revision").write_text(
            "dccb09599635bdff17633fa7e9dab014b91dce90\n"
        )
        manifest_lines = []
        for source in (zephyr / ".task123-source-revision", zephyr / "CMakeLists.txt"):
            digest = subprocess.check_output(
                ["sha256sum", source], text=True
            ).split()[0]
            manifest_lines.append(f"{digest}  ./{source.name}\n")
        (zephyr / ".task123-tree-sha256").write_text("".join(manifest_lines))

        pnnx = fake_bin / "pnnx"
        write_executable(pnnx, "#!/usr/bin/env bash\nexit 0\n")
        onnx = fixture_root / "yolo11n.onnx"
        onnx.write_bytes(b"fixture")
        real_sha256sum = shutil.which("sha256sum")
        assert real_sha256sum is not None
        write_executable(
            fake_bin / "sha256sum",
            f"""#!/usr/bin/env bash
if [[ "$#" == 1 && "$1" == "{onnx}" ]]; then
    printf '%s  %s\\n' 634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b "$1"
    exit 0
fi
exec {real_sha256sum} "$@"
""",
        )

        cross_tools = {}
        for name in ("CC", "CXX", "AR", "RANLIB"):
            tool = fake_bin / f"cross-{name.lower()}"
            write_executable(tool, "#!/usr/bin/env bash\nexit 0\n")
            cross_tools[f"CROSS_{name}"] = str(tool)

        environment = os.environ.copy()
        environment.update(cross_tools)
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "TASK123_PYTHON": str(python),
                "NCNN_SOURCE": str(ncnn),
                "PNNX": str(pnnx),
                "ZEPHYR_BASE": str(zephyr),
                "YOLO_ONNX": str(onnx),
            }
        )

        result = run(
            "bash", str(TASK123_ENTRYPOINT), "doctor", environment=environment
        )

        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        assert "jsonschema" in output


def test_doctor_discovers_and_validates_all_build_dependencies() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture_root = Path(directory)
        fake_bin = fixture_root / "bin"
        deps_root = fixture_root / "deps"
        download_cache = fixture_root / "downloads"
        fixture_repo = fixture_root / "repository"
        fixture_entrypoint = fixture_repo / "scripts" / "competition" / "task123.sh"
        fixture_tools = fixture_repo / "scripts" / "lib" / "task123-tools.sh"
        fake_bin.mkdir()
        download_cache.mkdir()
        fixture_entrypoint.parent.mkdir(parents=True)
        fixture_tools.parent.mkdir(parents=True)
        shutil.copy2(TASK123_ENTRYPOINT, fixture_entrypoint)
        shutil.copy2(TASK123_TOOLS, fixture_tools)

        for command in (
            "cargo",
            "rustup",
            "python3",
            "pkg-config",
            "clang",
            "cmake",
            "ninja",
            "qemu-system-aarch64",
            "qemu-aarch64",
            "debugfs",
            "e2fsck",
            "dtc",
            "flock",
        ):
            write_executable(fake_bin / command, "#!/usr/bin/env bash\nexit 0\n")

        fake_libclang = fixture_root / "libclang.so"
        fake_libclang.write_bytes(b"fixture")
        write_executable(
            fake_bin / "ldconfig",
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' 'libclang.so (libc6) => {fake_libclang!s}'\n",
        )
        write_executable(
            fake_bin / "git",
            "#!/usr/bin/env bash\nprintf 'fixture-commit\\n'\n",
        )

        cross_bin = deps_root / "aarch64-linux-musl-cross" / "bin"
        cross_bin.mkdir(parents=True)
        for command in (
            "aarch64-linux-musl-gcc",
            "aarch64-linux-musl-g++",
            "aarch64-linux-musl-ar",
            "aarch64-linux-musl-ranlib",
        ):
            write_executable(cross_bin / command, "#!/usr/bin/env bash\nexit 0\n")

        ncnn_revision = "946fe3fb14a8dff8c06df763f67be522167b2f00"
        ncnn = deps_root / f"ncnn-{ncnn_revision}"
        ncnn.mkdir()
        (ncnn / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n")
        (ncnn / ".task123-source-revision").write_text(f"{ncnn_revision}\n")

        zephyr_revision = "dccb09599635bdff17633fa7e9dab014b91dce90"
        zephyr = deps_root / f"zephyr-{zephyr_revision}"
        zephyr.mkdir()
        (zephyr / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n")
        (zephyr / ".task123-source-revision").write_text(f"{zephyr_revision}\n")
        write_archive_manifest(zephyr)

        pnnx = deps_root / "pnnx-20260526-linux" / "pnnx"
        pnnx.parent.mkdir()
        write_executable(pnnx, "#!/usr/bin/env bash\nexit 0\n")

        managed_python = deps_root / f"zephyr-python-{zephyr_revision}" / "bin" / "python3"
        managed_python.parent.mkdir(parents=True)
        write_executable(managed_python, "#!/usr/bin/env bash\nexit 0\n")

        onnx = download_cache / "yolo11n.onnx"
        onnx.write_bytes(b"fixture")
        real_sha256sum = shutil.which("sha256sum")
        assert real_sha256sum is not None
        write_executable(
            fake_bin / "sha256sum",
            f"""#!/usr/bin/env bash
if [[ "$#" == 1 && "$1" == "{onnx}" ]]; then
    printf '%s  %s\\n' 634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b "$1"
    exit 0
fi
exec {real_sha256sum} "$@"
""",
        )

        environment = os.environ.copy()
        for name in (
            "CROSS_ROOT",
            "CROSS_CC",
            "CROSS_CXX",
            "CROSS_AR",
            "CROSS_RANLIB",
            "CROSS_COMPILE",
            "TASK123_PYTHON",
            "NCNN_SOURCE",
            "PNNX",
            "ZEPHYR_BASE",
            "YOLO_ONNX",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "TASK123_DEPS_DIR": str(deps_root),
                "TASK123_DOWNLOAD_CACHE": str(download_cache),
            }
        )

        result = run(
            "bash", str(fixture_entrypoint), "doctor", environment=environment
        )

        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert "DOCTOR_PASS" in output

        write_executable(fake_bin / "pkg-config", "#!/usr/bin/env bash\nexit 1\n")
        missing_libudev = run(
            "bash", str(fixture_entrypoint), "doctor", environment=environment
        )

        missing_output = missing_libudev.stdout + missing_libudev.stderr
        assert missing_libudev.returncode != 0, missing_output
        assert "libudev" in missing_output

        write_executable(fake_bin / "pkg-config", "#!/usr/bin/env bash\nexit 0\n")
        write_executable(fake_bin / "ldconfig", "#!/usr/bin/env bash\nexit 0\n")
        missing_libclang = run(
            "bash", str(fixture_entrypoint), "doctor", environment=environment
        )

        missing_output = missing_libclang.stdout + missing_libclang.stderr
        assert missing_libclang.returncode != 0, missing_output
        assert "libclang" in missing_output


def test_task123_toolchain_installs_starry_endpoint_target() -> None:
    with RUST_TOOLCHAIN.open("rb") as source:
        toolchain = tomllib.load(source)["toolchain"]

    assert "aarch64-unknown-linux-musl" in toolchain["targets"]


def test_starry_staging_checks_the_configured_image_extract_directory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture_root = Path(directory)
        fixture_repo = fixture_root / "repository"
        fixture_entrypoint = fixture_repo / "scripts" / "competition" / "task123.sh"
        fixture_tools = fixture_repo / "scripts" / "lib" / "task123-tools.sh"
        fake_bin = fixture_root / "bin"
        e2fsck_log = fixture_root / "e2fsck.log"
        extract_dir = fixture_root / "images"
        rootfs = extract_dir / "rootfs-aarch64-alpine.img"

        fixture_entrypoint.parent.mkdir(parents=True)
        fixture_tools.parent.mkdir(parents=True)
        fake_bin.mkdir()
        extract_dir.mkdir()
        source, separator, _ = TASK123_ENTRYPOINT.read_text().rpartition('\nmain "$@"')
        assert separator
        fixture_entrypoint.write_text(f"{source}\n")
        shutil.copy2(TASK123_TOOLS, fixture_tools)
        rootfs.write_bytes(b"rootfs")

        starry_elf = (
            fixture_repo
            / "target"
            / "aarch64-unknown-none-softfloat"
            / "release"
            / "starryos"
        )
        starry_elf.parent.mkdir(parents=True)
        starry_elf.write_bytes(b"registered virtio network device")

        write_executable(fake_bin / "cargo", "#!/usr/bin/env bash\nexit 0\n")
        write_executable(
            fake_bin / "e2fsck",
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$E2FSCK_LOG\"\n",
        )

        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "TGOS_IMAGE_EXTRACT_DIR": str(extract_dir),
                "E2FSCK_LOG": str(e2fsck_log),
            }
        )
        result = run(
            "bash",
            "-c",
            f"source {fixture_entrypoint!s}; stage_starry_task23_guest",
            environment=environment,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert e2fsck_log.read_text().splitlines() == [
            f"-fy {rootfs}",
            f"-fn {rootfs}",
        ]


def test_zephyr_python_resolution_preserves_virtual_environment_path() -> None:
    with tempfile.TemporaryDirectory() as directory:
        deps_root = Path(directory)
        revision = "test-revision"
        python = deps_root / f"zephyr-python-{revision}" / "bin" / "python3"
        python.parent.mkdir(parents=True)
        host_python = shutil.which("python3")
        assert host_python is not None
        python.symlink_to(host_python)

        environment = os.environ.copy()
        environment.pop("TASK123_PYTHON", None)
        environment["TASK123_DEPS_DIR"] = str(deps_root)
        result = run(
            "bash",
            "-c",
            f"source {TASK123_TOOLS!s}; "
            f"resolve_task123_python {REPO_ROOT!s} {revision}",
            environment=environment,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(python)


def test_task123_rootfs_resolution_honors_isolated_image_directory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture_root = Path(directory)
        repository = fixture_root / "repository"
        extract_dir = fixture_root / "images"
        explicit_rootfs = fixture_root / "explicit.img"
        environment = os.environ.copy()
        environment.pop("STARRY_TASK23_ROOTFS", None)
        environment["TGOS_IMAGE_EXTRACT_DIR"] = str(extract_dir)

        isolated = run(
            "bash",
            "-c",
            f"source {TASK123_TOOLS!s}; resolve_task123_rootfs {repository!s}",
            environment=environment,
        )

        assert isolated.returncode == 0, isolated.stdout + isolated.stderr
        assert isolated.stdout.strip() == str(extract_dir / "rootfs-aarch64-alpine.img")

        environment["STARRY_TASK23_ROOTFS"] = str(explicit_rootfs)
        explicit = run(
            "bash",
            "-c",
            f"source {TASK123_TOOLS!s}; resolve_task123_rootfs {repository!s}",
            environment=environment,
        )

        assert explicit.returncode == 0, explicit.stdout + explicit.stderr
        assert explicit.stdout.strip() == str(explicit_rootfs)


def test_task123_python_check_requires_pytest() -> None:
    with tempfile.TemporaryDirectory() as directory:
        python = Path(directory) / "python3"
        write_executable(
            python,
            "#!/usr/bin/env bash\n"
            "case \"${2:-}\" in\n"
            "    *pytest*) exit 1 ;;\n"
            "    *) exit 0 ;;\n"
            "esac\n",
        )

        result = run(
            "bash",
            "-c",
            f"source {TASK123_TOOLS!s}; task123_check_zephyr_python {python!s}",
        )

        assert result.returncode != 0
        assert "pytest" in result.stderr


def test_ci_gate_uses_configured_task123_python() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture_root = Path(directory)
        fake_bin = fixture_root / "bin"
        fake_bin.mkdir()
        invocation_log = fixture_root / "python-invocations"

        write_executable(fake_bin / "cargo", "#!/usr/bin/env bash\nexit 0\n")
        write_executable(
            fake_bin / "python3",
            "#!/usr/bin/env bash\n"
            "printf 'host python must not run\\n' >&2\n"
            "exit 37\n",
        )
        task123_python = fixture_root / "task123-python"
        write_executable(
            task123_python,
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {invocation_log!s}\n",
        )

        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "TASK123_PYTHON": str(task123_python),
            }
        )
        result = run("bash", str(CI_REGRESSION), environment=environment)

        assert result.returncode == 0, result.stdout + result.stderr
        invocations = invocation_log.read_text().splitlines()
        assert invocations[0] == "-m pytest -q scripts/test/net-dual-guest"
        assert invocations[1].startswith("-m pytest -q scripts/task3/test_")
        assert invocations[1].endswith(".py")


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
    legacy_boot = source.split("boot_legacy_uimage_from_ram() {", 1)[1].split(
        "\n}", 1
    )[0]
    assert 'boot_format="${ATK_BOOT_FORMAT:-fit}"' in source
    assert "legacy-uimage" in source
    assert "bootm start $FASTBOOT_DOWNLOAD_BUFFER" in legacy_boot
    assert 'legacy_load_address="${ATK_LEGACY_LOAD_ADDRESS:-}"' in source
    assert 'legacy_payload_size="${ATK_LEGACY_PAYLOAD_SIZE:-}"' in source
    assert 'legacy_entry_address="${ATK_LEGACY_ENTRY_ADDRESS:-}"' in source
    assert "cp.b 0x00c00840" in legacy_boot
    assert "bootm loados" not in legacy_boot
    assert "bootm go" not in legacy_boot
    assert 'send_console "booti $legacy_load_address"' in legacy_boot
    assert "fastboot flash" not in source
    assert "fastboot erase" not in source


def test_ram_boot_does_not_hardcode_one_fastboot_device() -> None:
    source = RAM_BOOT.read_text()
    assert 'fastboot_sn="${ATK_FASTBOOT_SN:-}"' in source
    assert "found multiple fastboot devices; set ATK_FASTBOOT_SN" in source
    assert "resolve_fastboot_serial" in source
    assert 'fastboot -s "$fastboot_sn" stage' in source
    assert "8d4bd3e013e56633" not in source


def test_native_zephyr_board_runner_archives_strict_evidence() -> None:
    assert run("bash", "-n", str(NATIVE_ZEPHYR_BOARD)).returncode == 0
    source = NATIVE_ZEPHYR_BOARD.read_text()
    assert "mkimage" in source
    assert "ATK_BOOT_FORMAT=fit" in source
    assert 'kernel = "kernel-1"' in source
    assert 'fdt = "fdt-1"' in source
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
        "task2-throughput",
        "task3-build",
        "task3-matrix",
        "task3-all",
        "demo",
        "demo-video",
    ):
        assert command in result.stdout
    assert "task1-ai" not in result.stdout
    ablation = run("bash", str(TASK123_ENTRYPOINT), "board", "task1-ai")
    assert ablation.returncode != 0
    assert "task1-ai is a non-official ablation" in ablation.stderr
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
