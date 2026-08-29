"""Deterministic tests for terrain feature calculations."""

import unittest

import numpy as np

from geohazard_watch.terrain import summarize_dem_array


class TerrainSummaryTests(unittest.TestCase):
    def test_flat_surface(self) -> None:
        elevation = np.full((5, 5), 100.0)

        result = summarize_dem_array(elevation, dx_m=30.0, dy_m=30.0)

        self.assertEqual(result["sample_count"], 25)
        self.assertEqual(result["elevation_m"], {"min": 100.0, "max": 100.0, "mean": 100.0})
        self.assertEqual(result["relief_m"], 0.0)
        self.assertEqual(result["slope_deg"]["mean"], 0.0)
        self.assertEqual(result["slope_deg"]["max"], 0.0)
        self.assertEqual(result["slope_deg"]["fraction_ge_30deg"], 0.0)
        self.assertEqual(result["flat_fraction"], 1.0)
        self.assertTrue(all(value == 0.0 for value in result["aspect_pct"].values()))

    def test_east_rising_plane_faces_west_downslope(self) -> None:
        row = np.arange(5, dtype=np.float64) * 30.0
        elevation = np.tile(row, (5, 1))

        result = summarize_dem_array(elevation, dx_m=30.0, dy_m=30.0)

        self.assertEqual(result["relief_m"], 120.0)
        self.assertAlmostEqual(result["slope_deg"]["mean"], 45.0, places=3)
        self.assertAlmostEqual(result["slope_deg"]["max"], 45.0, places=3)
        self.assertEqual(result["slope_deg"]["fraction_ge_30deg"], 1.0)
        self.assertEqual(result["flat_fraction"], 0.0)
        self.assertEqual(result["aspect_pct"]["W"], 100.0)

    def test_rejects_non_2d_arrays(self) -> None:
        with self.assertRaisesRegex(ValueError, "two-dimensional"):
            summarize_dem_array(np.array([1.0, 2.0, 3.0]), dx_m=30.0, dy_m=30.0)


if __name__ == "__main__":
    unittest.main()
