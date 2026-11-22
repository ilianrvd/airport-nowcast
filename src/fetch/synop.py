"""SYNOP от планински станции (Черни връх, 15613) — само за архива.

Нужен за пориви, WS и фьон в София и за отделния проект за вятъра (0–3 ч).
Източник: Ogimet getsynop (CSV: станция,год,мес,ден,час,мин,текст). Една заявка на пускане.
Разчитат се само вятър (посока, скорост, порив 910/911), T и Td; суровият текст се пази.
"""
import datetime as dt
import logging

import requests

logger = logging.getLogger(__name__)
MS2KT = 1.94384


def decode(raw):
    """Минимално разчитане на FM-12 SYNOP. Връща dict с wind_dir, wind_kt, gust_kt, temp_c, dew_c."""
    g = raw.replace("=", " ").split()
    out = {"wind_dir": None, "wind_kt": None, "gust_kt": None, "temp_c": None, "dew_c": None}
    try:
        k = g.index("AAXX")
    except ValueError:
        return out
    if len(g) < k + 5:
        return out
    iw = g[k + 1][-1]
    f = 1.0 if iw in ("3", "4") else MS2KT          # 3/4 — възли; 0/1 — m/s
    i = k + 4                                       # iRiXhVV е k+3, Nddff е k+4
    nddff = g[i]
    if len(nddff) == 5 and nddff[1:].isdigit():
        dd, ff = int(nddff[1:3]), int(nddff[3:5])
        if ff == 99 and len(g) > i + 1 and g[i + 1].startswith("00"):
            ff = int(g[i + 1][2:5]); i += 1
        out["wind_dir"] = None if dd in (0, 99) else dd * 10
        out["wind_kt"] = round(ff * f, 1)
    sec = g[i + 1:]
    s3 = sec.index("333") if "333" in sec else len(sec)

    def temp(t):
        if len(t) == 5 and t[2:].isdigit() and t[1] in "01":
            v = int(t[2:]) / 10.0
            return -v if t[1] == "1" else v
        return None
    for t in sec[:s3]:
        if t.startswith("555"):
            break
        if t[0] == "1" and out["temp_c"] is None:
            out["temp_c"] = temp(t)
        elif t[0] == "2" and out["dew_c"] is None:
            out["dew_c"] = temp(t)
    for t in sec[s3 + 1:]:
        if t.startswith("555"):
            break
        if len(t) == 5 and t[:3] in ("910", "911") and t[3:].isdigit():
            out["gust_kt"] = max(out["gust_kt"] or 0, round(int(t[3:]) * f, 1))
    return out


def fetch_synop(cfg, now):
    """{станция: [{time, raw, ...разчетени}]} за последните cfg['hours'] часа."""
    begin = (now - dt.timedelta(hours=cfg["hours"])).strftime("%Y%m%d%H00")
    end = now.strftime("%Y%m%d%H%M")
    res = {}
    for st in cfg["stations"]:
        r = requests.get(cfg["url"], timeout=cfg["timeout"], headers={"User-Agent": "airport-nowcast/0.1"},
                         params={"block": st, "begin": begin, "end": end})
        r.raise_for_status()
        rows = []
        for line in r.text.splitlines():
            p = line.split(",", 6)
            if len(p) < 7 or not p[0].strip().startswith(st):
                continue
            try:
                t = dt.datetime(int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5]), tzinfo=dt.timezone.utc)
            except ValueError:
                continue
            raw = p[6].strip()
            rows.append({"time": t.isoformat(), "raw": raw, **decode(raw)})
        res[st] = rows
    return res
