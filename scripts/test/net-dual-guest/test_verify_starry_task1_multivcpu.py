#!/usr/bin/env python3
"""Deterministic tests for Task 1 multi-vCPU evidence checks."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_starry_task1_multivcpu.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("verify_task1_multivcpu", MODULE_PATH)
assert SPEC and SPEC.loader
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class VerifyTask1MultivcpuTests(unittest.TestCase):
    def test_checked_in_static_contract_is_valid(self) -> None:
        self.assertEqual(VERIFY.verify_static_contract(MODULE_PATH.parents[3]), [])

    def test_runtime_rejects_single_vcpu_and_dead_workloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "run.log").write_text(
                "use Round-robin scheduler.\n"
                "TASK1_TOPOLOGY_CPU_ONLINE count=1\n"
                "TASK1_TOPOLOGY_PROBE cpu=0 allowed=0\n"
            )
            failures, _ = VERIFY.verify_run(run_dir, "rr", 2)
        self.assertTrue(any("two online Guest CPUs" in failure for failure in failures))
        self.assertTrue(any("both workloads alive" in failure for failure in failures))

    def test_matrix_rejects_changed_immutable_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            matrix = Path(directory)
            for name, digest in (("rr-01", "0" * 64), ("fp-rr-01", "1" * 64)):
                run_dir = matrix / name
                run_dir.mkdir()
                (run_dir / "equivalence-hashes.txt").write_text(
                    f"starryos.bin={digest}\n"
                )
            failures = VERIFY.verify_matrix(matrix)
        self.assertEqual(len(failures), 1)
        self.assertIn("immutable artifact hashes differ", failures[0])


if __name__ == "__main__":
    unittest.main()
