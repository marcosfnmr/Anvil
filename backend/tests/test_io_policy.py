from __future__ import annotations

import unittest

from services.io_policy import diagnose_reading_range


class ReadingRangeDiagnosticsTests(unittest.TestCase):
    limits = {"min_value": 0.0, "max_value": 100.0, "unit": "%"}

    def test_in_range_reading_preserves_good_quality_without_warning(self) -> None:
        result = diagnose_reading_range({"value": 50.0, "quality": "good"}, self.limits)
        self.assertEqual(result["quality"], "good")
        self.assertNotIn("range_diagnostic", result)

    def test_values_below_and_above_limits_are_annotated(self) -> None:
        for value in (-0.1, 100.1):
            with self.subTest(value=value):
                result = diagnose_reading_range({"value": value, "quality": "good"}, self.limits)
                self.assertEqual(result["quality"], "out_of_range")
                self.assertEqual(result["protocol_quality"], "good")
                self.assertEqual(result["range_diagnostic"]["minimum"], 0.0)
                self.assertEqual(result["range_diagnostic"]["maximum"], 100.0)
                self.assertIn("%", result["range_diagnostic"]["message"])

    def test_bad_protocol_quality_is_not_replaced_by_range_diagnostic(self) -> None:
        result = diagnose_reading_range({"value": 150.0, "quality": "stale"}, self.limits)
        self.assertEqual(result["quality"], "stale")
        self.assertNotIn("range_diagnostic", result)

    def test_undeclared_limits_and_boolean_values_are_not_range_checked(self) -> None:
        for value, limits in ((150.0, None), (True, self.limits), (float("nan"), self.limits)):
            with self.subTest(value=value):
                result = diagnose_reading_range({"value": value}, limits)
                self.assertEqual(result["quality"], "good")
                self.assertNotIn("range_diagnostic", result)


if __name__ == "__main__":
    unittest.main()
