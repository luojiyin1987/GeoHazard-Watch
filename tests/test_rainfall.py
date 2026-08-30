from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import ssl
import unittest
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError

from geohazard_watch.aoi import Region
from geohazard_watch.rainfall import (
    DailyRainfall,
    HTTP_MAX_ATTEMPTS,
    _bbox_parts,
    _chunk_ranges,
    _daily_rainfall,
    _request_json,
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


class RainfallRequestRetryTests(unittest.TestCase):
    @staticmethod
    def _response(payload: bytes = b'{"ok": true}') -> MagicMock:
        response = MagicMock()
        response.headers.get.return_value = None
        response.read.return_value = payload
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    @patch("geohazard_watch.rainfall.sleep")
    @patch("geohazard_watch.rainfall.urlopen")
    def test_transient_url_error_is_retried(self, urlopen, sleep) -> None:
        urlopen.side_effect = [URLError("temporary TLS EOF"), self._response()]

        result = _request_json("https://example.invalid/ImageServer", {"f": "json"})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.5)

    @patch("geohazard_watch.rainfall.sleep")
    @patch("geohazard_watch.rainfall.urlopen")
    def test_retryable_http_status_uses_exponential_backoff(self, urlopen, sleep) -> None:
        errors = [
            HTTPError("https://example.invalid", 503, "unavailable", {}, BytesIO()),
            HTTPError("https://example.invalid", 429, "rate limited", {}, BytesIO()),
            HTTPError("https://example.invalid", 500, "server error", {}, BytesIO()),
        ]
        urlopen.side_effect = [*errors, self._response()]

        result = _request_json("https://example.invalid/ImageServer", {"f": "json"})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(0.5), call(1.0), call(2.0)])

    @patch("geohazard_watch.rainfall.sleep")
    @patch("geohazard_watch.rainfall.urlopen")
    def test_non_retryable_http_status_fails_immediately(self, urlopen, sleep) -> None:
        urlopen.side_effect = HTTPError(
            "https://example.invalid", 404, "not found", {}, BytesIO()
        )

        with self.assertRaisesRegex(OSError, "endpoint=ImageServer"):
            _request_json("https://example.invalid/ImageServer", {"f": "json"})

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    @patch("geohazard_watch.rainfall.sleep")
    @patch("geohazard_watch.rainfall.urlopen")
    def test_certificate_verification_failure_is_not_retried(self, urlopen, sleep) -> None:
        certificate_error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        urlopen.side_effect = URLError(certificate_error)

        with self.assertRaisesRegex(OSError, "certificate verify failed"):
            _request_json("https://example.invalid/ImageServer", {"f": "json"})

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    @patch("geohazard_watch.rainfall.sleep")
    @patch("geohazard_watch.rainfall.urlopen")
    def test_exhausted_retries_report_attempts_and_time_context(self, urlopen, sleep) -> None:
        urlopen.side_effect = URLError("temporary TLS EOF")

        with self.assertRaisesRegex(
            OSError,
            rf"after {HTTP_MAX_ATTEMPTS} attempts .*endpoint=computeStatisticsHistograms, time=1,2",
        ):
            _request_json(
                "https://example.invalid/computeStatisticsHistograms",
                {"time": "1,2", "f": "json"},
            )

        self.assertEqual(urlopen.call_count, HTTP_MAX_ATTEMPTS)
        self.assertEqual(sleep.call_args_list, [call(0.5), call(1.0), call(2.0)])


if __name__ == "__main__":
    unittest.main()
