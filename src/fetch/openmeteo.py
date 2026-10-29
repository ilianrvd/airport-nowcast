"""
IFS HRES 9 km и GFS от Open-Meteo — точкови почасови данни за петте летища
==========================================================================
Една заявка на модел за всички летища. Ако Open-Meteo откаже заявката заради
непозната променлива, всяка променлива се проверява поотделно веднъж и
валидният списък се пази в cache/om_vars_<модел>.json.

Резултат: {"model", "fetched", "times": [ISO UTC], "airports": {icao: {var: [..]}},
           "dropped": [променливи, които моделът не дава]}
Вятър в kt, височини в m, видимост в m.
"""

import os, json, logging
import datetime as dt

import requests

logger = logging.getLogger("fetch.openmeteo")


def _request(url, lats, lons, model, hourly, past_h, fc_h, timeout=60):
    params = {
        "latitude": ",".join(f"{x:.4f}" for x in lats),
        "longitude": ",".join(f"{x:.4f}" for x in lons),
        "models": model,
        "hourly": ",".join(hourly),
        "past_hours": past_h,
        "forecast_hours": fc_h,
        "wind_speed_unit": "kn",
        "timezone": "GMT",
        "timeformat": "unixtime",
        "cell_selection": "nearest",
    }
    return requests.get(url, params=params, timeout=timeout,
                        headers={"User-Agent": "airport-nowcast/0.1"})


def _valid_vars(url, lat, lon, model, wanted, cache_dir):
    """Проверява всяка променлива поотделно; кешира резултата."""
    path = os.path.join(cache_dir, f"om_vars_{model}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if set(cached.get("wanted", [])) == set(wanted):
            return cached["valid"]
    valid = []
    for v in wanted:
        try:
            r = _request(url, [lat], [lon], model, [v], 0, 2, timeout=30)
            if r.status_code == 200:
                vals = r.json().get("hourly", {}).get(v, [])
                if any(x is not None for x in vals):
                    valid.append(v)
        except requests.RequestException as e:
            logger.warning(f"{model}/{v}: {e}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"wanted": wanted, "valid": valid}, f, ensure_ascii=False, indent=1)
    dropped = sorted(set(wanted) - set(valid))
    if dropped:
        logger.warning(f"{model}: моделът не дава {', '.join(dropped)}")
    return valid


def nb_coords(airports, cells_file, grid, radius):
    """Координатите на околността R×R около фиксираните клетки на ICON —
    същите точки, в които ICON дава околността (обща маска по суша/височина)."""
    with open(cells_file, encoding="utf-8") as f:
        fixed = json.load(f)
    out = {}
    for icao in airports:
        i, j = fixed[icao]["cell"]
        out[icao] = [(round(grid["lat0"] + (i + di) * grid["d"], 4),
                      round(grid["lon0"] + (j + dj) * grid["d"], 4))
                     for di in range(-radius, radius + 1) for dj in range(-radius, radius + 1)]
    return out


def fetch_neighbourhood(model, coords, variables, om, timeout=90):
    """Една заявка за всички точки на всички летища.
    Резултат: {"model", "times", "airports": {icao: {"lat", "lon", var: [[по точки] по време]}}}"""
    flat = [(icao, la, lo) for icao, pts in coords.items() for la, lo in pts]
    r = _request(om["url"], [p[1] for p in flat], [p[2] for p in flat], model, variables,
                 om["past_hours"], om["forecast_hours"], timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = [data]
    times = [dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).isoformat()
             for t in data[0]["hourly"]["time"]]
    out = {"model": model, "times": times, "airports": {}}
    for (icao, la, lo), d in zip(flat, data):
        a = out["airports"].setdefault(icao, {"lat": [], "lon": [], **{v: [] for v in variables}})
        a["lat"].append(la)
        a["lon"].append(lo)
        for v in variables:
            a[v].append(d.get("hourly", {}).get(v))
    # точки × време → време × точки (както при ICON)
    for a in out["airports"].values():
        for v in variables:
            pts = a[v]
            a[v] = [[(p[k] if p else None) for p in pts] for k in range(len(times))]
    return out


def fetch_model(key, cfg, airports, om, cache_dir):
    """key = 'ifs' | 'gfs'; cfg = OPEN_METEO['models'][key]."""
    icaos = list(airports)
    lats = [airports[i]["lat"] for i in icaos]
    lons = [airports[i]["lon"] for i in icaos]
    model, wanted = cfg["model"], cfg["hourly"]

    r = _request(om["url"], lats, lons, model, wanted, om["past_hours"], om["forecast_hours"])
    hourly_vars = wanted
    if r.status_code == 400:
        logger.info(f"{model}: заявката е отказана ({r.text[:120]}) — проверявам променливите")
        hourly_vars = _valid_vars(om["url"], lats[0], lons[0], model, wanted, cache_dir)
        r = _request(om["url"], lats, lons, model, hourly_vars, om["past_hours"], om["forecast_hours"])
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):        # едно летище → обект, няколко → списък
        data = [data]

    times = [dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).isoformat()
             for t in data[0]["hourly"]["time"]]
    out = {"model": model, "fetched": dt.datetime.now(dt.timezone.utc).isoformat(),
           "times": times, "airports": {},
           "dropped": sorted(set(wanted) - set(hourly_vars))}
    for icao, d in zip(icaos, data):
        h = d.get("hourly", {})
        out["airports"][icao] = {
            "grid_lat": d.get("latitude"), "grid_lon": d.get("longitude"),
            "grid_elev_m": d.get("elevation"),
            **{v: h.get(v) for v in hourly_vars},
        }
    return out
