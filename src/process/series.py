"""
Времева ос на таблицата и моделни серии в общ вид
=================================================
Колоните: „Сега“ = времето на последния METAR, после през 30 мин до +6 ч и
през 1 ч до +12 ч. Всички моделни серии се превръщат в общ вид
{"t": [epoch s], "u", "v" (kt), "spd" (kt), "gust" (kt), "t2m", "td2m" (°C)}
и се интерполират линейно във времето към колоните; вятърът през u/v.
"""

import math
import datetime as dt

import numpy as np

KT = 1.943844  # m/s → kt


def timeline(t0: dt.datetime, cfg) -> list:
    cols = [t0]
    n30 = int(6 * 60 / cfg["step_min_0_6"])
    cols += [t0 + dt.timedelta(minutes=cfg["step_min_0_6"] * k) for k in range(1, n30 + 1)]
    cols += [t0 + dt.timedelta(hours=h) for h in range(7, cfg["horizon_h"] + 1, cfg["step_h_6_12"])]
    return cols


def lead_label(t0, t):
    m = int(round((t - t0).total_seconds() / 60))
    if m == 0:
        return "METAR"
    if m <= 360:
        return f"+{m // 60}:{m % 60:02d}"
    return f"+{m // 60}"


def _uv(spd, ddeg):
    """Метеорологична посока (откъдето духа) → u, v."""
    r = math.radians(ddeg)
    return -spd * math.sin(r), -spd * math.cos(r)


def uv_to_dirspd(u, v):
    spd = math.hypot(u, v)
    d = (math.degrees(math.atan2(-u, -v)) + 360) % 360
    return d, spd


def _arr(x):
    return np.array([np.nan if v is None else v for v in x], dtype=float)


def _median3(y):
    """Медиана от 3 последователни стойности — маха единични скокове
    (напр. ICON ceiling около прага), без да размива реалната промяна."""
    out = y.copy()
    for k in range(1, len(y) - 1):
        w = y[k - 1:k + 2]
        if np.isfinite(w).all():
            out[k] = float(np.median(w))
    return out


def from_icon(icon: dict, icao: str) -> dict:
    a = icon["airports"][icao]["sl"]
    t = [dt.datetime.fromisoformat(s).timestamp() for s in icon["valid"]]
    u, v = _arr(a["u_10m"]) * KT, _arr(a["v_10m"]) * KT
    def hourly(acc):
        """Акумулирано от началото на run-а → mm за изминалия час.
        Първата стъпка няма предишна → NaN."""
        x = _arr(acc)
        r = np.full_like(x, np.nan)
        steps = icon["steps"]
        for k in range(1, len(x)):
            if steps[k] - steps[k - 1] == 1:
                r[k] = max(x[k] - x[k - 1], 0.0)
        return r

    tot = hourly(a["tot_prec"])
    conv = hourly(a["rain_con"]) + hourly(a["snow_con"])
    snow = hourly(a["snow_gsp"]) + hourly(a["snow_con"])

    # Профил: {ниво hPa: (z m, T °C, RH %)} по стъпки
    pl = icon["airports"][icao]["pl"]
    prof = {}
    for lev in pl.get("t", {}):
        if lev in pl.get("relhum", {}) and lev in pl.get("fi", {}):
            prof[int(lev)] = (_arr(pl["fi"][lev]) / 9.80665,
                              _arr(pl["t"][lev]) - 273.15,
                              _arr(pl["relhum"][lev]))
    return {"t": np.array(t), "u": u, "v": v, "spd": np.hypot(u, v),
            "gust": _arr(a["vmax_10m"]) * KT,
            "t2m": _arr(a["t_2m"]) - 273.15, "td2m": _arr(a["td_2m"]) - 273.15,
            "precip": tot, "precip_conv": conv, "precip_snow": snow,
            "ww": _arr(a["ww"]), "profile": prof,
            "vis": _arr(a["vis"]), "ceiling": _median3(_arr(a["ceiling"])),
            "clcl": _arr(a["clcl"]), "hbas_con": _arr(a["hbas_con"]),
            "htop_con": _arr(a["htop_con"]), "cape": _arr(a["cape_ml"]),
            "hsurf": icon["airports"][icao]["hsurf"],
            "nb": _nb_icon(icon["airports"][icao].get("nb"))}


def _nb_icon(nb):
    """Околността на ICON: {"hsurf", "fr_land" (по клетки), "vis", "ceiling" (време × клетки)}."""
    if not nb or not nb.get("vis"):
        return None
    def mat(x):
        return np.array([[np.nan if v is None else v for v in (row or [])] for row in x], dtype=float)
    return {"hsurf": _arr(nb["hsurf"]), "fr_land": _arr(nb["fr_land"]),
            "vis": mat(nb["vis"]), "ceiling": mat(nb.get("ceiling") or [])}


def from_openmeteo(om: dict, icao: str) -> dict:
    a = om["airports"][icao]
    t = [dt.datetime.fromisoformat(s).timestamp() for s in om["times"]]
    spd, d = _arr(a.get("wind_speed_10m", [])), _arr(a.get("wind_direction_10m", []))
    u = -spd * np.sin(np.radians(d))
    v = -spd * np.cos(np.radians(d))
    n = len(t)
    def g(k):
        x = a.get(k)
        return _arr(x) if x is not None else np.full(n, np.nan)
    prof = {}
    for p in (1000, 950, 925, 900, 850, 800, 700, 600):
        ks = (f"geopotential_height_{p}hPa", f"temperature_{p}hPa", f"relative_humidity_{p}hPa")
        if all(a.get(k) is not None for k in ks):
            prof[p] = tuple(_arr(a[k]) for k in ks)
    return {"t": np.array(t), "u": u, "v": v, "spd": spd, "gust": g("wind_gusts_10m"),
            "t2m": g("temperature_2m"), "td2m": g("dew_point_2m"),
            "precip": g("precipitation"), "ww": g("weather_code"), "profile": prof,
            "vis": g("visibility"), "clcl": g("cloud_cover_low")}


def profile_at(series: dict, ts: float):
    """Профил към момента ts (линейна интерполация) → [(p, z, T, RH), …]."""
    prof = series.get("profile") or {}
    out = []
    for p, (z, tt, rh) in prof.items():
        tmp = {"t": series["t"], "z": z, "T": tt, "RH": rh}
        vals = [at(tmp, k, ts) for k in ("z", "T", "RH")]
        if all(np.isfinite(v) for v in vals):
            out.append((p, *vals))
    return out


def at(series: dict, key: str, ts: float) -> float:
    """Линейна интерполация във времето; NaN извън обхвата."""
    t, y = series["t"], series[key]
    ok = np.isfinite(y)
    if ok.sum() < 2 or ts < t[ok][0] or ts > t[ok][-1]:
        return float("nan")
    return float(np.interp(ts, t[ok], y[ok]))


def gust_at(series: dict, ts: float) -> float:
    """Поривът е максимум за изминалия час → взима се по-голямото от двата
    съседни часа, а не интерполация (не бива да го размиваме)."""
    t, y = series["t"], series["gust"]
    ok = np.isfinite(y)
    if not ok.any() or ts < t[ok][0] - 3600 or ts > t[ok][-1]:
        return float("nan")
    near = ok & (t >= ts) & (t <= ts + 3600)
    if not near.any():
        near = ok & (np.abs(t - ts) <= 3600)
    return float(np.nanmax(y[near])) if near.any() else float("nan")


def weighted(vals: dict, weights: dict) -> float:
    """Теглово средно; моделите с NaN отпадат и теглата се пренормират."""
    num = den = 0.0
    for k, w in weights.items():
        x = vals.get(k)
        if w > 0 and x is not None and np.isfinite(x):
            num += w * x
            den += w
    return num / den if den > 0 else float("nan")
