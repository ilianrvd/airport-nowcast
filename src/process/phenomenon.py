"""
Явление по колони
=================
- Колона „Сега“: групите за явление от METAR.
- 0–6 ч: от airports.json на nowcasting-public (срокът с най-близко valid
  време, ± 15 мин). Интензивност по rate_mmh_3km (WMO за дъжд), SH при
  convective, TS / VCTS по мълниите в 8 / 16 km (само сроковете 0 и +30 мин).
  Ако METAR има валеж, а източникът НЕ е радар и показва 0 → валежът от METAR
  се задържа до 1 ч (сателитът и моделът пропускат слабия валеж).
- Извън nowcast-а (след +6 ч, или файлът липсва/стар): от моделите —
  валеж за часа (ICON + IFS + GFS, тегла като за вятъра), SH при дял на
  конвективния валеж в ICON > 0.5, TS при ww/weather_code 95–99.
- Предпазител за сателита: виж sat_guard() по-долу.
Типът засега е RA; фазата (SN, RASN, FZRA, FZDZ) идва в следващата стъпка.
Резултат по колона: {"wx": "-SHRA" | None, "src": "M" | "Н" | "N", "rate": mm/h}
"""

import datetime as dt

import numpy as np

from .series import weighted

_PRECIP = ("DZ", "RA", "SN", "SG", "PL", "GR", "GS", "UP")


def _metar_precip(m):
    """Групите с валеж (и TS) от METAR, без VC/BR/FG."""
    return [g for g in (m or {}).get("wx", [])
            if not g.startswith("VC") and (any(p in g for p in _PRECIP) or "TS" in g)]


def _hour_value(series, key, ts):
    """Стойност за часа, който съдържа ts (валиден в края на часа)."""
    if series is None or key not in series:
        return float("nan")
    t, y = series["t"], series[key]
    ok = np.isfinite(y) & (t >= ts) & (t < ts + 3600)
    return float(y[ok][0]) if ok.any() else float("nan")


def _intensity(rate, c):
    if not np.isfinite(rate) or rate < c["rain_min"]:
        return None
    if rate < c["rain_moderate"]:
        return "-"
    if rate < c["rain_heavy"]:
        return ""
    return "+"


def _compose(intensity, shower, ts, vcts):
    """Група: [инт][TS|SH]RA, или TS/VCTS без валеж."""
    if intensity is None:
        return "TS" if ts else ("VCTS" if vcts else None)
    desc = "TS" if ts else ("SH" if shower else "")
    g = f"{intensity}{desc}RA"
    return g + (" VCTS" if vcts and not ts else "")


def sat_guard(metars, nowcast_ap, now, settings):
    """Предпазител за сателита (H40B; решение на Илиан 25.11.2026).
    METAR без валеж, без BKN/OVC под low_ft (и без VV) и без CB/TCU → слабото ехо на H40B
    се маха (валеж, видимост от него, CB/TCU и порив): под keep_rate mm/h и не конвективно
    ≥ keep_conv_dbz в 10 km. H40B надценява тънката средна облачност като валежна; силното
    ехо и CB остават. Мълниите остават. При радар (radar_ro, radar_iabg) — нищо не се маха.
    Връща (nowcast_ap за ползване, причина или None)."""
    sg = settings.PHENOMENON.get("sat_guard")
    if not sg or not nowcast_ap or nowcast_ap.get("source") not in sg["sources"]:
        return nowcast_ap, None
    m0 = metars[0] if metars else None
    if m0 is None or m0.get("time") is None or \
            (now - m0["time"]).total_seconds() > settings.METAR["stale_min"] * 60:
        return nowcast_ap, None
    if _metar_precip(m0) or m0.get("vv_ft") is not None or any(
            cl.get("cover") in ("BKN", "OVC") and cl.get("base_ft") is not None and cl["base_ft"] < sg["low_ft"]
            for cl in m0.get("clouds", [])) or any(cl.get("type") for cl in m0.get("clouds", [])):
        return nowcast_ap, None
    zero = {"dbz_max_3km": 0.0, "dbz_max_10km": 0.0, "rate_mmh_3km": 0.0, "convective": False}

    def strong(L):
        return (L.get("rate_mmh_3km") or 0) >= sg["keep_rate"] or \
            (L.get("convective") and (L.get("dbz_max_10km") or 0) >= sg["keep_conv_dbz"])
    leads, n = [], 0
    for L in nowcast_ap.get("leads", []):
        if strong(L) or (L.get("rate_mmh_3km") is None and L.get("dbz_max_3km") is None):
            leads.append(L)
        else:
            n += 1 if ((L.get("rate_mmh_3km") or 0) > 0 or (L.get("dbz_max_10km") or 0) > 0) else 0
            leads.append({**L, **{k: v for k, v in zero.items() if k in L}})
    ap = dict(nowcast_ap)
    ap["leads"] = leads
    why = (f"{nowcast_ap['source']}: METAR без валеж и без BKN/OVC < {sg['low_ft']} ft — "
           f"слабото ехо (< {sg['keep_rate']} mm/h, не конв. ≥ {sg['keep_conv_dbz']} dBZ) не се ползва"
           + (f" ({n} срока)" if n else ""))
    return ap, why


def compute(metars, nowcast_ap, nc_meta, models, cols, now, settings):
    c = settings.PHENOMENON
    W = settings.WEIGHTS["wx"]
    m0 = metars[0] if metars else None
    m_ok = m0 is not None and m0.get("time") is not None and \
        (now - m0["time"]).total_seconds() <= settings.METAR["stale_min"] * 60
    m_precip = _metar_precip(m0) if m_ok else []

    leads = []
    source = None
    if nowcast_ap and nc_meta and not nc_meta.get("stale"):
        source = nowcast_ap.get("source")
        for L in nowcast_ap.get("leads", []):
            if L.get("rate_mmh_3km") is None and L.get("dbz_max_3km") is None:
                continue                # срок без данни (извън хоризонта на nowcast) → моделите
            L = dict(L)
            L["_ts"] = dt.datetime.fromisoformat(L["valid"].replace("Z", "+00:00")).timestamp()
            leads.append(L)
    is_radar = source in c["radar_sources"]
    ltg = (nowcast_ap or {}).get("lightning", {}) or {}

    out = []
    for k, t in enumerate(cols):
        ts = t.timestamp()
        if k == 0 and m_ok:
            wx = [g for g in m0.get("wx", [])]
            out.append({"wx": " ".join(wx) if wx else None, "src": "M", "rate": None})
            continue

        L = None
        if leads:
            best = min(leads, key=lambda x: abs(x["_ts"] - ts))
            if abs(best["_ts"] - ts) <= c["match_min"] * 60:
                L = best

        if L is not None:
            rate = L.get("rate_mmh_3km")
            rate = float("nan") if rate is None else float(rate)
            inten = _intensity(rate, c)
            early = L["lead_min"] <= c["ts_max_lead_min"]
            ts_here = early and (ltg.get("r8") or 0) > 0
            vcts = early and not ts_here and (ltg.get("r16") or 0) > 0
            # Ситнежът от нисък слоест облак често не се вижда и от радара →
            # METAR с DZ се задържа и при радар, и по-дълго.
            dz = any("DZ" in g for g in m_precip)
            age_h = (t - m0["time"]).total_seconds() / 3600 if m_precip else 99
            hold = inten is None and m_precip and (
                (dz and age_h <= c["dz_hold_h"]) or
                (not is_radar and age_h <= c["metar_hold_h"]))
            if hold:
                out.append({"wx": " ".join(m_precip), "src": "M", "rate": rate})
            else:
                out.append({"wx": _compose(inten, bool(L.get("convective")), ts_here, vcts),
                            "src": "Н", "rate": rate})
            continue

        # Модели
        rate = weighted({n: _hour_value(s, "precip", ts) for n, s in models.items()}, W)
        inten = _intensity(rate, c)
        ic = models.get("icon")
        tot_i = _hour_value(ic, "precip", ts)
        conv_i = _hour_value(ic, "precip_conv", ts)
        shower = bool(np.isfinite(tot_i) and tot_i > 0 and np.isfinite(conv_i)
                      and conv_i / tot_i > c["conv_share"])
        ww = [_hour_value(s, "ww", ts) for s in models.values()]
        ts_here = sum(1 for w in ww if np.isfinite(w) and int(w) in c["ts_ww"]) >= 2
        out.append({"wx": _compose(inten, shower, ts_here, False), "src": "N",
                    "rate": None if not np.isfinite(rate) else round(rate, 2)})
    return {"source": source if leads else None, "cols": out}
