# GeoHazard Watch

GeoHazard Watch is an experiment in explainable geohazard monitoring built from public Earth-observation data.

The first milestone is deliberately small: given a named area of interest (AOI) and a date range, query public STAC metadata and report which Sentinel observations are available. It does **not** download imagery, calculate a hazard score, or claim to predict landslides.

## Bootstrap pipeline

PR #1 uses the public Microsoft Planetary Computer STAC API to discover:

- Sentinel-1 Level-1 GRD scenes;
- Sentinel-2 Level-2A scenes.

The metadata API can be queried anonymously, so this first pipeline does not require an Earth Engine project or NASA credentials.

## Setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Query an AOI

A region is a small JSON file containing a name and WGS84 bounding box in the order `west, south, east, north`:

```json
{
  "name": "example-mountain-region",
  "bbox": [102.0, 29.5, 103.5, 30.5]
}
```

Query Sentinel metadata for a period:

```bash
geohazard-watch catalog \
  --region examples/region.json \
  --start 2026-08-01 \
  --end 2026-08-28
```

The command prints JSON with the normalized AOI, query period, STAC endpoint, and for each dataset:

- STAC collection ID;
- scene count;
- first acquisition time;
- last acquisition time.

This is metadata discovery only. Asset download and raster processing are intentionally deferred until the catalog path has been exercised successfully.

## Why start with catalog discovery?

The project follows a simple progression:

```text
public metadata access
        ↓
terrain / rainfall / satellite features
        ↓
explainable evidence fusion
        ↓
validation against historical events
```

Each layer should be usable and testable before the next one is added. This keeps data-access failures separate from scientific-model failures.

## Data source

The default catalog endpoint is:

```text
https://planetarycomputer.microsoft.com/api/stac/v1
```

It can be overridden with `--endpoint` so later work can add or test other STAC-compatible providers without changing AOI files.
