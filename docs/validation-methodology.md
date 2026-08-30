# Validation methodology and known assumptions

GeoHazard Watch treats historical validation as an evidence-replay exercise, not as a prediction benchmark. The goal is to make the spatial, temporal, and provenance limits of each evidence layer explicit before any future hazard-scoring or sensor-fusion work is attempted.

## Validation design

For each curated historical landslide event, `geohazard-watch validate` currently performs the following steps:

1. Build an approximately 10 km × 10 km WGS84 AOI centered on the catalog point (`aoi_half_size_km = 5`).
2. Derive Copernicus DEM terrain evidence for that AOI.
3. Derive GPM IMERG Early V07 calendar-day rainfall ending on the event date for 1-, 3-, and 7-day windows.
4. Derive the same calendar-day rainfall for the same AOI `control_offset_days` earlier (28 days by default).
5. When a curated UTC event boundary is available, derive trailing 6-, 12-, 24-, and 72-hour rainfall using half-open intervals immediately before the event boundary.
6. Derive matched event-centered rainfall at the same UTC clock boundary shifted backward by the control offset.
7. Report raw event evidence, raw control evidence, and event-minus-control deltas.

The output deliberately keeps `hazard_score` as `null`. A positive rainfall delta is evidence of a wetter event period relative to the selected temporal reference; it is not a landslide probability or a forecast-skill claim.

## Calendar-day versus event-centered rainfall

The two rainfall views answer different questions and are intentionally kept side by side.

Calendar-day rainfall uses complete UTC days:

```text
1d / 3d / 7d ending on the historical event date
```

Event-centered rainfall uses a curated UTC event boundary `T`:

```text
6h  = [T-6h,  T)
12h = [T-12h, T)
24h = [T-24h, T)
72h = [T-72h, T)
```

The event boundary itself is excluded. IMERG is half-hourly, so curated event boundaries used by this method must align to a 30-minute boundary.

Matched event-centered controls preserve both AOI and UTC clock time. For a 28-day control offset:

```text
event boundary   = T
control boundary = T - 28 days
```

This makes the 6/12/24/72-hour comparison event-centered versus event-centered instead of comparing rolling-hour evidence against UTC calendar days.

## Temporal provenance

The legacy GLC records used by the initial validation set can contain an event clock time, but the catalog schema does not provide a timezone field.

For the initial Turkish records with clock times, the manifest therefore records four fields together:

- the catalog clock time;
- a curated IANA timezone label;
- an explicit `catalog_clock_assumed_local` reference;
- a frozen UTC event boundary used for reproducible replay.

For example:

```text
Artvin 2009
catalog clock: 20:00
curated timezone assumption: Europe/Istanbul
frozen UTC boundary: 2009-09-23T17:00:00Z

Rize 2010
catalog clock: 18:00
curated timezone assumption: Europe/Istanbul
frozen UTC boundary: 2010-08-26T15:00:00Z
```

These UTC boundaries are sensitivity-analysis inputs. They are not claims that the GLC supplied timezone-aware timestamps.

If a curated clock time is unavailable, event-centered rainfall and matched event-centered controls are not synthesized. The date-only event remains explicit in output.

## Spatial validity heuristic

Catalog coordinates are not assumed to represent survey-grade failure locations.

The validation output compares three support scales:

- catalog-reported location-accuracy scale, when parseable;
- AOI half-size used for terrain evidence;
- nominal north-south IMERG grid scale derived from its 0.1° spatial resolution (about 11.132 km using the current latitude-degree approximation).

The current interpretation is intentionally conservative:

- evidence support smaller than the reported location-accuracy scale is marked `contextual_only`;
- evidence whose support scale is at least as large as that reported scale may be marked `catalog_scale_supported`;
- missing or unparseable accuracy keeps the interpretation `unresolved`.

This is a scale-aware heuristic, not a probabilistic uncertainty model. A GLC label such as `10km` or `25km` should not be read as a rigorously defined confidence radius unless upstream documentation establishes that meaning.

Terrain values summarize an AOI. Even when the catalog location is precise, AOI-mean slope or relief must not be described as the exact failure-slope geometry.

## Temporal controls are references, not negatives

The earlier same-location period is a temporal reference only.

A landslide catalog is incomplete. The absence of a catalog record at the control date does not prove that no landslide occurred. Therefore:

- controls must not be labeled confirmed negatives;
- negative event-minus-control deltas do not imply code failure;
- positive deltas do not establish causation;
- control selection remains a methodological limitation to revisit as the validation set grows.

## Initial reference cases

The initial three cases are intentionally heterogeneous so they expose different validity boundaries.

| Event | Catalog accuracy | Event clock | Current interpretation role |
|---|---:|---|---|
| `glc-2342-rize-2010` | 1 km | curated from catalog clock | strongest initial baseline; terrain and rainfall support are compatible with the catalog-reported location scale |
| `glc-1189-artvin-2009` | 10 km | curated from catalog clock | terrain is regional/contextual while IMERG is on roughly the same support scale as the reported location uncertainty |
| `glc-4736-diyarbakir-2013` | 25 km | unavailable | deliberately weak/anomalous case; terrain and rainfall remain contextual and event-centered rainfall is not synthesized |

The Diyarbakir record also preserves an internal provenance inconsistency: the catalog point and location metadata refer to Diyarbakir while the retained source narrative describes the football-pitch event as occurring in Sirnak. GeoHazard Watch preserves that contradiction rather than silently relocating the event.

## Known provenance and semantics limitations

### GLC source text and reporting bias

The historical catalog is used for reproducible event identifiers and provenance, not as a complete ground-truth inventory. Event dates, locations, triggers, source reports, and reporting density may all be uncertain or biased.

### `gazeteer_distance` source unit

The legacy GLC CSV header names the field `gazeteer_distance` but does not state its unit in the header. The current internal manifest field is named `gazetteer_distance_km`; that internal name must not be treated as proof that the legacy source value was documented in kilometres. The value is retained as provenance metadata and is not used by the current spatial-validity calculation.

### IMERG spatial support

IMERG Early V07 is approximately 0.1°. The current nominal kilometre conversion uses a simple latitude-degree scale for interpretability. Actual east-west cell width varies with latitude, and an AOI aggregate is not equivalent to a point rain gauge.

### Terrain support

Copernicus DEM terrain evidence is static susceptibility context. It does not identify the actual failure surface, soil condition, drainage state, or anthropogenic modification at an event site.

### Trigger evidence

Rainfall is temporal trigger context. Even strong event-centered rainfall does not by itself prove that rainfall caused a reported landslide.

## Evidence-layer rule

A new evidence layer should not be added merely because data are available.

For each future layer, GeoHazard Watch should preserve:

```text
raw / intermediate evidence
        ↓
provenance
        ↓
spatial + temporal support
        ↓
reference-case replay
        ↓
baseline comparison
        ↓
only then: evidence fusion
```

In particular, future Sentinel/HLS/SAR work should retain intermediate raster fields and simple baselines rather than collapsing immediately to a single change or hazard score.
