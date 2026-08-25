import importlib.util
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
ENTRYPOINT = REPO / "scripts/competition/task123.sh"
SCENARIO_RUNNER = ROOT / "run-starry-task23-scenario.sh"
RENDER_SCRIPT = ROOT / "render_qemu_runtime.py"
SPEC = importlib.util.spec_from_file_location("render_qemu_runtime", RENDER_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
VM_RENDER_SCRIPT = ROOT / "render_vm_runtime.py"
VM_SPEC = importlib.util.spec_from_file_location("render_vm_runtime", VM_RENDER_SCRIPT)
assert VM_SPEC is not None and VM_SPEC.loader is not None
VM_MODULE = importlib.util.module_from_spec(VM_SPEC)
VM_SPEC.loader.exec_module(VM_MODULE)


class Task123RuntimeIsolationTest(unittest.TestCase):
    def test_public_full_suite_is_exactly_the_ten_behavioral_scenarios(self) -> None:
        expected = [
            "task3-yolo-smoke",
            "task1-scheduler-ab",
            "task2-normal",
            "task23-integrated",
            "task2-drop-ack",
            "task2-retry-exhausted",
            "task2-blackout",
            "task2-out-of-order",
            "task2-invalid-parameter",
            "task3-model-rejected",
        ]
        listed = subprocess.run(
            ["bash", "scripts/competition/task123.sh", "--list"],
            cwd=REPO,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        scenario_block = listed.split("Scenarios:\n", 1)[1].split("\nGates:\n", 1)[0]
        listed_scenarios = [
            line.split()[0] for line in scenario_block.splitlines() if line.strip()
        ]
        self.assertEqual(listed_scenarios, expected)

        entrypoint = ENTRYPOINT.read_text()
        full_suite = entrypoint.split("        full)\n", 1)[1].split(
            "            ;;", 1
        )[0]
        positions = [full_suite.index(scenario) for scenario in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("ci-contracts", full_suite)

    def test_task2_suite_uses_a_model_independent_scenario(self) -> None:
        entrypoint = ENTRYPOINT.read_text()
        runner = SCENARIO_RUNNER.read_text()

        task2_suite = entrypoint.split("        task2)\n", 1)[1].split(
            "            ;;", 1
        )[0]

        self.assertIn("task2-normal", entrypoint)
        self.assertIn("task23-integrated", entrypoint)
        self.assertIn('STARRY_TASK23_SCOPE=task2', entrypoint)
        self.assertIn("task2-normal", task2_suite)
        self.assertNotIn("task23-integrated", task2_suite)
        self.assertIn('task_scope="${STARRY_TASK23_SCOPE:-integrated}"', runner)
        self.assertIn('if [[ "$task_scope" == integrated ]]', runner)

    def test_task2_normal_waits_for_the_endpoint_run_mode_marker(self) -> None:
        runner = SCENARIO_RUNNER.read_text()

        self.assertIn('task2_ready_mode="task2"', runner)
        self.assertIn(
            "printf 'expect 30 TASK2_CONTROLLER_READY mode=%s\\n' "
            '"$task2_ready_mode"',
            runner,
        )

    def test_blackout_waits_for_the_selected_run_mode_recovery_marker(self) -> None:
        runner = SCENARIO_RUNNER.read_text()

        self.assertIn(
            "printf 'expect 180 STARRY_T2N1_FAULT_RECOVERY_COMPLETE "
            "mode=%s.*safe_observed=true recovered=true\\n' \"$run_mode\"",
            runner,
        )

    def test_drop_ack_keeps_starry_attached_until_retransmit_line_is_complete(self) -> None:
        runner = SCENARIO_RUNNER.read_text()
        drop_ack = runner.split("        drop-ack)\n", 1)[1].split(
            "            ;;", 1
        )[0]

        retransmit = drop_ack.index("STARRY_T2N1_RETRANSMIT seq=1 attempt=1")
        attach_zephyr = drop_ack.index("printf 'attach 2\\n'")
        self.assertLess(retransmit, attach_zephyr)
        self.assertLess(drop_ack.index("STARRY_T2N1_PASS"), attach_zephyr)
        self.assertGreater(drop_ack.index("TASK2_FAULT_DROP_ACK seq=1"), attach_zephyr)
        self.assertNotIn(
            "STARRY_T2N1_FAULT_RECOVERY_COMPLETE "
            "mode=normal.*safe_observed=true recovered=true",
            runner,
        )
        self.assertNotIn(
            "printf 'expect 30 TASK2_CONTROLLER_READY mode=%s\\n' \"$scenario\"",
            runner,
        )

    def test_task_specific_one_click_entrypoints_are_discoverable(self) -> None:
        listed = subprocess.run(
            ["bash", "scripts/competition/task123.sh", "--list"],
            cwd=REPO,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        for suite in ("task1", "task2", "task3", "full"):
            self.assertIn(f"  {suite}", listed)

        tasks = json.loads((REPO / ".vscode/tasks.json").read_text())["tasks"]
        labels = {task.get("label") for task in tasks}
        for label in (
            "Competition: Run Task 1",
            "Competition: Run Task 2",
            "Competition: Run Task 3",
            "Competition: Run Full Validation",
        ):
            self.assertIn(label, labels)

    def test_judge_entrypoint_honors_cross_root_for_yolo_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cross_bin = root / "cross" / "bin"
            cross_bin.mkdir(parents=True)
            compiler = cross_bin / "aarch64-linux-musl-g++"
            compiler.write_text(
                "#!/bin/sh\n"
                "while [ \"$1\" != -o ]; do shift; done\n"
                "shift\n"
                "printf '#!/bin/sh\\nexit 0\\n' > \"$1\"\n"
                "chmod +x \"$1\"\n"
            )
            compiler.chmod(0o755)
            qemu = root / "qemu-aarch64"
            qemu.write_text("#!/bin/sh\nexec \"$1\"\n")
            qemu.chmod(0o755)
            input_path = root / "input.ppm"
            input_path.write_bytes(b"P6\n1 1\n255\n\0\0\0")

            environment = os.environ.copy()
            for variable in (
                "CROSS_CC",
                "CROSS_CXX",
                "CROSS_AR",
                "CROSS_RANLIB",
                "CROSS_COMPILE",
            ):
                environment.pop(variable, None)
            environment.update(
                {
                    "ALLOW_DIRTY": "1",
                    "CROSS_ROOT": str(root / "cross"),
                    "QEMU_AARCH64": str(qemu),
                    "TASK3_NCNN_INPUT": str(input_path),
                    "TASK123_EVIDENCE_DIR": str(root / "evidence"),
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    "scripts/competition/task123.sh",
                    "run",
                    "task3-yolo-smoke",
                ],
                cwd=REPO,
                env=environment,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TASK123_SCENARIO_PASS name=task3-yolo-smoke", result.stdout)

    def test_judge_entrypoint_does_not_discover_user_local_cross_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cross_bin = (
                root
                / ".local"
                / "toolchains"
                / "aarch64-linux-musl-cross"
                / "bin"
            )
            cross_bin.mkdir(parents=True)
            compiler = cross_bin / "aarch64-linux-musl-g++"
            compiler.write_text(
                "#!/bin/sh\n"
                "while [ \"$1\" != -o ]; do shift; done\n"
                "shift\n"
                "printf '#!/bin/sh\\nexit 0\\n' > \"$1\"\n"
                "chmod +x \"$1\"\n"
            )
            compiler.chmod(0o755)
            qemu = root / "qemu-aarch64"
            qemu.write_text("#!/bin/sh\nexec \"$1\"\n")
            qemu.chmod(0o755)
            input_path = root / "input.ppm"
            input_path.write_bytes(b"P6\n1 1\n255\n\0\0\0")

            environment = os.environ.copy()
            for variable in (
                "CROSS_ROOT",
                "CROSS_CC",
                "CROSS_CXX",
                "CROSS_AR",
                "CROSS_RANLIB",
                "CROSS_COMPILE",
            ):
                environment.pop(variable, None)
            environment.update(
                {
                    "ALLOW_DIRTY": "1",
                    "HOME": str(root),
                    "TASK123_DEPS_DIR": str(root / "repository-deps"),
                    "QEMU_AARCH64": str(qemu),
                    "TASK3_NCNN_INPUT": str(input_path),
                    "TASK123_EVIDENCE_DIR": str(root / "evidence"),
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    "scripts/competition/task123.sh",
                    "run",
                    "task3-yolo-smoke",
                ],
                cwd=REPO,
                env=environment,
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("aarch64-linux-musl-g++ was not found", result.stderr)

    def test_task3_runners_do_not_use_global_process_or_tmp_cleanup(self) -> None:
        runners = [
            REPO / "scripts/task3/run-task3-experiment.sh",
            REPO / "scripts/task3/run-task3-fault.sh",
            REPO / "scripts/task3/run-task3-switch.sh",
            REPO / "scripts/task3/run-task3-switch-fault.sh",
        ]
        forbidden = (
            'pkill -f "tg-xtask axvisor qemu"',
            'pkill -f "qemu-system-[a]arch64"',
            'pkill -f "ack_drop_proxy.py"',
            'log="/tmp/task3-',
            'steps="/tmp/task3-',
        )
        for runner in runners:
            source = runner.read_text()
            for fragment in forbidden:
                self.assertNotIn(fragment, source, f"{runner}: {fragment}")

    def test_task123_builders_do_not_assume_a_home_or_fixed_tmp_toolchain(self) -> None:
        builders = [
            REPO / "scripts/test/rt-partition/run-p1-comparison.sh",
            REPO / "scripts/test/rt-partition/build-rt-tools.sh",
            ROOT / "build-tools.sh",
            ROOT / "build-rtthread-periodic.sh",
            ROOT / "build-rtthread-task2.sh",
        ]
        forbidden = ("/home/huhu/", "$HOME/", "/tmp/rtthread-toolchain", "/tmp/rtthread-scons")
        for builder in builders:
            source = builder.read_text()
            for fragment in forbidden:
                self.assertNotIn(fragment, source, f"{builder}: {fragment}")

    def test_model_and_temporary_directory_overrides_reach_all_consumers(self) -> None:
        initramfs_builder = (ROOT / "build-linux-initramfs.sh").read_text()
        ncnn_smoke = (REPO / "scripts/task3/run-ncnn-smoke.sh").read_text()
        rt_tools = (REPO / "scripts/test/rt-partition/build-rt-tools.sh").read_text()

        self.assertIn(
            'YOLO_AB_OUT_DIR="$ab_model_dir"',
            initramfs_builder,
        )
        self.assertIn(
            'model_dir="${TASK3_NCNN_MODEL_DIR:-',
            ncnn_smoke,
        )
        self.assertIn('"$model_dir/yolo11n.ncnn.param"', ncnn_smoke)
        self.assertIn('"$model_dir/yolo11n.ncnn.bin"', ncnn_smoke)
        self.assertNotIn(
            '"$repo_root/tmp/task3-yolo/ncnn-model/yolo11n.ncnn.param"',
            ncnn_smoke,
        )
        self.assertIn('temporary_parent="${TMPDIR:-/tmp}"', initramfs_builder)
        self.assertIn('temporary_parent="${TMPDIR:-/tmp}"', rt_tools)
        self.assertNotIn("mktemp -d /tmp/", initramfs_builder)
        self.assertNotIn("mktemp -d /tmp/", rt_tools)
        self.assertIn(
            "default .deps/rt-partition/src",
            rt_tools,
        )

    def test_task1_and_task3_smoke_runners_do_not_start_task2_control(self) -> None:
        task1_runners = (
            ROOT / "run-starry-task1-periodic-ab.sh",
            ROOT / "run-starry-task1-periodic-rtthread-ab.sh",
        )
        for runner in task1_runners:
            source = runner.read_text()
            self.assertIn("t2n1-run.sh model-only", source, str(runner))
            self.assertNotIn("t2n1-run.sh normal", source, str(runner))

        build_smoke = tomllib.loads(
            (REPO / "scripts/competition/qemu-aarch64-starry-build-smoke.toml").read_text()
        )
        self.assertEqual(
            build_smoke["shell_init_cmd"],
            "sh /usr/bin/t2n1-run.sh model-only",
        )

    def test_task1_formal_qemu_matrix_covers_idle_and_pressure_once(self) -> None:
        matrix = (
            REPO / "scripts/competition/run-starry-task1-qemu-matrix.sh"
        ).read_text()
        runner = (ROOT / "run-starry-task1-periodic-ab.sh").read_text()

        self.assertIn('sample_count="${TASK1_SAMPLE_COUNT:-6000}"', matrix)
        self.assertEqual(matrix.count('STARRY_TASK1_PERIODIC_REPEATS=1'), 2)
        self.assertNotIn('STARRY_TASK1_PERIODIC_REPEATS=3', matrix)
        self.assertNotIn('build_probe 60000', matrix)
        self.assertIn('STARRY_TASK1_LOAD_MODE=idle', matrix)
        self.assertIn('STARRY_TASK1_LOAD_MODE=yolo', matrix)
        self.assertIn('"$output_root/idle-ab-$sample_count"', matrix)
        self.assertIn('"$output_root/pressure-ab-$sample_count"', matrix)
        self.assertIn('repeats="${STARRY_TASK1_PERIODIC_REPEATS:-1}"', runner)
        self.assertIn('guest_duration_sec=$(((sample_count * 10 + 999) / 1000))', runner)
        self.assertIn('measurement_timeout_sec="${STARRY_TASK1_MEASUREMENT_TIMEOUT_SEC:-$((guest_duration_sec * 4 + 600))}"', runner)
        self.assertIn('qemu_timeout_sec="${STARRY_TASK1_QEMU_TIMEOUT_SEC:-$((guest_duration_sec * 4 + 900))}"', runner)
        self.assertIn('ZEPHYR_DUMP_GATED=1', matrix)
        self.assertIn("PERIODIC LATENCY SAMPLING COMPLETE", runner)
        self.assertIn("PERIODIC LATENCY CHUNK end=", runner)

    def test_p1_comparison_default_rootfs_is_an_image_not_a_child_path(self) -> None:
        runner = (REPO / "scripts/test/rt-partition/run-p1-comparison.sh").read_text()

        self.assertIn(
            "tmp/axbuild/rootfs/rootfs-aarch64-alpine.img}",
            runner,
        )
        self.assertNotIn(
            "rootfs-aarch64-alpine.img/rootfs-aarch64-alpine.img",
            runner,
        )

    def test_two_runtime_directories_are_independently_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["TASK123_RUNTIME_PARENT"] = directory
            subprocess.run(
                [
                    "bash",
                    "-c",
                    """
                    set -euo pipefail
                    source scripts/lib/task123-tools.sh
                    first="$(create_task123_runtime_dir)"
                    second="$(create_task123_runtime_dir)"
                    test "$first" != "$second"
                    touch "$first/first.sock" "$second/second.sock"
                    remove_task123_runtime_dir "$first"
                    test ! -e "$first"
                    test -f "$second/second.sock"
                    remove_task123_runtime_dir "$second"
                    test ! -e "$second"
                    """,
                ],
                cwd=REPO,
                env=environment,
                check=True,
            )

    def test_heavy_qemu_runners_share_one_configurable_execution_lock(self) -> None:
        runners = (
            REPO / "scripts/test/rt-partition/run-cyclictest.sh",
            REPO / "scripts/test/rt-partition/run-native-zephyr.sh",
            REPO / "scripts/test/rt-partition/run-timer-wheel-lock-ab.sh",
            ROOT / "run-starry-task1-periodic-ab.sh",
            ROOT / "run-starry-task1-periodic-rtthread-ab.sh",
            SCENARIO_RUNNER,
        )
        for runner in runners:
            self.assertIn(
                'acquire_task123_qemu_slot "$repo_root"',
                runner.read_text(),
                str(runner),
            )

        with tempfile.TemporaryDirectory() as directory:
            lock_file = Path(directory) / "qemu.lock"
            environment = os.environ.copy()
            environment["TASK123_QEMU_LOCK_FILE"] = str(lock_file)
            holder = subprocess.Popen(
                [
                    "bash",
                    "-c",
                    """
                    set -euo pipefail
                    source scripts/lib/task123-tools.sh
                    acquire_task123_qemu_slot "$PWD"
                    printf 'ready\\n'
                    read -r _
                    """,
                ],
                cwd=REPO,
                env=environment,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert holder.stdout is not None
            self.assertEqual(holder.stdout.readline().strip(), "ready")

            contender_environment = environment.copy()
            contender_environment["TASK123_QEMU_LOCK_TIMEOUT_SEC"] = "1"
            blocked = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source scripts/lib/task123-tools.sh; '
                    'acquire_task123_qemu_slot "$PWD"',
                ],
                cwd=REPO,
                env=contender_environment,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("QEMU execution slot", blocked.stderr)

            assert holder.stdin is not None
            holder.stdin.write("release\n")
            holder.stdin.flush()
            self.assertEqual(holder.wait(timeout=5), 0)
            holder.stdin.close()
            holder.stdout.close()
            assert holder.stderr is not None
            holder.stderr.close()

            acquired = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source scripts/lib/task123-tools.sh; '
                    'acquire_task123_qemu_slot "$PWD"',
                ],
                cwd=REPO,
                env=contender_environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(
                acquired.returncode,
                0,
                acquired.stdout + acquired.stderr,
            )

    def test_dirty_source_identity_records_patch_and_untracked_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repo"
            evidence = Path(directory) / "evidence"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            (repository / "tracked.txt").write_text("before\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Task123 Test",
                    "-c",
                    "user.email=task123@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                cwd=repository,
                check=True,
            )
            (repository / "tracked.txt").write_text("after\n")
            (repository / "untracked.txt").write_text("new\n")
            subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; write_task123_source_identity "$2" "$3"',
                    "bash",
                    str(REPO / "scripts/lib/task123-tools.sh"),
                    str(repository),
                    str(evidence),
                ],
                check=True,
            )

            identity = (evidence / "source-identity.txt").read_text()
            untracked = (evidence / "untracked-files.txt").read_text()
            patch = (evidence / "worktree.patch").read_text()

        self.assertIn("worktree=dirty", identity)
        self.assertIn("untracked.txt", untracked)
        self.assertIn("-before", patch)
        self.assertIn("+after", patch)

    def test_runtime_qemu_configs_get_distinct_socket_paths(self) -> None:
        source_text = """
args = [
  "-serial", "unix:@SERIAL_SOCKET@,server,nowait",
  "-drive", "id=disk0,if=none,format=raw,file=@ROOTFS@",
  "-qmp", "unix:@QMP_SOCKET@,server,nowait",
]
timeout = 30
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.toml"
            source.write_text(source_text)
            rootfs = root / "rootfs.img"
            rootfs.touch()
            rendered = []
            for run in ("one", "two"):
                run_dir = root / run
                run_dir.mkdir()
                output = run_dir / "qemu.toml"
                serial = run_dir / "serial.sock"
                qmp = run_dir / "qmp.sock"
                MODULE.render_qemu_runtime(source, output, rootfs, serial, qmp)
                rendered.append(tomllib.loads(output.read_text())["args"])

        self.assertNotEqual(rendered[0][1], rendered[1][1])
        self.assertNotEqual(rendered[0][5], rendered[1][5])
        self.assertIn("file=" + str(rootfs), rendered[0][3])

    def test_all_evidence_capture_configs_render_without_shared_paths(self) -> None:
        configs = [
            "qemu-aarch64-starry-zephyr-switch-msix1-capture.toml",
            "qemu-aarch64-starry-rtthread-switch-msix1-capture.toml",
            "qemu-aarch64-starry-zephyr-task1-capture.toml",
            "qemu-aarch64-starry-rtthread-task1-capture.toml",
            "qemu-aarch64-starry-rtthread-task1-periodic-capture.toml",
        ]
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            rootfs = runtime / "rootfs.img"
            rootfs.touch()
            for index, config_name in enumerate(configs):
                run_dir = runtime / str(index)
                run_dir.mkdir()
                output = run_dir / "qemu.toml"
                serial = run_dir / "serial.sock"
                qmp = run_dir / "qmp.sock"
                MODULE.render_qemu_runtime(
                    ROOT / config_name, output, rootfs, serial, qmp
                )
                arguments = tomllib.loads(output.read_text())["args"]
                self.assertIn(f"unix:{serial}", arguments[arguments.index("-serial") + 1])
                self.assertIn(f"unix:{qmp}", arguments[arguments.index("-qmp") + 1])

    def test_headless_qemu_config_gets_owned_qmp_rootfs_and_captures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            output = runtime / "qemu.toml"
            rootfs = runtime / "rootfs.img"
            qmp = runtime / "qmp.sock"
            capture_prefix = runtime / "capture"
            MODULE.render_qemu_runtime(
                ROOT / "qemu-aarch64-p2.toml",
                output,
                rootfs,
                None,
                qmp,
                capture_prefix,
                {12721: 43127},
            )
            arguments = tomllib.loads(output.read_text())["args"]

        self.assertIn(f"unix:{qmp}", arguments[arguments.index("-qmp") + 1])
        self.assertTrue(any(f"file={rootfs}" in value for value in arguments))
        self.assertTrue(any(f"file={capture_prefix}.vm1.pcap" in value for value in arguments))
        self.assertTrue(any(f"file={capture_prefix}.vm2.pcap" in value for value in arguments))
        self.assertFalse(any("127.0.0.1:12721" in value for value in arguments))
        self.assertEqual(
            sum("127.0.0.1:43127" in value for value in arguments),
            2,
        )

    def test_vm_runtime_renderer_replaces_only_requested_guest_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            output = runtime / "vm.toml"
            ramdisk = runtime / "selected.cpio.gz"
            VM_MODULE.render_vm_runtime(
                ROOT / "vm-aarch64-p2-switch-linux.toml",
                output,
                ramdisk_path=ramdisk,
            )
            config = tomllib.loads(output.read_text())

        self.assertEqual(config["kernel"]["ramdisk_path"], str(ramdisk))
        self.assertIn("${workspace}", config["kernel"]["kernel_path"])


if __name__ == "__main__":
    unittest.main()
