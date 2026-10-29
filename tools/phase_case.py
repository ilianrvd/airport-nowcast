"""
Проверка на фазата върху минал случай (Open-Meteo Historical Forecast API)
=========================================================================
За всеки час: T2m, Td, Tw на 2 m, интеграл на мокрия термометър и фаза по
ICON и по GFS, плюс дела на снега в собствения валеж на ICON. Резултатът
се сравнява на ръка с METAR.

Употреба:
  python tools\\phase_case.py LBSF 2026-01-08 2026-01-08
  python tools\\phase_case.py LBSF 2026-01-10 2026-01-11 --from 18 --to 06
"""

import os, sys, argparse
import datetime as dt

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from config.settings import AIRPORTS
from src.process.phase import wet_bulb_from_td, wb_integral, classify, wet_bulb

URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
LEVELS = [1000, 950, 925, 900, 850, 800, 700, 600]
SFC = ["temperature_2m", "dew_point_2m", "surface_pressure", "precipitation",
       "rain", "showers", "snowfall", "weather_code"]
PL = [f"{v}_{p}hPa" for p in LEVELS
      for v in ("temperature", "relative_humidity", "geopotential_height")]


def fetch(model, a, d0, d1):
    params = {"latitude": a["lat"], "longitude": a["lon"], "models": model,
              "hourly": ",".join(SFC + PL), "start_date": d0, "end_date": d1,
              "timezone": "GMT", "cell_selection": "nearest"}
    r = requests.get(URL, params=params, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"{model}: {r.status_code} {r.text[:200]}")
    return r.json()["hourly"]


def phase_series(h, z_sfc):
    out = []
    for k, t in enumerate(h["time"]):
        t2, td, ps = h["temperature_2m"][k], h["dew_point_2m"][k], h["surface_pressure"][k]
        if None in (t2, td, ps):
            out.append(None)
            continue
        tw2 = wet_bulb_from_td(t2, td, ps)
        levels = [(p, h[f"geopotential_height_{p}hPa"][k], h[f"temperature_{p}hPa"][k],
                   h[f"relative_humidity_{p}hPa"][k]) for p in LEVELS]
        I, warm = wb_integral(levels, z_sfc, tw2)
        pr, sn = h["precipitation"][k] or 0.0, h["snowfall"][k] or 0.0
        share = (sn / 0.7) / pr if pr and pr > 0.05 else None   # snowfall cm → mm вода
        out.append({"t2": t2, "td": td, "tw2": tw2, "I": I, "warm": warm,
                    "phase": classify(I, t2), "precip": pr,
                    "snow_share": None if share is None else min(share, 1.0)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("icao")
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--from", dest="h0", type=int, default=0, help="първи час UTC на първия ден")
    ap.add_argument("--to", dest="h1", type=int, default=23, help="последен час UTC на последния ден")
    ap.add_argument("--icon", default="icon_eu", help="модел на ICON (icon_eu | icon_seamless)")
    args = ap.parse_args()

    a = AIRPORTS[args.icao.upper()]
    res = {}
    for name, model in (("ICON", args.icon), ("GFS", "gfs_seamless")):
        try:
            h = fetch(model, a, args.start, args.end)
            res[name] = (h["time"], phase_series(h, a["elev_m"]))
        except Exception as e:
            print(f"{name}: {e}")
    if not res:
        sys.exit("Няма данни")

    times = next(iter(res.values()))[0]
    t_first = dt.datetime.fromisoformat(args.start).replace(hour=args.h0)
    t_last = dt.datetime.fromisoformat(args.end).replace(hour=args.h1)
    print(f"\n{args.icao.upper()} {a['name']}, летище {a['elev_m']} m. Прагове: SN < 90 ≤ RASN ≤ 202.5 < RA °C·m; "
          f"FZRA при T2m ≤ 0 и I ≥ 90\n")
    print(f"{'час UTC':16} | {'ICON: T2/Td  Tw2   I °C·m  Twmax  фаза  валеж сняг%':54} | "
          f"{'GFS: T2/Td  Tw2   I °C·m  фаза  валеж':40}")
    print("-" * 116)
    for k, ts in enumerate(times):
        t = dt.datetime.fromisoformat(ts)
        if t < t_first or t > t_last:
            continue
        line = f"{ts.replace('T', ' '):16} |"
        for name in ("ICON", "GFS"):
            if name not in res:
                line += " —".ljust(55 if name == "ICON" else 40) + " |"
                continue
            p = res[name][1][k]
            if p is None:
                line += " няма данни".ljust(55 if name == "ICON" else 40) + " |"
                continue
            base = f" {p['t2']:5.1f}/{p['td']:5.1f} {p['tw2']:5.1f} {p['I']:7.0f}"
            if name == "ICON":
                sh = "—" if p["snow_share"] is None else f"{100 * p['snow_share']:.0f}"
                line += f"{base} {p['warm']:6.1f}  {p['phase'] or '—':5} {p['precip']:5.1f} {sh:>4} |"
            else:
                line += f"{base}  {p['phase'] or '—':5} {p['precip']:5.1f}".ljust(40) + " |"
        print(line)


if __name__ == "__main__":
    main()
