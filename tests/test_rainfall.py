from __future__ import annotations

from datetime import date, timedelta
import unittest

import numpy as np

from geohazard_watch.rainfall import DailyRainfall, _longitude_mask, summarize_rainfall_days


class RainfallSummaryTests(unittest.TestCase):
    def test_accumulation_windows_sum_aoi_daily_means(self) -> None:
        start = date(2026, 8, 1)
        days = [
            DailyRainfall(
                day=start + timedelta(days=index),
                mean_mm=float(index + 1),
                max_mm=float((index + 1) * 10),
                sample_count=4,
            )
            for index in range(7)
        ]

        result = summarize_rainfall_days(days)

        self.assertEqual(
            result["accumulation_mean_mm"],
            {"1d": 7.0, "3d": 18.0, "7d": 28.0},
        )
        self.assertEqual(result["daily"][-1]["max_mm"], 70.0)

    def test_non_consecutive_dates_are_rejected(self) -> None:
        start = date(2026, 8, 1)
        days = [
            DailyRainfall(
                day=start + timedelta(days=index + (1 if index == 6 else 0)),
                mean_mm=1.0,
                max_mm=1.0,
                sample_count=1,
            )
            for index in range(7)
        ]

        with self.assertRaisesRegex(ValueError, "consecutive"):
            summarize_rainfall_days(days)

    def test_antimeridian_longitude_mask(self) -> None:
        lon = np.array([-179.0, -170.0, 0.0, 170.0, 179.0])
        mask = _longitude_mask(lon, (170.0, -10.0, -170.0, 10.0))

        np.testing.assert_array_equal(
            mask,
            np.array([True, True, False, True, True]),
        )


if __name__ == "__main__":
    unittest.main()
