"""
ALADIN (CHMI) от Open-Meteo — само за архива
============================================
Една заявка за петте летища, 72 ч напред. Файлът е по run:
cache/aladin/aladin_YYYYMMDDHH.json. Ако файлът за текущия run вече е записан
(в кеша или в клона archive), архивната стъпка не го добавя повторно.

Run-ът се взема от meta.json на Open-Meteo. Ако meta не е достъпен, run-ът се
приема за последния кратен на 6 ч момент поне 4 ч назад (приблизително).
Неизвестните променливи се отсяват като при IFS/GFS (виж openmeteo.py).
"""

import os, json, logging
import datetime as dt

import requests

from src.fetch.openmeteo import _valid_vars

logger = logging.getLogger("fetch.aladin")


def _run_time(cfg, now):
    try:
        r = requests.get(cfg["meta_url"], timeout=20,
                         headers={"User-Agent": "airport-nowcast/0.1"})
        r.raise_for_status()
        t = r.json().get("last_run_initialisation_time")
        if t:
            return dt.datetime.fromtimestamp(int(t), tz=dt.timezone.utc), "meta"
    except Exception as e:
        logger.warning(f"ALADIN meta.json: {e}")
    t = now - dt.timedelta(hours=4)
    t = t.replace(hour=t.hour - t.hour % cfg["run_every_h"], minute=0, second=0, microsecond=0)
    return t, "приблизително"


def fetch_aladin(now, airports, cfg, om, cache_dir):
    """Връща (път, run_iso, нов_ли). Не тегли повторно, ако run-ът вече е в кеша."""
    run, run_src = _run_time(cfg, now)
    out_dir = os.path.join(cache_dir, "aladin")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"aladin_{run:%Y%m%d%H}.json")
    if os.path.exists(path):
        return path, run.isoformat(), False

    icaos = list(airports)
    lats = [airports[i]["lat"] for i in icaos]
    lons = [airports[i]["lon"] for i in icaos]

    def req(hourly):
        return requests.get(om["url"], timeout=90, headers={"User-Agent": "airport-nowcast/0.1"},
                            params={
                                "latitude": ",".join(f"{x:.4f}" for x in lats),
                                "longitude": ",".join(f"{x:.4f}" for x in lons),
                                "models": cfg["model"], "hourly": ",".join(hourly),
                                "forecast_hours": cfg["forecast_hours"], "past_hours": 0,
                                "wind_speed_unit": "kn", "timezone": "GMT",
                                "timeformat": "unixtime", "cell_selection": cfg["cell_selection"],
                            })

    wanted = cfg["hourly"]
    hourly = wanted
    r = req(hourly)
    if r.status_code == 400:
        logger.info(f"ALADIN: заявката е отказана ({r.text[:120]}) — проверявам променливите")
        hourly = _valid_vars(om["url"], lats[0], lons[0], cfg["model"], wanted, cache_dir)
        r = req(hourly)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = [data]

    times = [dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).isoformat()
             for t in data[0]["hourly"]["time"]]
    res = {"model": cfg["model"], "run": run.isoformat(), "run_source": run_src,
           "fetched": now.isoformat(), "times": times, "airports": {},
           "dropped": sorted(set(wanted) - set(hourly))}
    for icao, d in zip(icaos, data):
        h = d.get("hourly", {})
        vals = {v: h.get(v) for v in hourly}
        res["airports"][icao] = {
            "grid_lat": d.get("latitude"), "grid_lon": d.get("longitude"),
            "grid_elev_m": d.get("elevation"),
            "empty": [v for v, a in vals.items() if not a or all(x is None for x in a)],
            **vals,
        }
    try:
        res["nb"] = _fetch_nb(airports, cfg, om)
    except Exception as e:                      # околността е допълнение — не губим основния файл
        logger.warning(f"ALADIN околност: {e}")
        res["nb"] = None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    return path, run.isoformat(), True


def _fetch_nb(airports, cfg, om):
    """Околност (2R+1)² около всяко летище; една заявка. Резултат като при ICON/IFS:
    {"vars": [...], "airports": {icao: {"lat": [...], "lon": [...], var: [[по точки] по време]}}}"""
    nb = cfg.get("nb")
    if not nb:
        return None
    R, dla, dlo, vars_ = nb["radius"], nb["step_lat"], nb["step_lon"], nb["hourly"]
    flat = [(icao, round(a["lat"] + i * dla, 4), round(a["lon"] + j * dlo, 4))
            for icao, a in airports.items() for i in range(-R, R + 1) for j in range(-R, R + 1)]
    r = requests.get(om["url"], timeout=90, headers={"User-Agent": "airport-nowcast/0.1"}, params={
        "latitude": ",".join(f"{p[1]:.4f}" for p in flat),
        "longitude": ",".join(f"{p[2]:.4f}" for p in flat),
        "models": cfg["model"], "hourly": ",".join(vars_),
        "forecast_hours": cfg["forecast_hours"], "past_hours": 0,
        "wind_speed_unit": "kn", "timezone": "GMT", "timeformat": "unixtime",
        "cell_selection": "nearest"})
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = [data]
    out = {"vars": vars_, "airports": {}}
    for (icao, la, lo), d in zip(flat, data):
        a = out["airports"].setdefault(icao, {"lat": [], "lon": [], **{v: [] for v in vars_}})
        a["lat"].append(d.get("latitude", la))
        a["lon"].append(d.get("longitude", lo))
        for v in vars_:
            a[v].append((d.get("hourly") or {}).get(v))
    for a in out["airports"].values():           # точки × време → време × точки
        n = max((len(x) for v in vars_ for x in a[v] if x), default=0)
        for v in vars_:
            pts = a[v]
            a[v] = [[(p[k] if p else None) for p in pts] for k in range(n)]
    return out
