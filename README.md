# GeoHazard Watch

GeoHazard Watch is an experiment in explainable geohazard monitoring built from public Earth-observation data.

The project grows one evidence layer at a time. It does **not** claim to predict landslides. The current workflow can discover Sentinel observations for an area of interest (AOI), derive terrain features from a public 30 m DEM, summarize recent GPM IMERG rainfall, and replay those evidence layers around curated historical landslide events.

## Current pipeline

```text
region.json
   ├── catalog  → Sentinel-1 / Sentinel-2 availability
   ├── terrain  → Copernicus DEM GLO-30
   │               ├── elevation
   │               ├── relief
   │               ├── slope
   │               └── aspect
   └── rainfall → GPM IMERG Early V07
                   ├── AOI daily mean precipitation depth
                   └── 1d / 3d / 7d AOI-mean accumulation

validation/events.json
   └── validate → event-centered terrain + rainfall
                    ├── historical event date
                    ├── earlier same-location temporal control
                    └── 1d / 3d / 7d rainfall deltas
```

The metadata and terrain paths use Microsoft Planetary Computer. Rainfall uses NASA Earthdata GIS's public GPM IMERG Early V07 ImageServer. Historical validation starts from curated NASA Global Landslide Catalog (GLC) identifiers and keeps raw evidence visible instead of turning it into a hazard score.

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

The rainfall command uses NASA's public `GPM_3IMERGHHE` ImageServer, which exposes IMERG **Early Run V07** precipitation at 0.1° every half hour. For the requested UTC date and the six preceding days it reports AOI daily-mean precipitation depth and cumulative AOI-mean precipitation for windows ending on the requested day:

- `1d`;
- `3d`;
- `7d`.

`accumulation_mean_mm` is the sum of the AOI's daily-mean precipitation depth. It is not a landslide probability and it is not the sum of daily maximum grid cells.

The ImageServer publishes half-hourly precipitation as a rate in mm/hour. To avoid exceeding the service's 20-image mosaic limit, each day is processed in four six-hour blocks. Each block mosaics 12 half-hour slices with `MT_SUM`; the result is multiplied by 0.5 hour and the four blocks are accumulated into a daily precipitation depth.

The command reads the service's advertised time extent before doing the seven-day calculation. If the requested date is newer than the ImageServer has published, it fails with the latest available service date instead of silently returning a partial window. The operational ImageServer can lag the underlying Early Run product, so this service freshness is treated as data provenance rather than assumed from IMERG's nominal latency.

For antimeridian-crossing AOIs, the geometry is split into two envelopes and recombined by valid-pixel count.

## Replay evidence around a historical event

The initial validation manifest contains three rainfall/downpour-triggered events identified by stable NASA GLC event IDs. Run one event with:

```bash
geohazard-watch validate \
  --event glc-2342-rize-2010
```

The default design is intentionally simple and inspectable:

- build an approximately 10 km × 10 km AOI centered on the catalog point;
- derive the existing terrain evidence once for that AOI;
- derive 1/3/7-day rainfall ending on the historical event date;
- derive the same rainfall evidence for the same AOI 28 days earlier;
- report the raw evidence plus event-minus-control rainfall accumulation differences.

The earlier date is a **temporal reference**, not a labeled negative example. A catalog containing no report at that place and date does not prove that no landslide occurred. The validation command therefore leaves `hazard_score` as `null` and makes no probability or forecast-skill claim.

The initial `validation/events.json` records preserve legacy GLC event IDs for reproducibility. Event metadata and original source material should be re-verified before using these cases in scientific publication or a formal benchmark.

## Run offline numerical tests

```bash
python -m unittest discover -s tests
```

Terrain, rainfall aggregation, and validation assembly have deterministic tests that do not require remote data access.

## Why build in layers?

```text
public metadata access
        ↓
terrain features
        ↓
rainfall features
        ↓
historical event validation
        ↓
Sentinel change signals
        ↓
measure incremental evidence value
        ↓
explainable evidence fusion
```

Each layer should be usable and testable before the next one is added. A new evidence layer should eventually justify itself by improving discrimination on historical cases rather than merely increasing pipeline complexity.

## Data sources

The default STAC endpoint is:

```text
https://planetarycomputer.microsoft.com/api/stac/v1
```

Terrain requires `cop-dem-glo-30` with a `data` COG asset.

Rainfall uses the public NASA Earthdata GIS image service:

```text
https://gis.earthdata.nasa.gov/image/rest/services/GESDISC/GPM_3IMERGHHE/ImageServer
```

`GPM_3IMERGHHE` is IMERG Early Run V07 at 0.1° / 30-minute resolution. The service exposes the `precipitation` variable and its current published time extent through the ArcGIS REST API.

Historical event identifiers come from NASA's Global Landslide Catalog / Cooperative Open Online Landslide Repository lineage:

```text
https://data.nasa.gov/dataset/global-landslide-catalog-export
https://gis.earthdata.nasa.gov/gis05/rest/services/Landslides/COOLR_Reports_Points/MapServer/0
```

The legacy GLC export is useful for stable event identifiers, while COOLR is the current repository lineage. The project treats catalog incompleteness, event-date uncertainty, point-location uncertainty, and reporting bias as validation limitations rather than hidden assumptions.
