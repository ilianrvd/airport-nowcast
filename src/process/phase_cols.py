"""
Фаза и P(обледяващ валеж) по колони
===================================
За всяка колона и за всеки модел с профил (ICON, GFS):
  1. профилът към времето на колоната;
  2. корекция на T от METAR: ΔT = коригираната T (от wind_temp) − T2m на
     модела, изцяло на земята и линейно до 0 на 300 m над летището;
  3. Tw на земята от коригираните T/Td; интеграл и фаза (phase.classify).
Комбиниране: ICON е основен. Ако ICON и GFS дават сняг и дъжд (SN срещу
RA/FZRA) → RASN. FZRA, ако който и да е от двата даде FZRA при коригирана
T2m ≤ 0.

P(обледяващ) — само при валеж в колоната; начални тегла от спецификацията,
после max по ±1 ч (несигурност в момента на прехода):
  ICON дава FZRA → 50; GFS също → +20; IFS T2m ≤ 0 → +10;
  коригирана T2m в −0.5…+0.5 → в рамките 30–50; топъл слой слаб (Tw max < 2) → −20;
  METAR с FZ валеж → 90 в началото, после избледнява.
Показва се като степен: НИС < 30 ≤ УМЕР < 60 ≤ ВИС.
"""

import math

import numpy as np

from .series import at, profile_at
from .phase import wet_bulb_from_td, wb_integral, classify, corrected_levels, p_surface

DEPTH_M = 300.0


def _model_phase(series, ts, z_sfc, t_corr, td_corr):
    levels = profile_at(series, ts)
    t_mod = at(series, "t2m", ts)
    if not levels or not np.isfinite(t_mod) or t_corr is None or td_corr is None:
        return None
    dT = t_corr - t_mod
    lev = corrected_levels(levels, z_sfc, dT, DEPTH_M)
    tw_sfc = wet_bulb_from_td(t_corr, min(td_corr, t_corr), p_surface(z_sfc))
    I, warm = wb_integral(lev, z_sfc, tw_sfc)
    return {"I": I, "warm": warm, "phase": classify(I, t_corr)}


def _combine(pi, pg):
    if pi is None and pg is None:
        return None
    if pi is None:
        return pg["phase"]
    if pg is None:
        return pi["phase"]
    a, b = pi["phase"], pg["phase"]
    if "FZRA" in (a, b):
        return "FZRA"
    if {a, b} == {"SN", "RA"}:
        return "RASN"
    return a


def _p_icing(pi, pg, t_ifs, t_corr, metar_fz, h):
    if pi is None and pg is None:
        return 0.0
    p = 0.0
    if pi and pi["phase"] == "FZRA":
        p = 50.0
        if pg and pg["phase"] == "FZRA":
            p += 20.0
    elif pg and pg["phase"] == "FZRA":
        p = 30.0
    if p > 0 and t_ifs is not None and np.isfinite(t_ifs) and t_ifs <= 0:
        p += 10.0
    if p > 0 and t_corr is not None and -0.5 <= t_corr <= 0.5:
        p = min(max(p, 30.0), 50.0)
    warm = max([x["warm"] for x in (pi, pg) if x and np.isfinite(x["warm"])], default=float("nan"))
    if p > 0 and np.isfinite(warm) and warm < 2.0:
        p -= 20.0
    if metar_fz:
        p = max(p, 90.0 * math.exp(-h / 2.0))
    return max(0.0, min(p, 100.0))


def _degree(p):
    if p is None:
        return None
    return "ВИС" if p >= 60 else ("УМЕР" if p >= 30 else "НИС")


def compute(models, cols, metars, z_sfc):
    """cols: изход от wind_temp (с t_raw, td_raw, time, wx). Добавя към всяка
    колона "phase", "I_icon", "I_gfs", "p_icing" (число) и "icing" (степен)."""
    import datetime as dt
    m0 = metars[0] if metars else None
    metar_fz = bool(m0 and any("FZ" in g and ("RA" in g or "DZ" in g) for g in m0.get("wx", [])))
    t0 = dt.datetime.fromisoformat(cols[0]["time"]).timestamp()

    raw = []
    for c in cols:
        ts = dt.datetime.fromisoformat(c["time"]).timestamp()
        pi = _model_phase(models["icon"], ts, z_sfc, c["t_raw"], c["td_raw"]) if "icon" in models else None
        pg = _model_phase(models["gfs"], ts, z_sfc, c["t_raw"], c["td_raw"]) if "gfs" in models else None
        t_ifs = at(models["ifs"], "t2m", ts) if "ifs" in models else None
        h = (ts - t0) / 3600
        raw.append((ts, pi, pg, _p_icing(pi, pg, t_ifs, c["t_raw"], metar_fz, h)))

    for k, c in enumerate(cols):
        ts, pi, pg, p = raw[k]
        has_precip = bool(c.get("wx")) and any(x in c["wx"] for x in ("RA", "SN", "DZ"))
        # Фазата се смята винаги (за проверка), но се показва само при валеж
        c["phase_model"] = _combine(pi, pg)
        c["phase"] = c["phase_model"] if has_precip else None
        c["I_icon"] = None if pi is None else round(pi["I"])
        c["I_gfs"] = None if pg is None else round(pg["I"])
        # max по ±1 ч около колоната
        p_win = max(r[3] for r in raw if abs(r[0] - ts) <= 3600)
        c["p_icing"] = round(p_win) if has_precip else None
        # „—“ при нулева вероятност (топъл валеж) — НИС само когато има реален сигнал
        c["icing"] = _degree(c["p_icing"]) if (has_precip and p_win > 0) else None
    return cols


def apply_phase_to_wx(wx, phase):
    """Заменя RA в групата на модела/nowcast-а с фазата: -SHRA → -SHSN и т.н.
    Групи от METAR не се пипат (подават се с src M)."""
    if not wx or not phase or phase == "RA":
        return wx
    out = []
    for g in wx.split():
        if "RA" in g and "VC" not in g:
            inten = g[0] if g[0] in "+-" else ""
            body = g[1:] if inten else g
            desc = "TS" if body.startswith("TS") else ("SH" if body.startswith("SH") else "")
            if phase == "SN":
                g = f"{inten}{desc}SN"
            elif phase == "RASN":
                g = f"{inten}{desc}RASN"
            elif phase == "FZRA":
                g = f"{inten}FZRA"
        out.append(g)
    return " ".join(out)
