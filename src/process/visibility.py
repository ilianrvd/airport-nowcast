"""
Видимост и вероятност за видимост < 2000 / < 1000 m
====================================================
СТОЙНОСТ (точката на летището):
  1. лог-средно на ICON и IFS (GFS не участва), до 10 km;
  2. корекция от METAR в лог-скала, избледнява с τ = 3 ч;
  3. валеж → min(стойност, видимост при валеж по тип и интензивност);
  4. модел за мъгла — само В СВОЯТА ОБЛАСТ (виж по-долу): изместване +2 ч,
     рампа ±1 ч, тегло до 0.9 (0.6 за LBWN/LBBG), METAR ускорява/забавя рампата;
     часовете с DYNAMIC не участват. Ако METAR вече е FG, води METAR.

ВЕРОЯТНОСТ = максимум от явни източници (без сумиране, без изглаждане накрая,
стойността в таблицата НЕ влияе обратно). Източникът на максимума → p_src.
  "околност"  дял на точките 5×5 (ICON + IFS, ~15 km) под прага; само суша
              (fr_land ≥ 0.5) с височина ±150 m от летището. За ICON клетка
              с облак до земята (ceiling < 60 m) се брои като под прага.
  "METAR"     100, ако METAR е под прага, избледнява с τ = 3 ч.
  "валеж"     70, ако видимостта при валеж в колоната е под прага.
  "мъгла"     модел за мъгла в своята област, сигнал в прозорец ±1 ч (изместен):
              50 (< 2000) / 35 (< 1000); × 0.6 при cf_collapse; × 0.8 при borderline.
  "река"      LBGO с флаг river_regime, без валеж: 25.
  Монотонност: P(< 1000) ≤ P(< 2000).

ОБЛАСТ НА МОДЕЛА ЗА МЪГЛА: няма валеж (флаг precip и METAR), METAR няма
BKN/OVC под 5000 ft (нисък стратус — насищането не е от изстиване).
"""

import math
import datetime as dt

import numpy as np

from .series import at, weighted


def _ln(x):
    return math.log(max(float(x), 50.0))


def _base_vis(models, ts, vmax):
    vals = {}
    for n in ("icon", "ifs"):
        if n in models:
            v = at(models[n], "vis", ts)
            if np.isfinite(v):
                vals[n] = _ln(min(v, vmax))
    if not vals:
        return float("nan")
    return math.exp(weighted(vals, {"icon": 0.5, "ifs": 0.5}))


def _precip_vis(wx, cfg):
    if not wx:
        return None
    best = None
    for g in wx.split():
        if "VC" in g:
            continue
        inten = 0 if g.startswith("-") else (2 if g.startswith("+") else 1)
        for typ in ("FZRA", "RASN", "SN", "DZ", "RA"):
            if typ in g:
                v = cfg["precip_vis"][typ][inten]
                best = v if best is None else min(best, v)
                break
    return best


def _fog_lookup(fog_ap, ts, shift_h):
    if not fog_ap or not fog_ap.get("hours"):
        return None
    tq = ts - shift_h * 3600
    best = None
    for h in fog_ap["hours"]:
        th = dt.datetime.fromisoformat(h["valid"].replace("Z", "+00:00")).timestamp()
        if abs(th - tq) <= 1800:
            best = h
    return best


def _onset_ts(fog_ap, shift_h):
    on = (fog_ap or {}).get("summary", {}).get("onset")
    if not on:
        return None
    return dt.datetime.fromisoformat(on.replace("Z", "+00:00")).timestamp() + shift_h * 3600


# ---------- околност ----------
def _mask(hsurf, fr_land, elev, cfg):
    return (np.isfinite(hsurf) & (np.abs(hsurf - elev) <= cfg["nb_elev_m"])
            & np.isfinite(fr_land) & (fr_land >= 0.5))


def _row_at(t_axis, mat, ts):
    """Ред (по клетки) от матрица време × клетки, линейно във времето."""
    if mat is None or mat.size == 0:
        return None
    if ts < t_axis[0] or ts > t_axis[-1]:
        return None
    k = int(np.searchsorted(t_axis, ts))
    if k == 0:
        return mat[0]
    a = (ts - t_axis[k - 1]) / (t_axis[k] - t_axis[k - 1])
    return (1 - a) * mat[k - 1] + a * mat[k]


def _nb_fraction(ic_series, ifs_nb, mask, ts, thr, cfg):
    """Дял (0–1) на точките под прага: ICON и IFS с равни тегла; NaN, ако няма данни."""
    fr = []
    if ic_series is not None and ic_series.get("nb") is not None:
        nb = ic_series["nb"]
        v = _row_at(ic_series["t"], nb["vis"], ts)
        c = _row_at(ic_series["t"], nb["ceiling"], ts) if nb["ceiling"].size else None
        if v is not None:
            ok = mask & np.isfinite(v)
            if ok.any():
                low = v < thr
                if c is not None:
                    low = low | (np.isfinite(c) & (c < cfg["ceiling_fog_m"]))
                fr.append(float(low[ok].mean()))
    if ifs_nb is not None:
        v = _row_at(ifs_nb["t"], ifs_nb["vis"], ts)
        if v is not None:
            ok = mask & np.isfinite(v)
            if ok.any():
                fr.append(float((v[ok] < thr).mean()))
    return float(np.mean(fr)) if fr else float("nan")


def ifs_nb_series(inp_ifs_nb, icao):
    if not inp_ifs_nb or icao not in inp_ifs_nb.get("airports", {}):
        return None
    a = inp_ifs_nb["airports"][icao]
    t = np.array([dt.datetime.fromisoformat(s).timestamp() for s in inp_ifs_nb["times"]])
    vis = np.array([[np.nan if v is None else v for v in row] for row in a["visibility"]], dtype=float)
    return {"t": t, "vis": vis}


def compute(icao, cols, metars, models, fog_ap, fog_ok, settings, ifs_nb=None):
    cfg, P = settings.VIS, settings.FOG_PROB
    vmax = cfg["max_m"]
    elev = settings.AIRPORTS[icao]["elev_m"]
    t0 = dt.datetime.fromisoformat(cols[0]["time"]).timestamp()
    m0 = metars[0] if metars else None
    m1 = metars[1] if metars and len(metars) > 1 else None
    flags = (fog_ap or {}).get("flags", {}) or {}
    shift, spread = cfg["fog_shift_h"], cfg["fog_spread_h"]

    # --- област на модела за мъгла
    metar_precip = bool(m0 and any(x in g for g in m0.get("wx", [])
                                   for x in ("RA", "SN", "DZ", "PL", "GR", "GS")))
    metar_low = bool(m0 and any(c["cover"] in ("BKN", "OVC") and c.get("base_ft") is not None
                                and c["base_ft"] < 5000 for c in m0.get("clouds", [])))
    in_domain = bool(fog_ok and fog_ap and fog_ap.get("hours")
                     and not flags.get("precip") and not metar_precip and not metar_low)
    fog_note = None
    if fog_ok and fog_ap and not in_domain:
        why = [w for w, c in (("валеж в ICON", flags.get("precip")), ("валеж в METAR", metar_precip),
                              ("нисък таван в METAR", metar_low)) if c]
        fog_note = "моделът за мъгла е извън областта си: " + ", ".join(why)

    w_max = cfg["fog_w_max_coast"] if icao in cfg["coastal"] else cfg["fog_w_max"]
    if flags.get("cf_collapse"):          # ICON сваля облачността → честа фалшива мъгла
        w_max *= P["cf_collapse_factor"]
    onset = _onset_ts(fog_ap, shift) if in_domain else None

    # --- METAR
    v_m = m0.get("vis_m") if m0 else None
    base0 = _base_vis(models, t0, vmax)
    E = (_ln(min(v_m, vmax)) - _ln(base0)) if (v_m and np.isfinite(base0)) else 0.0
    dd = (m0["temp_c"] - m0["dew_c"]) if (m0 and m0.get("temp_c") is not None
                                          and m0.get("dew_c") is not None) else None
    falling = bool(m0 and m1 and m0.get("vis_m") and m1.get("vis_m") and m0["vis_m"] < m1["vis_m"])
    metar_fg = bool(v_m is not None and v_m < 1000)

    # --- маска на околността (обща за ICON и IFS — същите точки)
    ic = models.get("icon")
    mask = None
    if ic is not None and ic.get("nb") is not None:
        mask = _mask(ic["nb"]["hsurf"], ic["nb"]["fr_land"], elev, cfg)
    ifs_s = ifs_nb_series(ifs_nb, icao) if mask is not None else None

    for k, c in enumerate(cols):
        ts = dt.datetime.fromisoformat(c["time"]).timestamp()
        h = (ts - t0) / 3600

        # ===== СТОЙНОСТ =====
        pv = _precip_vis(c.get("wx") if c.get("wx_src") != "M" else None, cfg)
        if k == 0 and v_m is not None:
            c["vis_m"], c["vis_src"], c["fog_w"] = int(v_m), "M", 0.0
        else:
            base = _base_vis(models, ts, vmax)
            if not np.isfinite(base):
                c["vis_m"], c["vis_src"], c["fog_w"] = None, None, 0.0
            else:
                v = math.exp(_ln(base) + E * math.exp(-h / cfg["tau_h"]))
                src = "К" if abs(E * math.exp(-h / cfg["tau_h"])) > 0.05 else "N"
                w = 0.0
                fr = _fog_lookup(fog_ap, ts, shift) if in_domain else None
                if fr and fr.get("vis_m") is not None and onset is not None and not metar_fg \
                        and fr.get("regime") != "DYNAMIC":
                    ramp = (ts - (onset - spread * 3600)) / (2 * spread * 3600)
                    w = w_max * min(max(ramp, 0.0), 1.0)
                    if dd is not None and h <= 3:
                        if dd <= cfg["metar_dd_fast"] and falling and onset - ts <= 2 * 3600:
                            w = min(w + 0.3, w_max)
                        elif dd >= cfg["metar_dd_slow"]:
                            w *= 0.5
                    if w > 0 and fr["vis_m"] < v:
                        v = math.exp((1 - w) * _ln(v) + w * _ln(min(fr["vis_m"], vmax)))
                        src = "Ф"
                    else:
                        w = 0.0
                if pv is not None and pv < v:
                    v, src = pv, ("Н" if c.get("wx_src") == "Н" else "N")
                v = min(v, vmax)
                c["vis_m"] = int(round(v / 100.0) * 100) if v < 5000 else int(round(v / 1000.0) * 1000)
                c["vis_src"], c["fog_w"] = src, round(w, 2)

        # ===== ВЕРОЯТНОСТ =====
        res = {}
        for thr, key in ((2000, "2000"), (1000, "1000")):
            cand = {}
            if v_m is not None:
                cand["METAR"] = (100.0 * math.exp(-h / cfg["tau_h"])) if v_m < thr else 0.0
            if mask is not None:
                f = _nb_fraction(ic, ifs_s, mask, ts, thr, cfg)
                if np.isfinite(f):
                    cand["околност"] = 100.0 * f
            if pv is not None and pv < thr:
                cand["валеж"] = P["precip"]
            if in_domain:
                sig = False
                for d in (-spread, 0.0, spread):
                    fr = _fog_lookup(fog_ap, ts + d * 3600, shift)
                    if fr and fr.get("vis_m") is not None and fr["vis_m"] < thr and fr.get("regime") != "DYNAMIC":
                        sig = True
                if sig:
                    p = P["fog_2000"] if thr == 2000 else P["fog_1000"]
                    if flags.get("cf_collapse"):
                        p *= P["cf_collapse_factor"]
                    if flags.get("borderline") and thr == 1000:
                        p *= P["borderline_factor"]
                    cand["мъгла"] = p
            if flags.get("river_regime") and not metar_precip and not flags.get("precip"):
                cand["река"] = P["river"]
            if cand:
                src = max(cand, key=cand.get)
                res[key] = (cand[src], src if cand[src] >= 1 else None)
            else:
                res[key] = (None, None)

        p2, s2 = res["2000"]
        p1, s1 = res["1000"]
        if p1 is not None and p2 is not None and p1 > p2:
            p2, s2 = p1, s1
        c["p_lt2000"] = None if p2 is None else round(p2)
        c["p_lt1000"] = None if p1 is None else round(p1)
        c["p_src2000"], c["p_src1000"] = s2, s1
        c["fog2000"] = _deg(p2, P)
        c["fog1000"] = _deg(p1, P)

    _consistency(cols)
    return {"fog_in_domain": in_domain, "fog_note": fog_note,
            "nb_points": int(mask.sum()) if mask is not None else 0}


def _deg(p, P):
    if p is None:
        return None
    return "ВИС" if p >= P["degree"]["high"] else ("УМЕР" if p >= P["degree"]["mid"] else "НИС")


def _consistency(cols):
    """FG при < 1000 m, BR при 1000–5000 m и T−Td ≤ 2, без валеж от METAR."""
    for k, c in enumerate(cols):
        if k == 0 or c.get("wx_src") == "M" or c.get("vis_m") is None:
            continue
        wx = c.get("wx") or ""
        has_precip = any(x in wx for x in ("RA", "SN", "DZ"))
        dd = (c["t_raw"] - c["td_raw"]) if c.get("t_raw") is not None and c.get("td_raw") is not None else None
        add = None
        if c["vis_m"] < 1000 and not has_precip:
            add = "FG" if (c.get("t_raw") is None or c["t_raw"] > 0) else "FZFG"
        elif c["vis_m"] < 5000 and dd is not None and dd <= 2:
            add = "BR"
        if add and add not in wx:
            c["wx"] = (wx + " " + add).strip()
