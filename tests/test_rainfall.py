from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from geohazard_watch.aoi import Region
from geohazard_watch.rainfall import (
    DailyRainfall,
    _bbox_parts,
    _chunk_ranges,
    _daily_rainfall,
    query_rainfall,
    summarize_rainfall_days,
)


class RainfallSummaryTests(unittest.TestCase):
    def test_accumulation_windows_sum_aoi_daily_means(self) -> None:
        start = date(2026, 8, 1)
        days = [
            DailyRainfall(
                day=start + timedelta(days=index),
                mean_mm=float(index + 1),
                min_sample_count=4,
            )
            for index in range(7)
        ]

        result = summarize_rainfall_days(days)

        self.assertEqual(
            result["accumulation_mean_mm"],
            {"1d": 7.0, "3d": 18.0, "7d": 28.0},
        )
        self.assertEqual(result["daily"][-1]["min_sample_count"], 4)

    def test_non_consecutive_dates_are_rejected(self) -> None:
        start = date(2026, 8, 1)
        days = [
            DailyRainfall(
                day=start + timedelta(days=index + (1 if index == 6 else 0)),
                mean_mm=1.0,
                min_sample_count=1,
            )
            for index in range(7)
        ]

        with self.assertRaisesRegex(ValueError, "consecutive"):
            summarize_rainfall_days(days)

    def test_antimeridian_bbox_is_split(self) -> None:
        self.assertEqual(
            _bbox_parts((170.0, -10.0, -170.0, 10.0)),
            [
                (170.0, -10.0, 180.0, 10.0),
                (-180.0, -10.0, -170.0, 10.0),
            ],
        )

    def test_day_is_split_into_four_six_hour_chunks(self) -> None:
        ranges = _chunk_ranges(date(2026, 8, 10))

        self.assertEqual(len(ranges), 4)
        self.assertEqual(ranges[0][0].hour, 0)
        self.assertEqual(ranges[1][0].hour, 6)
        self.assertEqual(ranges[2][0].hour, 12)
        self.assertEqual(ranges[3][0].hour, 18)
        self.assertEqual(ranges[-1][1].date(), date(2026, 8, 10))

    @patch("geohazard_watch.rainfall._chunk_mean_rate")
    def test_daily_rainfall_converts_half_hourly_rate_sum_to_depth(
        self, chunk_mean_rate
    ) -> None:
        chunk_mean_rate.side_effect = [
            (4.0, 100),
            (6.0, 98),
            (8.0, 99),
            (10.0, 100),
        ]

        result = _daily_rainfall(
            date(2026, 8, 10),
            (102.0, 29.5, 103.5, 30.5),
        )

        self.assertEqual(result.mean_mm, 14.0)
        self.assertEqual(result.min_sample_count, 98)
        self.assertEqual(chunk_mean_rate.call_count, 4)

    @patch("geohazard_watch.rainfall._daily_rainfall")
    @patch("geohazard_watch.rainfall._service_time_extent")
    def test_incomplete_first_day_is_rejected_before_rainfall_requests(
        self, service_time_extent, daily_rainfall
    ) -> None:
        service_time_extent.return_value = (
            datetime(2026, 8, 4, 0, 30, tzinfo=timezone.utc),
            datetime(2026, 8, 10, 23, 30, tzinfo=timezone.utc),
        )
        region = Region("test", (102.0, 29.5, 103.5, 30.5))

        with self.assertRaisesRegex(ValueError, "complete UTC day"):
            query_rainfall(region, "2026-08-10")

        daily_rainfall.assert_not_called()

    @patch("geohazard_watch.rainfall._daily_rainfall")
    @patch("geohazard_watch.rainfall._service_time_extent")
    def test_incomplete_target_day_is_rejected_before_rainfall_requests(
        self, service_time_extent, daily_rainfall
    ) -> None:
        service_time_extent.return_value = (
            datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        )
        region = Region("test", (102.0, 29.5, 103.5, 30.5))

        with self.assertRaisesRegex(ValueError, "incomplete UTC day"):
            query_rainfall(region, "2026-08-10")

        daily_rainfall.assert_not_called()


if __name__ == "__main__":
    unittest.main()
