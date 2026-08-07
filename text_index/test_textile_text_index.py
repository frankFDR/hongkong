from __future__ import annotations

import unittest
from datetime import datetime

import textile_text_index as index


FEATURES = ["mainland_export", "mainland_import"]


def score(confidence: float, value: int) -> dict:
    return {
        feature: {"confidence": confidence, "scores": [value] * 24}
        for feature in FEATURES
    }


class TextileIndexTest(unittest.TestCase):
    def test_previous_month_score_is_aligned_to_target_month(self) -> None:
        rows = [
            {
                "timestamp": datetime(2024, 12, 10),
                "textile_relevance": 0.9,
                "textile_score": score(0.9, 1),
            },
            {
                "timestamp": datetime(2025, 1, 5),
                "textile_relevance": 0.7,
                "textile_score": score(0.5, -1),
            },
        ]
        values = index.calculate_indices(rows, FEATURES, ["2025-01"], 0.8, 0, 23)
        # Only the December article passes the 0.8 score-confidence threshold.
        # January's channel coefficient is 1 positive channel / 1 positive prefilter.
        self.assertAlmostEqual(values["2025-01"]["mainland_export"], 0.9)

    def test_missing_score_still_contributes_to_prefilter_denominator(self) -> None:
        rows = [
            {
                "timestamp": datetime(2025, 1, 1),
                "textile_relevance": 0.5,
                "textile_score": score(0.9, 1),
            },
            {
                "timestamp": datetime(2025, 1, 2),
                "textile_relevance": 0.5,
                "textile_score": None,
            },
        ]
        values = index.calculate_indices(rows, FEATURES, ["2025-01"], 0.8, 0, 23)
        self.assertAlmostEqual(values["2025-01"]["mainland_export"], 0.45)

    def test_month_formats(self) -> None:
        self.assertEqual(index.normalize_month("2025M2"), "2025-02")
        self.assertEqual(index.shift_month("2025-01", -1), "2024-12")


if __name__ == "__main__":
    unittest.main()
