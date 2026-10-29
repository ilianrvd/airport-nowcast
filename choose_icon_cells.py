"""
Избор на клетката на ICON-EU за всяко летище
============================================
Показва 3×3 (или --radius) клетки около летището с fr_land, hsurf и
разстояние и предлага клетка: най-близката суша (fr_land ≥ 0.5), чиято
височина е в рамките на 100 m от летището. Записва config/icon_cells.json само с --write.

Употреба:  python choose_icon_cells.py            (само показва)
           python choose_icon_cells.py --write    (записва предложенията)
"""

import os, sys, bz2, json, argparse, tempfile
import datetime as dt

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.settings import AIRPORTS, ICON_DWD, ICON_CELLS_FILE, HORIZON_H
from src.fetch.icon_dwd import find_run, _fname_ti, _session, _read_grib


def _ti(run, field, tmp):
    url = f"{ICON_DWD['base_url']}/{run.hour:02d}/{field}/{_fname_ti(run, field)}"
    r = _session.get(url, timeout=120)
    r.raise_for_status()
    p = os.path.join(tmp, f"{field}.grib2")
    with open(p, "wb") as f:
        f.write(bz2.decompress(r.content))
    return _read_grib(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--radius", type=int, default=1)
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    run, _ = find_run(now, ICON_DWD, HORIZON_H)
    if run is None:
        sys.exit("Няма наличен run на ICON-EU")
    with tempfile.TemporaryDirectory() as tmp:
        lat, lon, hs = _ti(run, "hsurf", tmp)
        _, _, fl = _ti(run, "fr_land", tmp)

    chosen = {}
    R = args.radius
    for icao, a in AIRPORTS.items():
        i0, j0 = int(np.abs(lat - a["lat"]).argmin()), int(np.abs(lon - a["lon"]).argmin())
        print(f"\n{icao} {a['name']}  ({a['lat']:.4f}, {a['lon']:.4f}), летище {a['elev_m']} m")
        print(f"{'i,j':>10} {'lat':>8} {'lon':>8} {'fr_land':>8} {'hsurf':>7} {'dist km':>8}")
        best, best_key = None, None
        for di in range(-R, R + 1):
            for dj in range(-R, R + 1):
                i, j = i0 + di, j0 + dj
                d_km = 111.2 * np.hypot(lat[i] - a["lat"], (lon[j] - a["lon"]) * np.cos(np.radians(a["lat"])))
                f, h = float(fl[i, j]), float(hs[i, j])
                mark = " ←най-близка" if (di, dj) == (0, 0) else ""
                print(f"{i:>5},{j:<4} {lat[i]:8.4f} {lon[j]:8.4f} {f:8.2f} {h:7.0f} {d_km:8.1f}{mark}")
                # ключ: първо суша, после височина в рамките на 100 m от летището, после разстояние
                key = (0 if f >= 0.5 else 1, 0 if abs(h - a["elev_m"]) <= 100 else 1, d_km)
                if best_key is None or key < best_key:
                    best_key, best = key, (i, j, f, h)
        i, j, f, h = best
        print(f"  предложение: [{i}, {j}]  fr_land {f:.2f}, hsurf {h:.0f} m")
        chosen[icao] = {"cell": [i, j], "fr_land": round(f, 2), "hsurf_m": round(h),
                        "run": run.strftime("%Y%m%d%H")}

    if args.write:
        with open(ICON_CELLS_FILE, "w", encoding="utf-8") as f:
            json.dump(chosen, f, ensure_ascii=False, indent=2)
        print(f"\nЗаписано: {ICON_CELLS_FILE}")
    else:
        print("\nНищо не е записано. Пусни с --write, ако приемаш предложенията.")


if __name__ == "__main__":
    main()
