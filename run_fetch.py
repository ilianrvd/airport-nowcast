"""
Стъпка 1: сваляне на всички входни данни + архив
=================================================
METAR (AWC) → IFS и GFS (Open-Meteo) → ICON-EU (DWD, кеш по run).
Записва cache/latest_inputs.json и архивно копие archive/YYYY/MM/DD/HHMM.json.gz.
Отказ на един източник не спира останалите — записва се в "errors".

Употреба:  python run_fetch.py              (всичко)
           python run_fetch.py --no-icon    (без ICON, бърз тест)
"""

import os, sys, json, gzip, argparse, logging
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.settings import (AIRPORTS, ICAO_LIST, HORIZON_H, METAR, OPEN_METEO, NOWCAST,
                             ICON_DWD, ICON_CELLS_FILE, CACHE_DIR, ARCHIVE_DIR, ALADIN)
from src.fetch.nowcast import fetch_airports_json
from src.fetch.fog import fetch_fog_json
from config.settings import FOG
from src.fetch.metar import fetch_metars, is_stale
from src.fetch.openmeteo import fetch_model, nb_coords, fetch_neighbourhood
from src.fetch.icon_dwd import fetch_icon
from src.fetch.aladin import fetch_aladin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("run_fetch")


def _json_default(o):
    if isinstance(o, dt.datetime):
        return o.isoformat()
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(type(o))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-icon", action="store_true")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    inputs = {"created": now.isoformat(), "errors": {}}

    try:
        inputs["metar"] = fetch_metars(ICAO_LIST, METAR["url"], METAR["hours"])
    except Exception as e:
        log.error(f"METAR: {e}")
        inputs["errors"]["metar"] = str(e)

    try:
        inputs["nowcast"] = fetch_airports_json(NOWCAST["url"], NOWCAST["max_age_min"])
    except Exception as e:
        log.error(f"airports.json: {e}")
        inputs["errors"]["nowcast"] = str(e)

    try:
        inputs["fog"] = fetch_fog_json(FOG["url"], FOG["max_age_h"])
    except Exception as e:
        log.error(f"fog.json: {e}")
        inputs["errors"]["fog"] = str(e)

    for key, cfg in OPEN_METEO["models"].items():
        try:
            inputs[key] = fetch_model(key, cfg, AIRPORTS, OPEN_METEO, CACHE_DIR)
        except Exception as e:
            log.error(f"{key}: {e}")
            inputs["errors"][key] = str(e)

    # IFS в точките на околността (същите като клетките на ICON)
    try:
        coords = nb_coords(AIRPORTS, ICON_CELLS_FILE, ICON_DWD["grid"], ICON_DWD["nb_radius"])
        inputs["ifs_nb"] = fetch_neighbourhood("ecmwf_ifs", coords, ["visibility"], OPEN_METEO)
    except Exception as e:
        log.error(f"IFS околност: {e}")
        inputs["errors"]["ifs_nb"] = str(e)

    # ALADIN — само архив (не влиза в таблицата)
    try:
        path, run, new = fetch_aladin(now, AIRPORTS, ALADIN, OPEN_METEO, CACHE_DIR)
        inputs["aladin"] = {"file": os.path.relpath(path, CACHE_DIR), "run": run, "new": new}
    except Exception as e:
        log.error(f"ALADIN: {e}")
        inputs["errors"]["aladin"] = str(e)

    if not args.no_icon:
        try:
            path, icon = fetch_icon(now, AIRPORTS, ICON_DWD, HORIZON_H, CACHE_DIR, ICON_CELLS_FILE)
            inputs["icon"] = {"file": os.path.relpath(path, CACHE_DIR), "run": icon["run"],
                              "failed_files": icon.get("failed_files", 0)}
        except Exception as e:
            log.error(f"ICON: {e}")
            inputs["errors"]["icon"] = str(e)

    latest = os.path.join(CACHE_DIR, "latest_inputs.json")
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(inputs, f, ensure_ascii=False, default=_json_default)
    arch_dir = os.path.join(ARCHIVE_DIR, f"{now:%Y}", f"{now:%m}", f"{now:%d}")
    os.makedirs(arch_dir, exist_ok=True)
    with gzip.open(os.path.join(arch_dir, f"{now:%H%M}.json.gz"), "wt", encoding="utf-8") as f:
        json.dump(inputs, f, ensure_ascii=False, default=_json_default)

    summary(inputs, now)


def summary(inputs, now):
    print("\n" + "=" * 78)
    print(f"Обобщение {now:%Y-%m-%d %H:%M} UTC")
    print("=" * 78)
    met = inputs.get("metar", {})
    for icao in ICAO_LIST:
        ms = met.get(icao, [])
        if ms:
            m = ms[0]
            age = int((now - m["time"]).total_seconds() // 60) if m.get("time") else None
            stale = " (СТАР)" if is_stale(m, now, METAR["stale_min"]) else ""
            print(f"{icao} METAR преди {age} мин{stale}: {m['raw']}")
            g = f"G{m['gust_kt']}" if m["gust_kt"] else ""
            print(f"     вятър {m['wind_dir']}/{m['wind_kt']}{g}  вид {m['vis_m']} m  "
                  f"wx {' '.join(m['wx']) or '—'}  таван {m['ceiling_ft']} ft  T/Td {m['temp_c']}/{m['dew_c']}")
        else:
            print(f"{icao} METAR: няма")
    for key in ("ifs", "gfs"):
        d = inputs.get(key)
        if d:
            print(f"\n{key.upper()} ({d['model']}): {len(d['times'])} часа, "
                  f"{d['times'][0][:16]} … {d['times'][-1][:16]}")
            if d["dropped"]:
                print(f"     отказани променливи: {', '.join(d['dropped'])}")
            # Празни полета: Open-Meteo понякога връща null вместо отказ
            for icao, vals in d["airports"].items():
                empty = [v for v, arr in vals.items()
                         if isinstance(arr, list) and not any(x is not None for x in arr)]
                if empty:
                    print(f"     {icao} празни: {', '.join(empty)}")
            ap0 = d["airports"].get("LBSF", {})
            cb = ap0.get("cloud_base")
            if cb is not None:
                print(f"     LBSF cloud_base (m): {cb[:8]}")
    nc = inputs.get("nowcast")
    if nc:
        src = ", ".join(f"{k} {v.get('source')}" for k, v in nc.get("airports", {}).items())
        print(f"\nNowcast ({nc.get('method')}): радар {nc.get('radar_time', '')[11:16]}Z, "
              f"на {nc.get('age_min')} мин{' — СТАР' if nc.get('stale') else ''}; {src}")
    fg = inputs.get("fog")
    if fg:
        fogs = ", ".join(f"{k} {'МЪГЛА' if (v.get('summary') or {}).get('fog') else 'ясно'}"
                         for k, v in fg.get("airports", {}).items())
        print(f"\nМодел за мъгла: init {fg.get('init', '')[:16]}, на {fg.get('age_h')} ч"
              f"{' — СТАР, не се ползва' if fg.get('stale') else ''}; {fogs}")
    if "icon" in inputs:
        i = inputs["icon"]
        print(f"\nICON-EU run {i['run'][:16]}, файл {i['file']}, неуспешни файлове: {i['failed_files']}")
    if "aladin" in inputs:
        a = inputs["aladin"]
        print(f"\nALADIN run {a['run'][:16]}, файл {a['file']}{' (нов)' if a['new'] else ''}")
    if inputs["errors"]:
        print("\nГРЕШКИ:", json.dumps(inputs["errors"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
