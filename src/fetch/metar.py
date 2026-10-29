"""
METAR от AWC Data API + собствен парсер на суровия текст
========================================================
Взимаме rawOb и obsTime от AWC и парсваме сами: видимостта на AWC е в мили,
а облачността и явленията ни трябват в нашите единици и прагове.

Резултат за всяко съобщение (dict):
  icao, time (UTC datetime), type (METAR/SPECI), raw, auto, cor
  wind_dir (° | "VRB" | None), wind_kt, gust_kt, wind_var ((от, до) | None)
  vis_m (int | None; 10000 = 10 km и повече), cavok (bool)
  wx (списък групи: "FG", "-RA", "VCTS" …)
  clouds (списък {"cover", "base_ft", "type"}), vv_ft, nsc (bool)
  ceiling_ft (най-ниският BKN/OVC или VV; None = няма под 5000 ft / няма данни)
  cb_tcu (str | None), temp_c, dew_c, qnh_hpa
  missing (списък групи с ////)
"""

import re
import logging
import datetime as dt

import requests

logger = logging.getLogger("fetch.metar")

_WIND = re.compile(r"^(VRB|\d{3}|///)(\d{2,3}|//)(?:G(\d{2,3}))?(KT|MPS)$")
_WIND_VAR = re.compile(r"^(\d{3})V(\d{3})$")
_VIS = re.compile(r"^(\d{4})(NDV|N|NE|E|SE|S|SW|W|NW)?$")
_RVR = re.compile(r"^R\d{2}[LCR]?/")
_WX = re.compile(
    r"^(\+|-|VC)?(MI|BC|PR|DR|BL|SH|TS|FZ)?"
    r"((?:DZ|RA|SN|SG|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PO|SQ|FC|SS|DS)*)$")
_CLOUD = re.compile(r"^(FEW|SCT|BKN|OVC)(\d{3}|///)(CB|TCU|///)?$")
_VV = re.compile(r"^VV(\d{3}|///)$")
_TEMP = re.compile(r"^(M?\d{2}|//)/(M?\d{2}|//)$")
_QNH = re.compile(r"^Q(\d{4})$")
_STOP = {"NOSIG", "BECMG", "TEMPO", "RMK"}


def _t(s):
    if s is None or "/" in s:
        return None
    return -int(s[1:]) if s.startswith("M") else int(s)


def parse_metar(raw: str) -> dict:
    """Парсва суров METAR/SPECI текст. Непознатите групи се пропускат."""
    toks = raw.replace("=", "").split()
    out = {
        "raw": raw, "type": "METAR", "icao": None, "auto": False, "cor": False,
        "wind_dir": None, "wind_kt": None, "gust_kt": None, "wind_var": None,
        "vis_m": None, "cavok": False, "wx": [], "clouds": [], "vv_ft": None,
        "nsc": False, "temp_c": None, "dew_c": None, "qnh_hpa": None,
        "missing": [],
    }
    i = 0
    if toks and toks[0] in ("METAR", "SPECI"):
        out["type"] = toks[0]
        i += 1
    if i < len(toks) and re.match(r"^[A-Z]{4}$", toks[i]):
        out["icao"] = toks[i]
        i += 1
    if i < len(toks) and re.match(r"^\d{6}Z$", toks[i]):
        i += 1

    for tok in toks[i:]:
        if tok in _STOP:
            break
        if tok == "AUTO":
            out["auto"] = True
        elif tok == "COR":
            out["cor"] = True
        elif (m := _WIND.match(tok)):
            d, s, g, unit = m.groups()
            k = 1.0 if unit == "KT" else 1.943844
            out["wind_dir"] = None if d == "///" else ("VRB" if d == "VRB" else int(d))
            out["wind_kt"] = None if "/" in s else round(int(s) * k)
            out["gust_kt"] = round(int(g) * k) if g else None
            if "/" in tok:
                out["missing"].append("wind")
        elif (m := _WIND_VAR.match(tok)):
            out["wind_var"] = (int(m.group(1)), int(m.group(2)))
        elif tok == "CAVOK":
            out["cavok"] = True
            out["vis_m"] = 10000
        elif tok == "////":
            out["missing"].append("vis")
        elif (m := _VIS.match(tok)) and out["vis_m"] is None:
            v = int(m.group(1))
            out["vis_m"] = 10000 if v == 9999 else v
        elif (m := _VIS.match(tok)):
            pass  # минимална видимост по направление — не я ползваме
        elif _RVR.match(tok):
            pass
        elif tok in ("NSC", "NCD", "SKC", "CLR"):
            out["nsc"] = True
        elif (m := _CLOUD.match(tok)):
            cover, base, typ = m.groups()
            out["clouds"].append({
                "cover": cover,
                "base_ft": None if base == "///" else int(base) * 100,
                "type": None if typ in (None, "///") else typ,
            })
            if base == "///":
                out["missing"].append("cloud_base")
        elif (m := _VV.match(tok)):
            out["vv_ft"] = None if m.group(1) == "///" else int(m.group(1)) * 100
            if m.group(1) == "///":
                out["missing"].append("vv")
        elif (m := _TEMP.match(tok)):
            out["temp_c"], out["dew_c"] = _t(m.group(1)), _t(m.group(2))
            if "/" in m.group(1) + m.group(2):
                out["missing"].append("temp")
        elif (m := _QNH.match(tok)):
            out["qnh_hpa"] = int(m.group(1))
        elif tok.startswith("RE") or tok.startswith("WS"):
            pass  # последно време, срез на вятъра — не ни трябват тук
        elif tok == "//":
            out["missing"].append("wx")
        elif (m := _WX.match(tok)) and m.group(3) or tok in ("TS", "VCTS", "VCSH", "-TS", "+TS"):
            out["wx"].append(tok)

    # Таван: най-ниският BKN/OVC или VV
    cands = [c["base_ft"] for c in out["clouds"]
             if c["cover"] in ("BKN", "OVC") and c["base_ft"] is not None]
    if out["vv_ft"] is not None:
        cands.append(out["vv_ft"])
    out["ceiling_ft"] = min(cands) if cands else None
    cbs = [c["type"] for c in out["clouds"] if c["type"]]
    out["cb_tcu"] = "CB" if "CB" in cbs else ("TCU" if "TCU" in cbs else None)
    return out


def fetch_metars(icaos, url, hours=3, timeout=30):
    """Последните METAR/SPECI за списък летища → {icao: [parsed, …]} (най-новият първи)."""
    params = {"ids": ",".join(icaos), "format": "json", "hours": hours}
    r = requests.get(url, params=params, timeout=timeout,
                     headers={"User-Agent": "airport-nowcast/0.1"})
    r.raise_for_status()
    data = r.json() if r.content else []
    out = {ic: [] for ic in icaos}
    for rec in data:
        raw = rec.get("rawOb")
        ic = rec.get("icaoId")
        if not raw or ic not in out:
            continue
        p = parse_metar(raw)
        t = rec.get("obsTime")
        p["time"] = (dt.datetime.fromtimestamp(t, tz=dt.timezone.utc)
                     if isinstance(t, (int, float)) else None)
        if rec.get("metarType") in ("METAR", "SPECI"):
            p["type"] = rec["metarType"]
        out[ic].append(p)
    for ic in out:
        out[ic].sort(key=lambda p: p["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                     reverse=True)
        if not out[ic]:
            logger.warning(f"{ic}: няма METAR за последните {hours} ч")
    return out


def is_stale(metar: dict, now: dt.datetime, stale_min: int) -> bool:
    return metar.get("time") is None or (now - metar["time"]).total_seconds() > stale_min * 60
