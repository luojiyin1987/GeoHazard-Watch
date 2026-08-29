# GeoHazard Watch

GeoHazard Watch is an experiment in explainable geohazard monitoring built from public Earth-observation data.

The project grows one evidence layer at a time. It does **not** claim to predict landslides. The current workflow can discover Sentinel observations for an area of interest (AOI) and derive a small set of terrain features from a public 30 m DEM.

## Current pipeline

```text
region.json
   ├── catalog → Sentinel-1 / Sentinel-2 availability
   └── terrain → Copernicus DEM GLO-30
                  ├── elevation
                  ├── relief
                  ├── slope
                  └── aspect
```

The metadata path uses the public Microsoft Planetary Computer STAC API. Terrain processing uses the same STAC catalog to locate Copernicus DEM GLO-30 tiles, signs the public data assets with the Planetary Computer SDK, and reads only the raster windows intersecting the AOI.

No Earth Engine project or NASA credentials are required.

## Setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

The terrain command adds NumPy, Rasterio, and the Planetary Computer SDK to the small STAC bootstrap dependency set.

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

The command prints JSON with the normalized AOI, query period, STAC endpoint, and for each dataset:

- STAC collection ID;
- scene count;
- first acquisition time;
- last acquisition time.

This remains metadata discovery only; Sentinel imagery is not downloaded.

## Derive terrain features

```bash
geohazard-watch terrain \
  --region examples/region.json
```

The terrain command searches the `cop-dem-glo-30` STAC collection and processes each intersecting DEM tile independently. It reads only the AOI window from each Cloud Optimized GeoTIFF rather than mosaicking the full DEM into memory.

The JSON output includes:

- elevation minimum, maximum, and mean;
- AOI relief (`max elevation - min elevation`);
- mean and maximum slope in degrees;
- fraction of sampled slope pixels at or above 30 degrees;
- aspect distribution across N / NE / E / SE / S / SW / W / NW;
- fraction of slope pixels treated as flat (`< 0.5°`);
- DEM collection, asset key, and tile count.

Slope is calculated with finite differences. Copernicus DEM GLO-30 is stored in geographic coordinates, so horizontal pixel spacing is converted to approximate metres at the latitude of each AOI part before calculating gradients. This is appropriate for the regional screening workflow here, but it is not a substitute for a carefully chosen projected CRS in precision geomorphology.

## Test the terrain math offline

The core terrain calculations have deterministic synthetic-array tests and do not require STAC access:

```bash
python -m unittest discover -s tests
```

## Why build in layers?

The project follows this progression:

```text
public metadata access
        ↓
terrain features
        ↓
rainfall / soil-moisture features
        ↓
Sentinel change signals
        ↓
explainable evidence fusion
        ↓
validation against historical events
```

Each layer should be usable and testable before the next one is added. This keeps data-access failures, numerical-processing failures, and scientific-model assumptions separate.

## Data source

The default STAC endpoint is:

```text
https://planetarycomputer.microsoft.com/api/stac/v1
```

It can be overridden with `--endpoint` so compatible providers can be tested without changing AOI files. A terrain endpoint must provide the `cop-dem-glo-30` collection with a `data` COG asset.
