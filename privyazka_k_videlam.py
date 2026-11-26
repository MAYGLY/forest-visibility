"""
Привязка размеченных снимков (tochki.csv) к выделам лесотаксации и сводка
видимости земли по типам леса.

  1. Читает все tochki.csv в папке (с подпапками).
  2. Для каждого снимка находит, куда смотрела камера: при съёмке под углом
     центр кадра смещён от дрона на h / tan(|угол|) по азимуту камеры
     (азимут берётся из самого снимка или соседнего кадра того же номера).
  3. Определяет выдел в центре кадра и его таксацию.
  4. Пишет две таблицы:
       vidimost_po_snimkam.csv — снимок + видимость + выдел;
       vidimost_svodka.csv     — средняя видимость по группе леса, высоте и углу.

Запуск:
  python privyazka_k_videlam.py --taksaciya "stands.gpkg" --papka "flights"
Ничего ставить не нужно; gis_vydely.py должен лежать рядом.
"""
import argparse, csv, glob, math, os, re, statistics
from gis_vydely import Taksaciya, opisanie, sdvig, xmp


def yaw_dlya(folder, fn):
    m = re.search(r'_(\d{4})_', fn)
    cand = [os.path.join(folder, fn)] + (sorted(glob.glob(os.path.join(folder, f'*_{m.group(1)}_*.JPG'))) if m else [])
    for f in cand:
        if os.path.exists(f):
            y = xmp(f)['yaw']
            if y is not None: return y
    return None


def chislo(v):
    try: return float(str(v).replace(',', '.'))
    except ValueError: return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--taksaciya', required=True)
    ap.add_argument('--papka', required=True)
    ap.add_argument('--layer', help='Название слоя при нескольких полигональных слоях')
    a = ap.parse_args()
    T = Taksaciya(a.taksaciya, a.layer)
    rows = []
    for root, _, files in os.walk(a.papka):
        if 'tochki.csv' not in files: continue
        with open(os.path.join(root, 'tochki.csv'), encoding='utf-8-sig') as f:
            for r in csv.DictReader(f, delimiter=';'):
                lat, lon, h, p = (chislo(r.get(k, '')) for k in ('lat', 'lon', 'высота_м', 'угол_камеры'))
                if None in (lat, lon, h, p) or not -90 <= p < 0: continue
                vis = r.get('доля_видимой_земли_в_лесу', r.get('доля_видимой_земли', ''))
                yaw = yaw_dlya(root, r['файл']); d = 0.0
                clat, clon = lat, lon
                if yaw is not None and abs(p) < 85:
                    d = h / math.tan(math.radians(abs(p)))
                    clat, clon = sdvig(lat, lon, d * math.sin(math.radians(yaw)), d * math.cos(math.radians(yaw)))
                o = opisanie(T.najti(clat, clon))
                rows.append({'папка': os.path.relpath(root, a.papka), 'файл': r['файл'], 'высота_м': round(h),
                             'угол': round(p), 'съёмка': 'вниз' if abs(p) >= 70 else 'под углом',
                             'тип_по_разметке': r.get('тип', ''), 'видимость': vis,
                             'азимут': '' if yaw is None else round(yaw), 'смещение_центра_м': round(d),
                             'lat_центра': round(clat, 6), 'lon_центра': round(clon, 6), **o})
    if not rows: raise SystemExit('Не найдено ни одного tochki.csv с координатами')
    with open('vidimost_po_snimkam.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=';'); w.writeheader(); w.writerows(rows)
    # сводка: группа леса × высота (округл. до 10 м) × съёмка
    g = {}
    for r in rows:
        v = chislo(r['видимость'])
        if v is None: continue
        k = (r['группа'], int(round(r['высота_м'] / 10) * 10), r['съёмка'])
        g.setdefault(k, []).append(v)
    with open('vidimost_svodka.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['группа', 'высота_м', 'съёмка', 'снимков', 'видимость_средняя', 'мин', 'макс'])
        for k in sorted(g):
            v = g[k]; w.writerow([*k, len(v), round(statistics.mean(v), 3), min(v), max(v)])
    vne = sum(r['группа'] == 'не лесфонд' for r in rows)
    print(f'Снимков: {len(rows)}, из них вне лесного фонда: {vne}')
    print('Таблицы: vidimost_po_snimkam.csv, vidimost_svodka.csv')


if __name__ == '__main__':
    main()
