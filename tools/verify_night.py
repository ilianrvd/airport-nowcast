"""
Проверка на архивирана таблица срещу METAR-ите, дошли след нея
==============================================================
За всяка колона на таблицата се търси METAR в ± 15 мин. Сравнява се:
  вятър (посока, скорост), T, видимост, таван, P(< 2000 / 1000 m) и
  P(таван < 1500 / 200 ft) срещу наблюдаваното събитие.

С --rebuild таблицата се смята наново от архивираните входни данни със
сегашния код (за сравнение стара / нова логика върху една и съща нощ).
ICON трябва да е в cache\\icon\\ (архивът сочи файла).

Употреба:
  python tools\\verify_night.py archive\\2026\\10\\28\\table_1907.json.gz
  python tools\\verify_night.py archive\\2026\\10\\28\\1905.json.gz --rebuild
  python tools\\verify_night.py … --icao LBWN
"""

import os, sys, json, gzip, argparse
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config.settings as S
from src.fetch.metar import fetch_metars


def load_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def rebuild(inputs_path):
    """Таблица от архивирани входни данни със сегашната логика."""
    import run_table
    inp = load_gz(inputs_path)
    icon = None
    if "icon" in inp:
        p = os.path.join(S.CACHE_DIR, inp["icon"]["file"])
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                icon = json.load(f)
        else:
            print(f"ВНИМАНИЕ: няма {p} — таблицата е без ICON")
    now = dt.datetime.fromisoformat(inp["created"])
    return run_table.build(inp, icon, now, S.ICAO_LIST)


def nearest_metar(metars, t, tol_min=15):
    best = None
    for m in metars:
        if m.get("time") is None:
            continue
        d = abs((m["time"] - t).total_seconds()) / 60
        if d <= tol_min and (best is None or d < best[0]):
            best = (d, m)
    return best[1] if best else None


def wdiff(a, b):
    return abs((a - b + 180) % 360 - 180)


def deg_hit(deg, event):
    """Степен (ВИС/УМЕР/НИС) срещу събитие: попадение / пропуск / фалшива / вярно."""
    if deg is None:
        return None
    yes = deg in ("ВИС", "УМЕР")
    return ("H" if event else "F") if yes else ("M" if event else "C")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--icao", default=None)
    args = ap.parse_args()

    T = rebuild(args.path) if args.rebuild else load_gz(args.path)
    created = dt.datetime.fromisoformat(T["created"])
    hours = int((dt.datetime.now(dt.timezone.utc) - created).total_seconds() // 3600) + 2
    icaos = [args.icao.upper()] if args.icao else list(T["airports"])
    metars = fetch_metars(icaos, S.METAR["url"], hours=min(max(hours, 3), 48))

    print(f"Таблица от {T['created'][:16]}Z{'  (пресметната наново)' if args.rebuild else ''}")
    for icao in icaos:
        a = T["airports"][icao]
        ms = metars.get(icao, [])
        print(f"\n{icao} — METAR-и: {len(ms)}")
        print(f"{'час':6} {'вод.':6} | {'вятър пр/наб':15} | {'T пр/наб':9} | {'вид пр/наб':13} "
              f"| P2000 P1000 | {'таван пр/наб':15} | P1500 P200")
        stats = {"dd": [], "ds": [], "dt": [], "v2": [], "v1": [], "c15": [], "c2": []}
        for c in a["cols"][1:]:
            t = dt.datetime.fromisoformat(c["time"])
            m = nearest_metar(ms, t)
            if m is None:
                continue
            # вятър
            wf = f"{c['wind_dir']}/{c['wind_kt']}" if c.get("wind_kt") is not None else "—"
            wo = f"{m['wind_dir']}/{m['wind_kt']}" if m.get("wind_kt") is not None else "—"
            if c.get("wind_kt") is not None and m.get("wind_kt") is not None:
                stats["ds"].append(c["wind_kt"] - m["wind_kt"])
                if isinstance(m.get("wind_dir"), int) and c["wind_dir"] not in (None, "VRB") and m["wind_kt"] >= 5:
                    stats["dd"].append(wdiff(int(c["wind_dir"]), m["wind_dir"]))
            if c.get("t") is not None and m.get("temp_c") is not None:
                stats["dt"].append(c["t"] - m["temp_c"])
            # видимост
            vo = m.get("vis_m")
            r2 = deg_hit(c.get("fog2000"), vo is not None and vo < 2000)
            r1 = deg_hit(c.get("fog1000"), vo is not None and vo < 1000)
            if r2: stats["v2"].append(r2)
            if r1: stats["v1"].append(r1)
            # таван
            co = m.get("ceiling_ft")
            co = co if (co is not None and co < 5000) else None
            q15 = deg_hit(c.get("cig1500"), co is not None and co < 1500)
            q2 = deg_hit(c.get("cig200"), co is not None and co < 200)
            if q15: stats["c15"].append(q15)
            if q2: stats["c2"].append(q2)
            print(f"{c['time'][11:16]:6} {c['lead']:6} | {wf:>7}/{wo:<7} | {str(c.get('t')):>3}/{str(m.get('temp_c')):<4} "
                  f"| {str(c.get('vis_m')):>6}/{str(vo):<6} | {c.get('fog2000') or '—':5} {c.get('fog1000') or '—':5} "
                  f"| {c.get('ceiling_txt') or '—':>7}/{str(co) if co else 'висок':<7} | {c.get('cig1500') or '—':5} {c.get('cig200') or '—'}")

        def mean_abs(x):
            return f"{sum(abs(v) for v in x) / len(x):.1f}" if x else "—"

        def bias(x):
            return f"{sum(x) / len(x):+.1f}" if x else "—"

        def hfmc(x):
            return " ".join(f"{k}{x.count(k)}" for k in "HMFC") if x else "—"

        print(f"  вятър: |Δпосока| {mean_abs(stats['dd'])}°, Δскорост {bias(stats['ds'])} kt "
              f"(|{mean_abs(stats['ds'])}|); T: {bias(stats['dt'])} °C (|{mean_abs(stats['dt'])}|)")
        print(f"  P(<2000): {hfmc(stats['v2'])}   P(<1000): {hfmc(stats['v1'])}   "
              f"P(таван<1500): {hfmc(stats['c15'])}   P(таван<200): {hfmc(stats['c2'])}")
        print("  (H попадение, M пропуск, F фалшива тревога, C вярно „няма“; УМЕР и ВИС се броят за „да“)")


if __name__ == "__main__":
    main()
