"""Command-line interface for GeoHazard Watch."""

from __future__ import annotations

import argparse
import json

from pystac_client.exceptions import APIError

from .aoi import Region
from .catalog import DEFAULT_STAC_ENDPOINT, query_catalog
from .rainfall import query_rainfall
from .terrain import query_terrain
from .validation import (
    DEFAULT_AOI_HALF_SIZE_KM,
    DEFAULT_CONTROL_OFFSET_DAYS,
    DEFAULT_EVENTS_PATH,
    validate_event,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geohazard-watch")
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog_parser = subparsers.add_parser(
        "catalog", help="query public Sentinel metadata for an AOI"
    )
    catalog_parser.add_argument("--region", required=True, help="path to region JSON")
    catalog_parser.add_argument("--start", required=True, help="start date (YYYY-MM-DD)")
    catalog_parser.add_argument("--end", required=True, help="end date (YYYY-MM-DD)")
    catalog_parser.add_argument(
        "--endpoint",
        default=DEFAULT_STAC_ENDPOINT,
        help="STAC API endpoint",
    )

    terrain_parser = subparsers.add_parser(
        "terrain", help="derive Copernicus DEM terrain features for an AOI"
    )
    terrain_parser.add_argument("--region", required=True, help="path to region JSON")
    terrain_parser.add_argument(
        "--endpoint",
        default=DEFAULT_STAC_ENDPOINT,
        help="STAC API endpoint containing cop-dem-glo-30",
    )

    rainfall_parser = subparsers.add_parser(
        "rainfall", help="derive GPM IMERG rainfall features ending on a date"
    )
    rainfall_parser.add_argument("--region", required=True, help="path to region JSON")
    rainfall_parser.add_argument(
        "--date",
        required=True,
        help="last UTC day included in 1/3/7-day rainfall windows (YYYY-MM-DD)",
    )

    validate_parser = subparsers.add_parser(
        "validate", help="compare evidence around a curated historical landslide event"
    )
    validate_parser.add_argument("--event", required=True, help="event id from validation manifest")
    validate_parser.add_argument(
        "--manifest",
        default=str(DEFAULT_EVENTS_PATH),
        help="path to curated historical event manifest",
    )
    validate_parser.add_argument(
        "--aoi-half-size-km",
        type=float,
        default=DEFAULT_AOI_HALF_SIZE_KM,
        help="half-width of event-centered validation AOI in kilometres",
    )
    validate_parser.add_argument(
        "--control-offset-days",
        type=int,
        default=DEFAULT_CONTROL_OFFSET_DAYS,
        help="days before the event used for the same-location temporal control",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "catalog":
            region = Region.from_file(args.region)
            result = query_catalog(
                region=region,
                start=args.start,
                end=args.end,
                endpoint=args.endpoint,
            )
        elif args.command == "terrain":
            region = Region.from_file(args.region)
            result = query_terrain(region=region, endpoint=args.endpoint)
        elif args.command == "rainfall":
            region = Region.from_file(args.region)
            result = query_rainfall(region=region, target_date=args.date)
        elif args.command == "validate":
            result = validate_event(
                event_id=args.event,
                manifest_path=args.manifest,
                half_size_km=args.aoi_half_size_km,
                control_offset_days=args.control_offset_days,
            )
        else:
            parser.error(f"unsupported command: {args.command}")
            return 2
    except (APIError, OSError, ValueError) as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
