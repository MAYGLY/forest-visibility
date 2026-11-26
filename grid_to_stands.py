"""Диагностика геопривязки сетки 7×7 широкоугольного снимка DJI."""

import argparse
import json
import math
import re
import csv
from pathlib import Path

from PIL import Image

from gis_vydely import Taksaciya, _in_ring, opisanie, sdvig


class Terrain:
    def __init__(self, path):
        with Image.open(path) as image:
            self.image = image.copy()
            self.dx, self.dy = image.tag_v2[33550][:2]
            self.lon0, self.lat0 = image.tag_v2[33922][3:5]
            keys = image.tag_v2[34735]
            entries = dict(
                (keys[i], keys[i + 3])
                for i in range(4, len(keys), 4)
                if keys[i + 1] == 0 and keys[i + 2] == 1
            )
            if entries.get(2048) != 4326 or entries.get(1025) != 2:
                raise ValueError("Ожидается FABDEM в EPSG:4326 с PixelIsPoint.")

    def height(self, lat, lon):
        x = (lon - self.lon0) / self.dx
        y = (self.lat0 - lat) / self.dy
        c, r = math.floor(x), math.floor(y)
        if not (0 <= c < self.image.width - 1 and 0 <= r < self.image.height - 1):
            raise ValueError("Луч вышел за пределы мозаичного листа FABDEM.")
        fx, fy = x - c, y - r
        heights = [self.image.getpixel((c + dc, r + dr)) for dr in (0, 1) for dc in (0, 1)]
        if any(not math.isfinite(h) or h < -1000 for h in heights):
            raise ValueError("Луч пересёк участок FABDEM без данных.")
        a, b, c, d = heights
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def dji_metadata(path):
    with path.open("rb") as stream:
        xmp = stream.read(300_000).decode("latin-1")
    fields = dict(re.findall(r'drone-dji:(\w+)="([^"]+)"', xmp))
    if fields.get("ImageSource") != "WideCamera":
        raise ValueError("Нужен кадр широкоугольной камеры DJI.")
    if abs(float(fields["GimbalRollDegree"])) > 0.01:
        raise ValueError("Эта проверка рассчитана на кадр с нулевым креном подвеса.")
    return fields


def intersect(terrain, lat, lon, altitude, east, north, up):
    if up >= 0:
        raise ValueError("Луч направлен выше горизонта.")
    if altitude <= terrain.height(lat, lon):
        raise ValueError("Высота камеры не выше поверхности в точке съёмки.")
    for k in range(1, 12_001):
        distance = k * 0.25
        ground_lat, ground_lon = sdvig(lat, lon, distance * east, distance * north)
        height = terrain.height(ground_lat, ground_lon)
        if altitude + distance * up <= height:
            return ground_lat, ground_lon, height, distance
    raise ValueError("Пересечение с рельефом не найдено в пределах 3 км.")


def ring_distance(lat, lon, ring):
    east_scale = 111320 * math.cos(math.radians(lat))
    offsets = [((x - lon) * east_scale, (y - lat) * 111320) for x, y in ring]
    closest = math.inf
    for (ax, ay), (bx, by) in zip(offsets, offsets[1:] + offsets[:1]):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        fraction = max(0, min(1, -(ax * dx + ay * dy) / length)) if length else 0
        closest = min(closest, math.hypot(ax + fraction * dx, ay + fraction * dy))
    return closest


def stand_at(layer, lat, lon):
    col, row = int(lon * 100), int(lat * 100)
    nearby = {
        key for i in range(col - 1, col + 2) for j in range(row - 1, row + 2)
        for key in layer.idx.get((i, j), [])
    }
    hits = []
    nearest = math.inf
    for key in nearby:
        (west, south, east, north), polygons, attrs = layer.v[key]
        if not (west - 0.001 <= lon <= east + 0.001 and south - 0.001 <= lat <= north + 0.001):
            continue
        for outer, *holes in polygons:
            if _in_ring(lon, lat, outer) and not any(_in_ring(lon, lat, hole) for hole in holes):
                hits.append(attrs)
            nearest = min(nearest, *(ring_distance(lat, lon, ring) for ring in [outer, *holes]))
    return hits, nearest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--focal-px", required=True, type=float, help="Рабочее фокусное в пикселях")
    parser.add_argument("--height-offset", required=True, type=float, help="Диагностическая поправка высоты, м")
    parser.add_argument("--dem", type=Path, required=True)
    parser.add_argument("--taksaciya", type=Path, required=True, help="Карта выделов GeoPackage в EPSG:4326")
    parser.add_argument("--layer", help="Название полигонального слоя")
    parser.add_argument("--marks", type=Path, help="Файл tochki.csv для включения классов точек")
    parser.add_argument("--grid", type=int, default=7)
    parser.add_argument("--oblast", choices=["vse", "T"], default="vse")
    args = parser.parse_args()
    if args.grid < 1:
        parser.error("Размер сетки должен быть положительным.")
    labels = None
    if args.marks:
        with args.marks.open(encoding="utf-8-sig", newline="") as stream:
            matches = [r for r in csv.DictReader(stream, delimiter=";")
                       if (args.marks.parent / r["файл"].replace(chr(92), "/")).resolve() == args.image.resolve()]
        if len(matches) != 1:
            parser.error("В таблице нужна ровно одна строка выбранного снимка.")
        labels = matches[0].get("точки", "")
        if len(labels) != args.grid ** 2 or set(labels) - set("012"):
            parser.error("Проверьте размер сетки и колонку точки в разметке.")

    if not math.isfinite(args.focal_px) or args.focal_px <= 0 or not math.isfinite(args.height_offset):
        parser.error("Требуются положительное фокусное и конечная поправка высоты.")

    tags = dji_metadata(args.image)
    with Image.open(args.image) as image:
        width, height = image.size
    lat, lon = map(float, (tags["GpsLatitude"], tags["GpsLongitude"]))
    altitude = float(tags["AbsoluteAltitude"]) + args.height_offset
    pitch, yaw = map(math.radians, map(float, (tags["GimbalPitchDegree"], tags["GimbalYawDegree"])))
    terrain = Terrain(args.dem)
    layer = Taksaciya(args.taksaciya, args.layer)
    features = []
    fw, fh = (0.503, 0.714) if args.oblast == "T" else (1.0, 1.0)
    for i in range(args.grid ** 2):
        row, col = divmod(i, args.grid)
        u = width * (1 - fw) / 2 + (col + 0.5) * width * fw / args.grid
        v = height * (1 - fh) / 2 + (row + 0.5) * height * fh / args.grid
        x, y = (u - width / 2) / args.focal_px, (v - height / 2) / args.focal_px
        norm = math.sqrt(1 + x * x + y * y)
        forward = math.cos(pitch) + y * math.sin(pitch)
        up = (math.sin(pitch) - y * math.cos(pitch)) / norm
        east = (forward * math.sin(yaw) + x * math.cos(yaw)) / norm
        north = (forward * math.cos(yaw) - x * math.sin(yaw)) / norm
        glat, glon, ground, distance = intersect(terrain, lat, lon, altitude, east, north, up)
        hits, edge = stand_at(layer, glat, glon)
        properties = {
            "point": i + 1, "row": row + 1, "column": col + 1, "u_px": u, "v_px": v,
            "ground_m": round(ground, 2), "ray_m": round(distance, 2),
            "stand_matches": len(hits),
            "boundary_m": round(edge, 2) if math.isfinite(edge) else None,
            "within_10_m": edge < 10, "within_15_m": edge < 15,
        }
        if labels is not None:
            properties["class"] = int(labels[i])
        if len(hits) == 1:
            properties.update(opisanie(hits[0]))
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [glon, glat]},
                         "properties": properties})
    result = {
        "type": "FeatureCollection",
        "diagnostic": {"image": args.image.name, "focal_px": args.focal_px,
                       "height_offset_m": args.height_offset, "dem": args.dem.name,
                       "grid": args.grid, "oblast": args.oblast},
        "features": features,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
