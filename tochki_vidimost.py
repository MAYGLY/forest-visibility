"""
Ручная оценка видимости земли сквозь полог по сетке точек.

На снимок (обычная камера, лучше снятый вниз) накладывается сетка 7×7 = 49 точек.
По очереди подсвечивается одна точка. Нажимайте:
   1 — в лесу (среди деревьев/кустов) земля в этой точке ВИДНА — просвет
   0 — точка закрыта кроной, стволом, ветвями
   2 — открытое место (поляна, поле, дорога, вода) — деревьев рядом нет
ВАЖНО: смотреть только то, что точно под центром точки. Центр на ветке/кроне — 0, даже если
рядом просвет; центр на снегу — 1, даже если вокруг ёлки. Не видно — увеличить лупой; если всё равно
непонятно — 0. В безлистном лесу: снег виден сквозь ветки в самом центре — 1.
2 — только НЕ ЛЕС: поле, вырубка, широкая дорога. Тропа, волок, прогал между деревьями — это лес: 1 или 0.
Доля видимой земли считается только по лесным точкам: 1 / (0 + 1).
Открытые точки (2) учитываются отдельно — так смешанные снимки тоже годятся.
   Backspace — отменить последнюю точку
   s — пропустить снимок
После 49 точек — тип насаждения:
   1 хвойный   2 смешанный   3 лиственный   4 кустарник/молодняк   5 редколесье
Ошиблись с типом — исправьте потом прямо в tochki.csv (открывается в Excel).
Снимки со взлёта/посадки (камера смотрит вперёд или высота < 15 м) пропускаются сами.
Результат дописывается в tochki.csv в той же папке; уже размеченные снимки пропускаются,
так что можно прерваться и продолжить.

Запуск:  python tochki_vidimost.py "папка_со_снимками"   [--grid 7]  [--mask "_W.JPG"]  [--oblast T]
  --oblast T — сетка ставится только в центральной части кадра, которую видит
               тепловизор H20N (≈50 % ширины и ≈71 % высоты обычного кадра).
               Используйте этот режим для оценки видимости в полосе тепловизора.
Нужно:   python -m pip install pillow matplotlib
"""
import os, re, sys, csv, argparse
from PIL import Image
import matplotlib
matplotlib.rcParams['keymap.save'] = []; matplotlib.rcParams['keymap.back'] = []
matplotlib.rcParams['keymap.fullscreen'] = []
import matplotlib.pyplot as plt

TYPES = {'1': 'хвойный', '2': 'смешанный', '3': 'лиственный', '4': 'кустарник', '5': 'редколесье'}

def xmp(path):
    with open(path, 'rb') as f: d = f.read(300000)
    i, j = d.find(b'<x:xmpmeta'), d.find(b'</x:xmpmeta')
    s = d[i:j].decode('utf-8', 'ignore') if i >= 0 else ''
    g = lambda t: (re.search(r'drone-dji:' + t + r'="([^"]+)"', s) or [None, ''])[1]
    return g('RelativeAltitude'), g('GimbalPitchDegree'), g('GpsLatitude') or g('Latitude'), g('GpsLongitude') or g('Longitude')

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('folder')
    ap.add_argument('--grid', type=int, default=7); ap.add_argument('--mask', default='_W.JPG')
    ap.add_argument('--oblast', choices=['vse', 'T'], default='vse')
    a = ap.parse_args()
    if a.grid < 1:
        ap.error("Размер сетки должен быть положительным.")
    files = sorted(os.path.join(r, f) for r, _, fs in os.walk(a.folder) for f in fs
                   if f.upper().endswith(a.mask.upper()) or (a.mask == '*' and f.lower().endswith(('.jpg', '.png'))))
    out = os.path.join(a.folder, 'tochki.csv')
    if os.path.exists(out):
        with open(out, encoding='utf-8-sig') as f: head = f.readline()
        if 'открыто_2' not in head:
            old = os.path.join(a.folder, 'tochki_старый_формат.csv')
            os.replace(out, old); print('Старая разметка переименована в', old, '— эти снимки нужно разметить заново.')
    done = set()
    if os.path.exists(out):
        with open(out, encoding='utf-8-sig') as f: done = {r['файл'] for r in csv.DictReader(f, delimiter=';')}
    todo = []
    for p in files:
        if os.path.relpath(p, a.folder) in done: continue
        h, pt, _, _ = xmp(p)
        try:
            if float(pt) > -20 or float(h) < 15:
                print('пропуск (взлёт/камера вперёд):', os.path.basename(p)); continue
        except (TypeError, ValueError):
            pass
        todo.append(p)
    print(f'Снимков: {len(files)}, осталось разметить: {len(todo)}')
    if not todo: return
    new = not os.path.exists(out)
    fo = open(out, 'a', newline='', encoding='utf-8-sig'); w = csv.writer(fo, delimiter=';')
    if new: w.writerow(['файл', 'высота_м', 'угол_камеры', 'lat', 'lon', 'тип', 'закрыто_0', 'видно_1', 'открыто_2', 'доля_видимой_земли_в_лесу', 'доля_открытого', 'точки'])
    n = a.grid
    for k, p in enumerate(todo):
        im = Image.open(p).convert('RGB'); W, H = im.size
        fw, fh = (0.503, 0.714) if a.oblast == 'T' else (1.0, 1.0)
        x0, y0 = W * (1 - fw) / 2, H * (1 - fh) / 2
        pts = [(x0 + (c + .5) * W * fw / n, y0 + (r + .5) * H * fh / n) for r in range(n) for c in range(n)]
        st = {'v': [], 'type': None, 'skip': False, 'quit': False}
        fig, ax = plt.subplots(figsize=(13, 7.5)); ax.imshow(im); ax.axis('off')
        sc = ax.scatter([x for x, _ in pts], [y for _, y in pts], s=60, facecolors='none', edgecolors='yellow')
        cur = ax.scatter([], [], s=260, facecolors='none', edgecolors='red', linewidths=2)
        def redraw():
            cols = [{1: 'lime', 0: 'red', 2: 'deepskyblue'}[v] for v in st['v']] + ['yellow'] * (len(pts) - len(st['v']))
            sc.set_edgecolors(cols)
            i = len(st['v'])
            cur.set_offsets([pts[i]] if i < len(pts) else [[-100, -100]])
            msg = (f'{k+1}/{len(todo)}  {os.path.basename(p)}   точка {min(i+1, len(pts))}/{len(pts)}:  1 — просвет В ЦЕНТРЕ, 0 — закрыто, 2 — не лес (поле, вырубка), Backspace — назад, s — пропуск, q — выход'
                   if i < len(pts) else 'Тип леса на снимке: 1 хвойный, 2 смешанный, 3 лиственный, 4 кустарник/молодняк, 5 редколесье')
            ax.set_title(msg, fontsize=10); fig.canvas.draw_idle()
        def key(e):
            if e.key == 'q': st['quit'] = True; plt.close(fig); return
            if e.key == 's': st['skip'] = True; plt.close(fig); return
            if len(st['v']) < len(pts):
                if e.key in ('0', '1', '2'): st['v'].append(int(e.key))
                elif e.key == 'backspace' and st['v']: st['v'].pop()
            elif e.key in TYPES: st['type'] = TYPES[e.key]; plt.close(fig); return
            elif e.key == 'backspace': st['v'].pop()
            redraw()
        fig.canvas.mpl_connect('key_press_event', key); redraw(); plt.show()
        if st['quit']: break
        if st['skip'] or st['type'] is None: continue
        h, pt, la, lo = xmp(p); v = st['v']
        n0, n1, n2 = v.count(0), v.count(1), v.count(2)
        vis = round(n1 / (n0 + n1), 3) if n0 + n1 else ''
        w.writerow([os.path.relpath(p, a.folder), h, pt, la, lo, st['type'], n0, n1, n2,
                    vis, round(n2 / len(v), 3), ''.join(map(str, v))]); fo.flush()
    fo.close(); print('Сохранено:', out)

if __name__ == '__main__':
    main()
