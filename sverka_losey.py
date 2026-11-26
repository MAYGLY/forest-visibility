"""
Сверка отметок лосей обычной камеры (losi_W.csv) и тепловизора (losi_T.csv)
и расчёт доли пропусков обычной камерой по типам леса.

  1. Каждую отметку переводит в координаты на земле (камера вниз:
     размер кадра на земле = высота × угол обзора камеры из EXIF).
  2. Отметки, лежащие ближе --radius м друг к другу и снятые с разницей не
     более --dt с, считает одним животным (один лось виден на нескольких
     соседних кадрах и на обеих камерах).
  3. Для каждого животного: видели ли его на обычной камере, на тепловизоре,
     в каком выделе он был.
  4. Сводка по группам леса: сколько всего, сколько видно обычной камерой,
     доля пропусков и поправочный множитель K = всего / видно_обычной.

Сравниваются только лоси в полосе тепловизора (она примерно вдвое уже полосы
обычной камеры: на 100 м около 65 м против 130 м); лоси, видимые только сбоку
обычной камерой, помечаются в_полосе_T = 0 и в сводку не идут.

ОГРАНИЧЕНИЕ: «всего» = видно хотя бы одной камерой. Лосей, которых не увидел
и тепловизор, этот расчёт не учитывает — для них нужны другие данные (GPS-ошейники
или наземная проверка).

Запуск:
  python sverka_losey.py --taksaciya "stands.gpkg" --papka "flights"
"""
import argparse, csv, math, os, re
from datetime import datetime
from gis_vydely import Taksaciya, opisanie, sdvig

# Диагональные углы обзора (DFOV) по паспорту DJI. EXIF тепловизионных кадров H20N
# ошибочно пишет 29 мм-экв. — поэтому берём паспортные значения по ширине кадра.
#   H20N: широкоугольная 1920×1080 — 73,6°; широкий тепловизор 640×512 — 45,5°
#   H20T: широкоугольная 4056×3040 — 82,9°; тепловизор 640×512 — 40,6°
DFOV = {('ZH20N', 1920): 73.6, ('ZH20N', 640): 45.5, ('ZH20T', 4056): 82.9, ('ZH20T', 640): 40.6}


def tan_pol(dfov, W, H):
    """Тангенсы половинных углов обзора по ширине и высоте кадра."""
    t = math.tan(math.radians(dfov / 2)); d = math.hypot(W, H)
    return t * W / d, t * H / d


def dfov_iz(path, W):
    model = 'ZH20N'
    try:
        from PIL import Image
        model = (Image.open(path).getexif().get(272) or model).strip()
    except Exception:
        pass
    return DFOV.get((model, int(W)), DFOV.get(('ZH20N', int(W)), 73.6 if W > 1000 else 45.5))


def vremya(fn):
    m = re.search(r'DJI_(\d{14})', fn)
    return datetime.strptime(m.group(1), '%Y%m%d%H%M%S') if m else None


def chitat(papka, kam):
    p = os.path.join(papka, f'losi_{kam}.csv')
    if not os.path.exists(p): return []
    out = []
    with open(p, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f, delimiter=';'):
            try:
                lat, lon, h, yaw = (float(r[k]) for k in ('lat', 'lon', 'высота_м', 'азимут'))
                x, y, W, H = (float(r[k]) for k in ('x', 'y', 'ширина', 'высота_px'))
            except (ValueError, KeyError):
                continue
            tw, th = tan_pol(dfov_iz(os.path.join(papka, r['файл']), W), W, H)
            vpravo = (x / W - 0.5) * 2 * h * tw         # м вправо от центра кадра
            vpered = (0.5 - y / H) * 2 * h * th         # м вперёд (верх кадра = направление камеры)
            yr = math.radians(yaw)
            vost = vpravo * math.cos(yr) + vpered * math.sin(yr)
            sev = -vpravo * math.sin(yr) + vpered * math.cos(yr)
            la, lo = sdvig(lat, lon, vost, sev)
            out.append({'kam': kam, 'файл': r['файл'], 't': vremya(r['файл']), 'lat': la, 'lon': lo,
                        'unc': r.get('уверенность', 'да') == 'нет', 'vbok': abs(vpravo), 'h': h})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--taksaciya', required=True); ap.add_argument('--papka', required=True)
    ap.add_argument('--radius', type=float, default=20, help='м: ближе — одно животное')
    ap.add_argument('--dt', type=float, default=60, help='с: в пределах — один пролёт')
    ap.add_argument('--layer', help='Название слоя при нескольких полигональных слоях')
    a = ap.parse_args()
    T = Taksaciya(a.taksaciya, a.layer)
    zveri = []
    for root, _, files in os.walk(a.papka):
        if 'prosmotreno_W.csv' not in files or 'prosmotreno_T.csv' not in files: continue
        pts = chitat(root, 'W') + chitat(root, 'T')
        # одиночная связь: точки рядом по месту и времени — одно животное
        grp = list(range(len(pts)))
        def g(i):
            while grp[i] != i: grp[i] = grp[grp[i]]; i = grp[i]
            return i
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                pi, pj = pts[i], pts[j]
                dist = math.hypot((pi['lon'] - pj['lon']) * 111320 * math.cos(math.radians(pi['lat'])),
                                  (pi['lat'] - pj['lat']) * 111320)
                dt = abs((pi['t'] - pj['t']).total_seconds()) if pi['t'] and pj['t'] else 0
                if dist <= a.radius and dt <= a.dt: grp[g(i)] = g(j)
        cl = {}
        for i, p in enumerate(pts): cl.setdefault(g(i), []).append(p)
        for c in cl.values():
            lat = sum(p['lat'] for p in c) / len(c); lon = sum(p['lon'] for p in c) / len(c)
            nW = sum(p['kam'] == 'W' for p in c); nT = sum(p['kam'] == 'T' for p in c)
            # тепловизор снимает полосу примерно вдвое уже обычной камеры: лося, который
            # был только сбоку от полосы тепловизора, сравнивать нечестно
            pol_T = min(p['h'] for p in c) * tan_pol(DFOV[('ZH20N', 640)], 640, 512)[0]
            v_polose = nT > 0 or any(p['vbok'] <= pol_T for p in c)
            t0 = min(p['t'] for p in c if p['t']) if any(p['t'] for p in c) else ''
            zveri.append({'вылет': os.path.relpath(root, a.papka), 'время': t0, 'lat': round(lat, 6), 'lon': round(lon, 6),
                          'кадров_W': nW, 'кадров_T': nT, 'видно_W': int(nW > 0), 'видно_T': int(nT > 0),
                          'не_уверен': int(all(p['unc'] for p in c)), 'в_полосе_T': int(v_polose), **opisanie(T.najti(lat, lon))})
    if not zveri:
        raise SystemExit('Нет вылетов, где просмотрены обе камеры (prosmotreno_W.csv и prosmotreno_T.csv)')
    with open('losi_sverka.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(zveri[0]), delimiter=';'); w.writeheader(); w.writerows(zveri)
    s = {}
    for z in zveri:
        if z['не_уверен'] or not z['в_полосе_T']: continue
        for k in (z['группа'], 'ВСЕГО'):
            d = s.setdefault(k, [0, 0, 0]); d[0] += 1; d[1] += z['видно_W']; d[2] += z['видно_T']
    with open('losi_svodka.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['группа', 'лосей_всего', 'видно_обычной', 'видно_тепловизором', 'доля_пропуска_обычной', 'K_обычной'])
        for k, (n, nw, nt) in sorted(s.items(), key=lambda kv: kv[0] == 'ВСЕГО'):
            w.writerow([k, n, nw, nt, round(1 - nw / n, 3), round(n / nw, 2) if nw else ''])
            print(f'{k:12s} всего {n:3d}  обычной {nw:3d}  тепловизором {nt:3d}  пропуск {100*(1-nw/n):.0f} %')
    print('Таблицы: losi_sverka.csv, losi_svodka.csv')


if __name__ == '__main__':
    main()
