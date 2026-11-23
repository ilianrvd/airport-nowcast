"""MultiConvect → airports.json (категории за конвекция в 25/50 km около летищата, 0–48 h) — само архив.

Файлът се записва веднъж на рън в cache/multiconvect/mc_YYYYMMDDHH.json; workflow-ът го копира
в архива (arch/multiconvect/). Във входните данни на пускането остава само препратка (run, файл, възраст).
Цел: калибриране на CB/TS след хоризонта на nowcast срещу METAR (вместо правилото „два модела“).
"""
import datetime as dt
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)


def fetch_multiconvect(cfg, cache_dir, now):
    r = requests.get(cfg["url"], timeout=cfg["timeout"], headers={"User-Agent": "airport-nowcast/0.1"})
    r.raise_for_status()
    data = r.json()
    init = data.get("run_init_utc")
    run = dt.datetime.fromisoformat(init.replace("Z", "+00:00")) if init else None
    out_dir = os.path.join(cache_dir, "multiconvect")
    os.makedirs(out_dir, exist_ok=True)
    tag = f"{run:%Y%m%d%H}" if run else now.strftime("%Y%m%d%H") + "x"
    path = os.path.join(out_dir, f"mc_{tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    age_h = (now - run).total_seconds() / 3600 if run else None
    return {"file": os.path.relpath(path, cache_dir), "run": init, "created": data.get("created_utc"),
            "rules_version": data.get("rules_version"),
            "age_h": round(age_h, 1) if age_h is not None else None,
            "stale": bool(age_h is None or age_h > cfg["max_age_h"])}
