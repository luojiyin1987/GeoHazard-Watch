# GeoHazard Watch

GeoHazard Watch is an experiment in explainable geohazard monitoring built from public Earth-observation data.

The project grows one evidence layer at a time. It does **not** claim to predict landslides. The current workflow can discover Sentinel observations for an area of interest (AOI), derive terrain features from a public 30 m DEM, and summarize recent GPM IMERG rainfall.

## Current pipeline

```text
region.json
   ├── catalog  → Sentinel-1 / Sentinel-2 availability
   ├── terrain  → Copernicus DEM GLO-30
   │               ├── elevation
   │               ├── relief
   │               ├── slope
   │               └── aspect
   └── rainfall → GPM IMERG Late Daily V07
                   ├── daily AOI mean / max
                   └── 1d / 3d / 7d AOI-mean accumulation
```

The metadata and terrain paths use Microsoft Planetary Computer. Rainfall uses NASA GES DISC's GPM IMERG Late Daily V07 archive published through AWS Open Data.

No Earth Engine project or NASA/PPS credentials are required by these commands.

## Setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Define an AOI

A region is a JSON file containing a name and WGS84 bounding box in the order `west, south, east, north`:

```json
{
  "name": "example-mountain-region",
  "bbox": [102.0, 29.5, 103.5, 30.5]
}
```

Bounding boxes that cross the antimeridian are supported by using a west longitude greater than the east longitude.

## Query satellite availability

```bash
geohazard-watch catalog \
  --region examples/region.json \
  --start 2026-08-01 \
  --end 2026-08-28
```

The command reports Sentinel-1 / Sentinel-2 scene counts and first / last acquisition times. It does not download Sentinel imagery.

## Derive terrain features

```bash
geohazard-watch terrain \
  --region examples/region.json
```

The terrain command searches `cop-dem-glo-30`, reads only AOI windows from each intersecting COG, and reports:

- elevation minimum, maximum, and mean;
- AOI relief;
- mean and maximum slope;
- fraction of slope pixels at or above 30 degrees;
- aspect distribution across eight sectors;
- fraction of slope pixels treated as flat.

Slope is calculated with finite differences and approximate metre pixel spacing at the AOI latitude. This is intended for regional screening, not precision geomorphology.

## Derive GPM rainfall features

```bash
geohazard-watch rainfall \
  --region examples/region.json \
  --date 2026-08-10
```

The rainfall command reads NASA GPM IMERG **Late Daily V07** data for the requested UTC day and the six preceding days. It reports each day's AOI mean and maximum precipitation plus cumulative AOI-mean precipitation for windows ending on the requested day:

- `1d`;
- `3d`;
- `7d`.

`accumulation_mean_mm` means **the sum of daily precipitation averaged over IMERG grid-cell centers inside the AOI**. It is not the sum of each day's maximum cell value.

IMERG Late Daily has a nominal 0.1° grid and is an expedited product rather than the Final research product. Recent dates may not be available yet; the command fails explicitly if any of the seven required days are missing instead of silently computing a partial window.

The AWS path is used anonymously over HTTPS. The implementation discovers the actual NetCDF object name for each date, so it does not hard-code the current `V07B` / `V07C` filename suffix.

## Run offline numerical tests

```bash
python -m unittest discover -s tests
```

The terrain and rainfall aggregation math have deterministic tests that do not require remote data access.

## Why build in layers?

```text
public metadata access
        ↓
terrain features
        ↓
rainfall features
        ↓
Sentinel change signals
        ↓
explainable evidence fusion
        ↓
validation against historical events
```

Each layer should be usable and testable before the next one is added. This keeps data-access failures, numerical-processing failures, and scientific-model assumptions separate.

## Data sources

The default STAC endpoint is:

```text
https://planetarycomputer.microsoft.com/api/stac/v1
```

Terrain requires `cop-dem-glo-30` with a `data` COG asset.

Rainfall uses:

```text
GPM_3IMERGDL.07
NASA GES DISC / AWS Open Data
s3://gesdisc-cumulus-prod-protected/GPM_L3/GPM_3IMERGDL.07/
```

The rainfall product is IMERG Late Daily V07 at approximately 0.1° spatial resolution.
