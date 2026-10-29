"""
fog.json от модела за мъгла (GitHub Pages)
==========================================
Пуска се веднъж на нощ (init 18Z, файлът е готов ~18:30Z), 13 часа 18Z → 06Z.
Pages кешира ~10 мин → ?t=<epoch>. Файл, чийто init е по-стар от
max_age_h, се маркира "stale" (пускането е пропаднало и е останал старият).
"""

import time
import logging
import datetime as dt

import requests

logger = logging.getLogger("fetch.fog")


def fetch_fog_json(url, max_age_h=14, timeout=30):
    r = requests.get(url, params={"t": int(time.time())}, timeout=timeout,
                     headers={"User-Agent": "airport-nowcast/0.1", "Cache-Control": "no-cache"})
    r.raise_for_status()
    data = r.json()
    init = dt.datetime.fromisoformat(data["init"].replace("Z", "+00:00"))
    age = (dt.datetime.now(dt.timezone.utc) - init).total_seconds() / 3600
    data["age_h"] = round(age, 1)
    data["stale"] = age > max_age_h
    if data["stale"]:
        logger.info(f"fog.json: init {data['init']} е на {age:.0f} ч — не се ползва")
    return data
