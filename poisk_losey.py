"""
Слепой просмотр кадров (сетка, камера вниз) и отметка лосей.

ПОРЯДОК (важно для честного расчёта):
  1) сначала весь вылет просматривается ОБЫЧНОЙ камерой:   --kamera W
  2) потом тот же вылет — ТЕПЛОВИЗОРОМ:                     --kamera T
  Тепловизионные кадры до окончания просмотра обычных не открывать,
  иначе будешь знать, где искать, и доля пропусков получится заниженной.

Управление:
  левый клик        — отметить лося (жёлтый кружок)
  правый клик       — убрать ближайшую отметку
  u                 — последняя отметка «не уверен» (оранжевая)
  пробел / →        — следующий кадр (кадр без отметок тоже сохраняется как просмотренный)
  ← / b             — предыдущий кадр
  Голубой пунктир на обычных кадрах — участок, который видит тепловизор.
  Отмечать надо ВСЕХ лосей на кадре, и внутри, и снаружи пунктира.
  колесо мыши       — приблизить/отдалить (панель matplotlib), Home — вернуть
  q                 — выход (можно продолжить потом с того же места)

Результат в папке вылета:
  losi_W.csv / losi_T.csv           — отметки (кадр, пиксели, координаты дрона, высота, азимут)
  prosmotreno_W.csv / _T.csv        — какие кадры просмотрены

Запуск:  python poisk_losey.py "папка_вылета" --kamera W
Нужно:   python -m pip install pillow matplotlib
"""
import argparse, csv, os, sys
from PIL import Image
import matplotlib
for k in ('keymap.save', 'keymap.back', 'keymap.forward', 'keymap.fullscreen', 'keymap.quit'):
    matplotlib.rcParams[k] = []
import matplotlib.pyplot as plt
from gis_vydely import xmp

POLYA = ['файл', 'lat', 'lon', 'высота_м', 'азимут', 'угол', 'x', 'y', 'ширина', 'высота_px', 'уверенность']


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('papka')
    ap.add_argument('--kamera', choices=['W', 'T'], required=True)
    ap.add_argument('--min_ugol', type=float, default=70, help='брать кадры с камерой вниз круче этого угла')
    ap.add_argument('--min_h', type=float, default=30)
    a = ap.parse_args()
    if a.kamera == 'W' and os.path.exists(os.path.join(a.papka, 'losi_T.csv')):
        print('ВНИМАНИЕ: тепловизор этого вылета уже просмотрен — просмотр обычной камеры будет не слепым.')
    suf = f'_{a.kamera}.JPG'
    files = sorted(os.path.join(r, f) for r, _, fs in os.walk(a.papka) for f in fs if f.upper().endswith(suf))
    fo_l = os.path.join(a.papka, f'losi_{a.kamera}.csv'); fo_p = os.path.join(a.papka, f'prosmotreno_{a.kamera}.csv')
    done = set()
    if os.path.exists(fo_p):
        with open(fo_p, encoding='utf-8-sig') as f: done = {l.strip() for l in f if l.strip() and l.strip() != 'файл'}
    meta, todo = {}, []
    for p in files:
        m = xmp(p); meta[p] = m
        if m['pitch'] is None or m['h'] is None or abs(m['pitch']) < a.min_ugol or m['h'] < a.min_h: continue
        todo.append(p)
    rest = [p for p in todo if os.path.relpath(p, a.papka) not in done]
    print(f'Кадров {a.kamera}: {len(files)}, сетка (камера вниз): {len(todo)}, осталось: {len(rest)}')
    if not rest: return
    nl, npr = not os.path.exists(fo_l), not os.path.exists(fo_p)
    fl = open(fo_l, 'a', newline='', encoding='utf-8-sig'); wl = csv.writer(fl, delimiter=';')
    fp = open(fo_p, 'a', newline='', encoding='utf-8-sig'); wp = csv.writer(fp, delimiter=';')
    if nl: wl.writerow(POLYA)
    if npr: wp.writerow(['файл'])
    marks = {}            # кадр -> [[x, y, уверенность]]
    i = [0]
    fig, ax = plt.subplots(figsize=(13, 8))
    st = {'quit': False}

    def save(p):
        rel = os.path.relpath(p, a.papka)
        if rel in done: return
        m = meta[p]; W, H = Image.open(p).size
        for x, y, u in marks.get(p, []):
            wl.writerow([rel, m['lat'], m['lon'], m['h'], m['yaw'], m['pitch'], round(x), round(y), W, H, u])
        wp.writerow([rel]); done.add(rel); fl.flush(); fp.flush()

    def show():
        p = rest[i[0]]; im = Image.open(p); ax.clear(); ax.imshow(im); ax.axis('off')
        if a.kamera == 'W':     # пунктир — участок кадра, который видит тепловизор H20N
            Wd, Hd = im.size; fw, fh = 0.503, 0.714
            ax.add_patch(plt.Rectangle((Wd * (1 - fw) / 2, Hd * (1 - fh) / 2), Wd * fw, Hd * fh,
                                       fill=False, ec='cyan', ls='--', lw=1))
        for x, y, u in marks.get(p, []):
            ax.add_patch(plt.Circle((x, y), 25 if a.kamera == 'W' else 12, fill=False, lw=2,
                                    ec='yellow' if u == 'да' else 'orange'))
        m = meta[p]
        ax.set_title(f"{i[0]+1}/{len(rest)}  {os.path.basename(p)}  h={m['h']:.0f} м   отмечено: {len(marks.get(p, []))}"
                     "   клик — лось, правый — убрать, u — не уверен, пробел — дальше, ← — назад, q — выход", fontsize=9)
        fig.canvas.draw_idle()

    def click(e):
        if e.inaxes != ax or getattr(fig.canvas.toolbar, 'mode', ''): return
        p = rest[i[0]]; L = marks.setdefault(p, [])
        if e.button == 1: L.append([e.xdata, e.ydata, 'да'])
        elif e.button == 3 and L:
            L.remove(min(L, key=lambda t: (t[0] - e.xdata) ** 2 + (t[1] - e.ydata) ** 2))
        show()

    def key(e):
        p = rest[i[0]]
        if e.key == 'u' and marks.get(p): marks[p][-1][2] = 'нет'; show()
        elif e.key in (' ', 'right'):
            save(p)
            if i[0] + 1 < len(rest): i[0] += 1; show()
            else: print('Все кадры просмотрены.'); plt.close(fig)
        elif e.key in ('left', 'b') and i[0] > 0:
            if os.path.relpath(rest[i[0] - 1], a.papka) in done:
                print('Предыдущий кадр уже сохранён; исправить можно прямо в', fo_l)
            i[0] -= 1; show()
        elif e.key == 'q': plt.close(fig)

    fig.canvas.mpl_connect('button_press_event', click); fig.canvas.mpl_connect('key_press_event', key)
    show(); plt.show(); fl.close(); fp.close()
    print('Сохранено:', fo_l)


if __name__ == '__main__':
    main()
