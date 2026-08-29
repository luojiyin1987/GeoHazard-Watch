"""Command-line interface for GeoHazard Watch."""

from __future__ import annotations

import argparse
import json

from pystac_client.exceptions import APIError

from .aoi import Region
from .catalog import DEFAULT_STAC_ENDPOINT, query_catalog


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "catalog":
        try:
            region = Region.from_file(args.region)
            result = query_catalog(
                region=region,
                start=args.start,
                end=args.end,
                endpoint=args.endpoint,
            )
        except (APIError, OSError, ValueError) as exc:
            parser.error(str(exc))

        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
