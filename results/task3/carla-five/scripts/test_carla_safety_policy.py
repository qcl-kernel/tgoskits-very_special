#!/usr/bin/env python3
"""Regression checks for the five-scene CARLA safety policy."""

from __future__ import annotations

import unittest

import carla_five_scenarios as scenarios


class SafetyPolicyTests(unittest.TestCase):
    def test_person_distance_uses_box_height(self) -> None:
        detection = (0, 0.9, 600.0, 300.0, 640.0, 345.0)
        self.assertAlmostEqual(scenarios.estimate_person_distance(detection), 24.0)

    def test_person_slows_before_the_hard_stop(self) -> None:
        self.assertEqual(scenarios.person_target_speed(30.0), 10.0)
        self.assertEqual(scenarios.person_target_speed(27.0), 5.0)
        self.assertEqual(scenarios.person_target_speed(24.0), 0.0)

    def test_target_specific_clearances_and_recovery(self) -> None:
        self.assertEqual(scenarios.VEHICLE_STOP_GAP_M, 8.0)
        self.assertEqual(scenarios.PERSON_MIN_CLEARANCE_M, 2.0)
        self.assertGreaterEqual(scenarios.POST_RESET_DRIVE_S, 6.0)


if __name__ == "__main__":
    unittest.main()
