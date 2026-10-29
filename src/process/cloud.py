"""
Облачност: таван, ниска облачност, P(таван < 1500 / 200 ft), CB/TCU
===================================================================
Таван (ft над летището):
  - Колона „Сега“ = METAR (най-ниският BKN/OVC или VV).
  - Иначе ICON `ceiling` (над терена — settings.CEILING_REF), коригиран от
    METAR в лог-скала: E = ln C_METAR − ln C_ICON в t0, избледнява с τ = 3 ч
    (6 ч, ако ICON сваля ниската облачност, а METAR е BKN/OVC под 5000 ft).
    И двете се ограничават до 6000 ft: „няма нисък таван“ се корекцира като 6000.
  - ≥ 5000 ft → „висока“. В мъгла (FG в явлението) → VV.
Ниска облачност: средно clcl на ICON/IFS/GFS (0.4 / 0.4 / 0.2) + корекция от
  METAR (най-голямото количество под 5000 ft), избледнява с τ. → FEW…OVC.
P(таван < 1500 ft): по коригирания таван и суровия ICON; P(< 200 ft): свързан
  с мъглата (P видимост < 1000) и тавана; монотонност; ±1 ч изглаждане.
CB/TCU: 0–6 ч от nowcast-а (конвективно ехо + мълнии в 16 km → CB, само ехо → TCU);
  после от ICON (основа < 5000 ft, връх > 6 km и CAPE > 500 → CB; връх 3–6 km → TCU).
"""

import math
import datetime as dt

import numpy as np

from .series import at, weighted

FT = 3.28084


def _metar_low_cover(m, high_ft, pct):
    if not m:
        return None
    if m.get("cavok") or m.get("nsc"):
        return 0.0
    if m.get("vv_ft") is not None:
        return 100.0
    vals = [pct[c["cover"]] for c in m.get("clouds", [])
            if c.get("base_ft") is not None and c["base_ft"] < high_ft]
    return float(max(vals)) if vals else 0.0


def _icon_ceiling_ft(ic, ts, ref):
    c = at(ic, "ceiling", ts)
    if not np.isfinite(c):
        return float("nan")
    if ref == "msl":
        c -= ic.get("hsurf", 0.0)
    return max(c, 0.0) * FT


def _amount(pct, bins):
    if pct is None or not np.isfinite(pct):
        return None
    for lim, name in bins:
        if pct < lim:
            return name
    return "OVC"


def _deg(p):
    return "ВИС" if p >= 60 else ("УМЕР" if p >= 30 else "НИС")


def compute(cols, metars, models, nowcast_ap, settings):
    C = settings.CLOUD
    cap, high = C["cap_ft"], C["high_ft"]
    ic = models.get("icon")
    m0 = metars[0] if metars else None
    t0 = dt.datetime.fromisoformat(cols[0]["time"]).timestamp()
    W = settings.WEIGHTS["wind"]

    # --- корекция на тавана
    cm = m0.get("ceiling_ft") if m0 else None
    if m0 and (m0.get("cavok") or m0.get("nsc")):
        cm = cap
    ci0 = _icon_ceiling_ft(ic, t0, settings.CEILING_REF) if ic else float("nan")
    E = 0.0
    if m0 is not None and np.isfinite(ci0):
        cm_eff = min(cm if cm is not None else cap, cap)
        E = math.log(max(cm_eff, 50)) - math.log(max(min(ci0, cap), 50))
    # ICON сваля ниската облачност, а METAR е BKN/OVC под 5000 ft?
    tau = C["tau_h"]
    if ic is not None and cm is not None and cm < high:
        l0, l2 = at(ic, "clcl", t0), at(ic, "clcl", t0 + 7200)
        if np.isfinite(l0) and np.isfinite(l2) and (l0 - l2) / 100.0 > C["collapse_drop"]:
            tau = C["tau_h_collapse"]

    # --- корекция на ниската облачност
    cov_m = _metar_low_cover(m0, high, C["cover_pct"])
    def clcl_mean(ts):
        return weighted({n: at(s, "clcl", ts) for n, s in models.items() if "clcl" in s}, W)
    l_mod0 = clcl_mean(t0)
    E_amt = (cov_m - l_mod0) if (cov_m is not None and np.isfinite(l_mod0)) else 0.0

    # --- nowcast за CB/TCU
    leads = []
    if nowcast_ap:
        for L in nowcast_ap.get("leads", []):
            leads.append((dt.datetime.fromisoformat(L["valid"].replace("Z", "+00:00")).timestamp(), L))
    ltg16 = ((nowcast_ap or {}).get("lightning") or {}).get("r16", 0) or 0
    ltg8 = ((nowcast_ap or {}).get("lightning") or {}).get("r8", 0) or 0

    p15, p02, cig_raw = [], [], []
    for k, c in enumerate(cols):
        ts = dt.datetime.fromisoformat(c["time"]).timestamp()
        h = (ts - t0) / 3600
        f = math.exp(-h / tau)
        fog = bool(c.get("wx")) and "FG" in c["wx"]

        if k == 0 and m0 is not None:
            ceil = cm if (cm is not None and cm < high) else None
            amt = _amount(cov_m, C["pct_bins"]) if cov_m is not None else None
            cb = m0.get("cb_tcu")
            src = "M"
        else:
            ci = _icon_ceiling_ft(ic, ts, settings.CEILING_REF) if ic else float("nan")
            if np.isfinite(ci):
                ceil = math.exp(math.log(max(min(ci, cap), 50)) + E * f)
                ceil = None if ceil >= high else ceil
            else:
                ceil = None
            lm = clcl_mean(ts)
            amt = _amount(min(max(lm + E_amt * f, 0), 100), C["pct_bins"]) if np.isfinite(lm) else None
            src = "К" if abs(E * f) > 0.05 else "N"
            # CB / TCU
            cb = None
            L = None
            if leads:
                tl, Lb = min(leads, key=lambda x: abs(x[0] - ts))
                if abs(tl - ts) <= 900:
                    L = Lb
            if L is not None:
                if L.get("convective"):
                    cb = "CB" if (L["lead_min"] <= 30 and (ltg8 + ltg16) > 0) or (L.get("dbz_max_10km") or 0) >= 45 else "TCU"
            elif ic is not None:
                hb, ht, cape = at(ic, "hbas_con", ts), at(ic, "htop_con", ts), at(ic, "cape", ts)
                if np.isfinite(hb) and hb > -400 and np.isfinite(ht):
                    off = ic.get("hsurf", 0.0) if C["conv_ref"] == "msl" else 0.0
                    base_ft, top_m = (hb - off) * FT, ht - off
                    if base_ft < high:
                        if top_m > C["cb_top_m"] and np.isfinite(cape) and cape > C["cb_cape"]:
                            cb = "CB"
                        elif top_m > C["tcu_top_m"]:
                            cb = "TCU"
        # VV в мъгла
        if fog:
            vv = ceil if (ceil is not None and ceil < 1000) else (100 if (c.get("vis_m") or 9999) < 300 else 200)
            c["ceiling_txt"], ceil = f"VV {int(round(vv / 100.0) * 100):d}", vv
        elif ceil is None:
            c["ceiling_txt"] = "висока" if (k > 0 or m0 is not None) else "—"
        else:
            c["ceiling_txt"] = str(int(round(ceil / 100.0) * 100))
        c["ceiling_ft"] = None if ceil is None else int(round(ceil / 100.0) * 100)
        c["low_cloud"] = amt
        c["cb_tcu"] = cb
        c["cloud_src"] = src

        # вероятности (сурови)
        ci_raw = _icon_ceiling_ft(ic, ts, settings.CEILING_REF) if ic else float("nan")
        if k == 0 and m0 is not None:
            a15 = 100.0 if (ceil is not None and ceil < 1500) else 0.0
            a02 = 100.0 if (ceil is not None and ceil < 200) else 0.0
        else:
            low_c = ceil is not None and ceil < 1500
            low_i = np.isfinite(ci_raw) and ci_raw < 1500
            a15 = 80.0 if (low_c and low_i) else 60.0 if low_c else 30.0 if low_i else 5.0
            a02 = 70.0 if (ceil is not None and ceil < 200) else 5.0
            if np.isfinite(ci_raw) and ci_raw < 200:
                a02 = max(a02, 67.0)
            a02 = max(a02, float(c.get("p_lt1000") or 0))     # мъгла ↔ таван < 200
        p15.append(a15)
        p02.append(a02)
        cig_raw.append(ts)

    for k, c in enumerate(cols):
        ts = cig_raw[k]
        win = [j for j in range(len(cols)) if abs(cig_raw[j] - ts) <= 3600]
        a15 = p15[k] if k == 0 else sum(p15[j] for j in win) / len(win)
        a02 = p02[k] if k == 0 else sum(p02[j] for j in win) / len(win)
        if c.get("ceiling_ft") is not None and c["ceiling_ft"] < 1500:
            a15 = max(a15, 60.0)
        if c.get("ceiling_ft") is not None and c["ceiling_ft"] < 200:
            a02 = max(a02, 60.0)
        a15 = max(a15, a02)
        c["p_cig1500"], c["p_cig200"] = round(a15), round(a02)
        c["cig1500"], c["cig200"] = _deg(a15), _deg(a02)
    return cols
