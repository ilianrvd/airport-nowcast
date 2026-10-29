"""
Вятър, пориви и T/Td за едно летище
===================================
X(t) = средно от моделите + B_stat + E_METAR · exp(−t/τ)

- Средно: посоката от векторното средно (u/v), скоростта от тегловото средно
  на скоростите (векторното средно занижава скоростта, когато моделите се
  разминават по посока). Тегла от settings.WEIGHTS, пренормирани при липса.
- E_METAR: грешката METAR − модел от последните до 3 METAR-а (≤ 2 ч), с тегла
  0.8 / 0.15 / 0.05. SPECI с рязка промяна → само последното наблюдение.
  Скорост и посока поотделно; посоката, когато METAR е ≥ 3 kt.
- Нулиране: ако и ICON, и IFS (наличните от двата) показват смяна на режима —
  посока > 60° при ≥ 6 kt или скорост > 10 kt за 2 ч — корекцията е 0 оттам нататък.
- Порив = коригиран среден вятър + превишение. Превишението тръгва от METAR
  (G − S; без G — под 10 kt) и с τ_gust преминава към превишението на модела
  (претеглено средно на поривите от моделите − средният вятър). Показва се, когато е
  ≥ 10 kt над средния вятър (както в METAR).
- T/Td: същата корекция; Td ≤ T.
"""

import math
import datetime as dt

import numpy as np

from .series import timeline, lead_label, uv_to_dirspd, at, gust_at, weighted


def _wrap(d):
    return (d + 180) % 360 - 180


def _model_wind(models, w, ts):
    us = {k: at(s, "u", ts) for k, s in models.items()}
    vs = {k: at(s, "v", ts) for k, s in models.items()}
    ss = {k: at(s, "spd", ts) for k, s in models.items()}
    u, v, spd = weighted(us, w), weighted(vs, w), weighted(ss, w)
    if not (np.isfinite(u) and np.isfinite(v)):
        return float("nan"), float("nan")
    d, _ = uv_to_dirspd(u, v)
    return d, spd


def _speci_jump(m0, m1, c):
    """Рязка промяна между последния SPECI и предишния METAR."""
    if m0.get("type") != "SPECI" or m1 is None:
        return False
    if (m0.get("wind_kt") is not None and m1.get("wind_kt") is not None
            and abs(m0["wind_kt"] - m1["wind_kt"]) > c["speci_jump_kt"]):
        return True
    if (isinstance(m0.get("wind_dir"), int) and isinstance(m1.get("wind_dir"), int)
            and abs(_wrap(m0["wind_dir"] - m1["wind_dir"])) > c["speci_jump_dir"]):
        return True
    if (m0.get("temp_c") is not None and m1.get("temp_c") is not None
            and abs(m0["temp_c"] - m1["temp_c"]) >= c["speci_jump_t"]):
        return True
    return False


def _errors(metars, models, W, c):
    """Претеглени грешки METAR − модел: ΔS, Δθ, ΔT, ΔTd (NaN, ако няма данни)."""
    t_last = metars[0]["time"]
    use = [m for m in metars[:len(c["metar_weights"])]
           if (t_last - m["time"]).total_seconds() <= c["max_metar_age_h"] * 3600]
    if len(use) > 1 and _speci_jump(use[0], use[1], c):
        use = use[:1]
    acc = {"s": [], "d": [], "t": [], "td": []}
    for m, w in zip(use, c["metar_weights"]):
        ts = m["time"].timestamp()
        d_mod, s_mod = _model_wind(models, W["wind"], ts)
        if m.get("wind_kt") is not None and np.isfinite(s_mod):
            acc["s"].append((w, m["wind_kt"] - s_mod))
            # Посоката: при METAR ≥ 3 kt, независимо от модела (при слаб вятър
            # в модела посоката му е шум и METAR носи информацията)
            if (isinstance(m.get("wind_dir"), int) and m["wind_kt"] >= 3
                    and np.isfinite(d_mod)):
                acc["d"].append((w, _wrap(m["wind_dir"] - d_mod)))
        t_mod = weighted({k: at(s, "t2m", ts) for k, s in models.items()}, W["temp"])
        td_mod = weighted({k: at(s, "td2m", ts) for k, s in models.items()}, W["temp"])
        if m.get("temp_c") is not None and np.isfinite(t_mod):
            acc["t"].append((w, m["temp_c"] - t_mod))
        if m.get("dew_c") is not None and np.isfinite(td_mod):
            acc["td"].append((w, m["dew_c"] - td_mod))

    def wavg(lst):
        sw = sum(w for w, _ in lst)
        return sum(w * x for w, x in lst) / sw if sw else float("nan")

    def wavg_angle(lst):
        sw = sum(w for w, _ in lst)
        if not sw:
            return float("nan")
        sx = sum(w * math.sin(math.radians(x)) for w, x in lst)
        cx = sum(w * math.cos(math.radians(x)) for w, x in lst)
        return math.degrees(math.atan2(sx, cx))

    return {"ds": wavg(acc["s"]), "dd": wavg_angle(acc["d"]),
            "dt": wavg(acc["t"]), "dtd": wavg(acc["td"]), "n_metar": len(use)}


def _regime_reset(models, t0, cols, c):
    """Първото време, в което всички налични от ICON/IFS показват смяна на режима."""
    keys = [k for k in ("icon", "ifs") if k in models]
    if not keys:
        return None
    win = c["reset_window_h"] * 3600
    for ts_dt in cols[1:]:
        ts = ts_dt.timestamp()
        flags = []
        for k in keys:
            s = models[k]
            u1, v1, u0, v0 = at(s, "u", ts), at(s, "v", ts), at(s, "u", ts - win), at(s, "v", ts - win)
            if not all(np.isfinite(x) for x in (u1, v1, u0, v0)):
                flags.append(False)
                continue
            d1, s1 = uv_to_dirspd(u1, v1)
            d0, s0 = uv_to_dirspd(u0, v0)
            jump_s = abs(s1 - s0) > c["reset_kt"]
            jump_d = (min(s0, s1) >= c["reset_dir_min_kt"]
                      and abs(_wrap(d1 - d0)) > c["reset_dir"])
            flags.append(jump_s or jump_d)
        if all(flags):
            return ts_dt
    return None


def compute(icao, metars, models, now, settings):
    """metars: списък (най-новият първи) с parsed METAR и "time" (datetime).
    models: {"icon"|"ifs"|"gfs": series}. Връща dict за таблицата."""
    W, c, wcfg = settings.WEIGHTS, settings.CORRECTION, settings.WIND
    tau = c["tau_h"]
    stale = (not metars or metars[0].get("time") is None
             or (now - metars[0]["time"]).total_seconds() > settings.METAR["stale_min"] * 60)

    if stale:
        t0 = now.replace(minute=(now.minute // 30) * 30, second=0, microsecond=0)
        err = {"ds": float("nan"), "dd": float("nan"), "dt": float("nan"),
               "dtd": float("nan"), "n_metar": 0}
        m0 = None
    else:
        m0 = metars[0]
        t0 = m0["time"]
        err = _errors(metars, models, W, c)

    cols = timeline(t0, settings.TIMELINE)
    reset_at = _regime_reset(models, t0, cols, c) if not stale else None
    e_metar = (m0["gust_kt"] - m0["wind_kt"]) if (m0 and m0.get("gust_kt") and m0.get("wind_kt") is not None) else None
    # Превишение порив − вятър на модела в t0 (за METAR без G)
    _g0 = weighted({n: gust_at(s, t0.timestamp()) for n, s in models.items()}, W["wind"])
    _s0 = _model_wind(models, W["wind"], t0.timestamp())[1]
    e_mod0 = max(_g0 - _s0, 0.0) if (np.isfinite(_g0) and np.isfinite(_s0)) else 0.0
    if stale:
        e_metar, e_mod0 = None, 0.0

    out = []
    for k, t in enumerate(cols):
        ts = t.timestamp()
        h = (ts - t0.timestamp()) / 3600
        alive = reset_at is None or t < reset_at
        f_w = math.exp(-h / tau["wind"]) if alive else 0.0
        f_t = math.exp(-h / tau["temp"]) if alive else 0.0
        f_g = math.exp(-h / tau["gust"]) if alive else 0.0

        d_mod, s_mod = _model_wind(models, W["wind"], ts)
        t_mod = weighted({n: at(s, "t2m", ts) for n, s in models.items()}, W["temp"])
        td_mod = weighted({n: at(s, "td2m", ts) for n, s in models.items()}, W["temp"])
        g_mod = weighted({n: gust_at(s, ts) for n, s in models.items()}, W["wind"])

        if k == 0 and m0 is not None:
            # Колоната „Сега“ е самото наблюдение
            spd = m0.get("wind_kt")
            dirn = m0.get("wind_dir")
            gust = m0.get("gust_kt")
            tt, td = m0.get("temp_c"), m0.get("dew_c")
            src = "M"
        else:
            spd = s_mod + (err["ds"] * f_w if np.isfinite(err["ds"]) else 0.0)
            spd = max(spd, 0.0) if np.isfinite(spd) else None
            dirn = ((d_mod + (err["dd"] * f_w if np.isfinite(err["dd"]) else 0.0)) % 360
                    if np.isfinite(d_mod) else None)
            gust = None
            if np.isfinite(g_mod) and np.isfinite(s_mod) and spd is not None:
                # Поривът = коригиран вятър + превишение. Превишението тръгва от
                # METAR и избледнява към това на модела: METAR с G дава G − S;
                # METAR без G казва, че превишението е под 10 kt.
                e_mod = max(g_mod - s_mod, 0.0)
                e_obs = e_metar if e_metar is not None else min(e_mod0, wcfg["gust_show_diff_kt"] - 1)
                gust = spd + e_obs * f_g + e_mod * (1.0 - f_g)
            tt = t_mod + (err["dt"] * f_t if np.isfinite(err["dt"]) else 0.0)
            td = td_mod + (err["dtd"] * f_t if np.isfinite(err["dtd"]) else 0.0)
            tt = tt if np.isfinite(tt) else None
            td = min(td, tt) if (np.isfinite(td) and tt is not None) else (td if np.isfinite(td) else None)
            src = "К" if (not stale and f_w > 0.05) else "N"

        spd_r = None if spd is None else int(round(spd))
        if spd_r is not None and (spd_r < wcfg["vrb_below_kt"] or dirn == "VRB"):
            dir_txt = "VRB"
        elif isinstance(dirn, (int, float)):
            dir_txt = f"{int(round(dirn / 10.0) * 10) % 360 or 360:03d}"
        else:
            dir_txt = None
        gust_r = None
        if gust is not None and spd_r is not None and gust - spd_r >= wcfg["gust_show_diff_kt"]:
            gust_r = int(round(gust))
        excess = (gust - spd_r) if (gust is not None and spd_r is not None) else None

        out.append({
            "time": t.isoformat(), "lead": lead_label(t0, t),
            "wind_dir": dir_txt, "wind_kt": spd_r, "gust_kt": gust_r,
            "_gust": None if gust is None else float(gust), "_excess": excess,
            "t": None if tt is None else round(tt), "td": None if td is None else round(td),
            "t_raw": None if tt is None else round(float(tt), 2),
            "td_raw": None if td is None else round(float(td), 2),
            "src": src,
        })

    # Хистерезис на поривите: ≥ 8 kt превишение се показва, ако и двата
    # съседа имат порив (иначе редът мига около прага от 10 kt)
    lo = wcfg["gust_show_diff_kt"] - 2
    for k in range(1, len(out) - 1):
        c, a, b = out[k], out[k - 1], out[k + 1]
        if (c["gust_kt"] is None and c["_excess"] is not None and c["_excess"] >= lo
                and a["gust_kt"] is not None and b["gust_kt"] is not None):
            c["gust_kt"] = int(round(c["_gust"]))
    for c in out:
        c.pop("_gust", None)
        c.pop("_excess", None)

    return {
        "icao": icao, "t0": t0.isoformat(), "stale_metar": stale,
        "metar_raw": m0["raw"] if m0 else None,
        "models": sorted(models), "reset_at": reset_at.isoformat() if reset_at else None,
        "correction": {k: (None if isinstance(v, float) and not np.isfinite(v) else
                           (round(v, 1) if isinstance(v, float) else v)) for k, v in err.items()},
        "cols": out,
    }
