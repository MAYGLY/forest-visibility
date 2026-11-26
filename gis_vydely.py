"""
Общий модуль: чтение таксации из GeoPackage и поиск выдела по координатам.
Работает на чистом Python (sqlite3), ничего ставить не нужно.
Слой должен быть в WGS 84 (EPSG:4326).

Модуль используется для привязки центров снимков, сетки точек и отметок животных.
"""
import math, re, sqlite3, struct
from collections import defaultdict
from pathlib import Path

HVOJNYE = {'Е', 'П', 'С', 'Л', 'К'}          # ель, пихта, сосна, лиственница, кедр
GRUPPY = ['ельник', 'сосняк', 'смешанный', 'лиственный', 'молодняк']


def _polys(blob):
    """Геометрия GPKG -> список полигонов [[кольцо, дырка, ...], ...]."""
    flags = blob[3]
    env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    w = memoryview(blob)[8 + env:]
    pos = [0]
    out = []

    def geom():
        bo = '<' if w[pos[0]] == 1 else '>'
        pos[0] += 1
        t, = struct.unpack_from(bo + 'I', w, pos[0]); pos[0] += 4
        if t >= 1000:
            raise ValueError('Экспортируйте геометрию как двумерные полигоны XY.')
        if t in (4, 5, 6, 7):                    # multi / collection
            n, = struct.unpack_from(bo + 'I', w, pos[0]); pos[0] += 4
            for _ in range(n): geom()
        elif t == 3:                             # polygon
            nr, = struct.unpack_from(bo + 'I', w, pos[0]); pos[0] += 4
            rings = []
            for _ in range(nr):
                n, = struct.unpack_from(bo + 'I', w, pos[0]); pos[0] += 4
                xy = struct.unpack_from(bo + '%dd' % (2 * n), w, pos[0]); pos[0] += 16 * n
                rings.append(list(zip(xy[0::2], xy[1::2])))
            out.append(rings)
        else:
            raise ValueError('Неподдерживаемая геометрия: тип %d' % t)
    geom()
    return out


def _in_ring(x, y, r):
    ins = False; j = len(r) - 1
    for i in range(len(r)):
        xi, yi = r[i]; xj, yj = r[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ins = not ins
        j = i
    return ins


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


class Taksaciya:
    def __init__(self, path, sloj=None):
        connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
        try:
            self._load(connection, sloj)
        finally:
            connection.close()

    def _load(self, c, sloj):
        c.row_factory = sqlite3.Row
        layers = {r[0]: r[1:] for r in c.execute(
            "SELECT table_name, column_name, srs_id FROM gpkg_geometry_columns "
            "WHERE upper(geometry_type_name) IN ('POLYGON', 'MULTIPOLYGON', 'GEOMETRY')"
        )}
        if sloj is None and len(layers) == 1:
            sloj = next(iter(layers))
        if sloj not in layers:
            raise ValueError('Укажите полигональный слой через --layer. Доступны: ' + ', '.join(layers))
        gcol, srs = layers[sloj]
        if srs != 4326:
            raise ValueError('Для сопоставления с GPS экспортируйте слой в EPSG:4326.')
        self.v = []
        self.idx = defaultdict(list)
        quoted = '"' + sloj.replace('"', '""') + '"'
        for r in c.execute(f'select * from {quoted}'):
            d = dict(r)
            blob = d.pop(gcol)
            if blob is None or blob[3] & 16:
                continue
            P = [poly for poly in _polys(blob) if poly and poly[0]]
            if not P:
                continue
            xs = [p[0] for pl in P for p in pl[0]]; ys = [p[1] for pl in P for p in pl[0]]
            bb = (min(xs), min(ys), max(xs), max(ys))
            k = len(self.v); self.v.append((bb, P, d))
            for i in range(int(bb[0] * 100), int(bb[2] * 100) + 1):       # ячейки ~0.01°
                for j in range(int(bb[1] * 100), int(bb[3] * 100) + 1):
                    self.idx[(i, j)].append(k)

    def najti(self, lat, lon):
        """Атрибуты выдела в точке или None (вне лесного фонда)."""
        for k in self.idx.get((int(lon * 100), int(lat * 100)), []):
            bb, P, d = self.v[k]
            if bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3]:
                for pl in P:
                    if _in_ring(lon, lat, pl[0]) and not any(_in_ring(lon, lat, h) for h in pl[1:]):
                        return d
        return None


def opisanie(d):
    """Краткое описание выдела: состав, полнота, возраст, высота, группа."""
    if d is None:
        return {'лесничество': '', 'кв': '', 'выд': '', 'состав': '', 'полнота': '', 'возраст': '',
                'высота_древостоя_м': '', 'группа': 'не лесфонд'}
    sost, hv, tot = [], 0, 0
    for i in range(1, 11):
        if str(d.get(f'ard{i}')) != '1': continue
        k, m = d.get(f'kf{i}'), d.get(f'mr{i}')
        try: k = int(k)
        except (TypeError, ValueError): continue
        if not m or k == 0: continue
        sost.append(f'{k}{m.capitalize()}'); tot += k
        if m in HVOJNYE: hv += k
    voz, pol = _f(d.get('amz1')), _f(d.get('skal1'))
    if tot == 0: gr = 'нелесное'
    elif voz is not None and voz < 30: gr = 'молодняк'
    elif hv >= 8: gr = 'ельник' if d.get('mr1') in ('Е', 'П') else 'сосняк'
    elif hv <= 2: gr = 'лиственный'
    else: gr = 'смешанный'
    tx = d.get('taxationun') or d.get('taxationunit')   # имена полей в разных выгрузках разные
    return {'лесничество': d.get('forestry_n') or d.get('forestry_name') or '', 'кв': d.get('kv') or '',
            'выд': int(float(tx)) if _f(tx) is not None else '', 'состав': ''.join(sost),
            'полнота': pol if pol is not None else '', 'возраст': int(voz) if voz is not None else '',
            'высота_древостоя_м': d.get('h1') or '', 'группа': gr}


def sdvig(lat, lon, vostok_m, sever_m):
    """Сместить точку на заданное число метров к востоку и северу."""
    return (lat + sever_m / 111320.0,
            lon + vostok_m / (111320.0 * math.cos(math.radians(lat))))


def xmp(path):
    """Высота, угол камеры, азимут камеры, широта, долгота из XMP снимка DJI."""
    with open(path, 'rb') as f: s = f.read(300000).decode('latin-1', 'ignore')
    g = lambda t: _f((re.search(r'drone-dji:' + t + r'="([^"]+)"', s) or [None, None])[1])
    r = {'h': g('RelativeAltitude'), 'pitch': g('GimbalPitchDegree'), 'yaw': g('GimbalYawDegree'),
         'lat': g('GpsLatitude'), 'lon': g('GpsLongitude')}
    if r['lat'] is None: r['lat'] = g('Latitude')
    if r['lon'] is None: r['lon'] = g('Longitude')
    if r['lat'] is None or r['lon'] is None:   # Phantom 4 Pro и др.: координаты только в EXIF
        try:
            from PIL import Image
            e = Image.open(path).getexif().get_ifd(0x8825)
            dm = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            r['lat'] = dm(e[2]) * (-1 if e.get(1) == 'S' else 1)
            r['lon'] = dm(e[4]) * (-1 if e.get(3) == 'W' else 1)
        except Exception:
            pass
    return r
