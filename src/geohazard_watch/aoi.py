"""Area-of-interest loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Region:
    """A named WGS84 bounding box in west, south, east, north order."""

    name: str
    bbox: tuple[float, float, float, float]

    @classmethod
    def from_file(cls, path: str | Path) -> "Region":
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid region JSON in {source}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError("Region file must contain a JSON object")

        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Region name must be a non-empty string")

        raw_bbox = payload.get("bbox")
        if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
            raise ValueError("Region bbox must contain [west, south, east, north]")

        values: list[float] = []
        for value in raw_bbox:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Region bbox values must be finite numbers")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("Region bbox values must be finite numbers")
            values.append(number)

        west, south, east, north = values
        if not -180 <= west < east <= 180:
            raise ValueError("Region bbox must satisfy -180 <= west < east <= 180")
        if not -90 <= south < north <= 90:
            raise ValueError("Region bbox must satisfy -90 <= south < north <= 90")

        return cls(name=name.strip(), bbox=(west, south, east, north))

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "bbox": list(self.bbox)}
