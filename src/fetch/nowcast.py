"""
airports.json от nowcasting-public (GitHub Pages)
=================================================
Pages кешира ~10 мин, затова към адреса се добавя ?t=<epoch>.
Файл по-стар от max_age_min (по "created" вътре в него) се връща с
"stale": true — обработката тогава минава към моделите.
"""

import time
import logging
import datetime as dt

import requests

logger = logging.getLogger("fetch.nowcast")


def fetch_airports_json(url, max_age_min=30, timeout=30):
    r = requests.get(url, params={"t": int(time.time())}, timeout=timeout,
                     headers={"User-Agent": "airport-nowcast/0.1", "Cache-Control": "no-cache"})
    r.raise_for_status()
    data = r.json()
    created = dt.datetime.fromisoformat(data["created"].replace("Z", "+00:00"))
    age = (dt.datetime.now(dt.timezone.utc) - created).total_seconds() / 60
    data["age_min"] = round(age, 1)
    data["stale"] = age > max_age_min
    if data["stale"]:
        logger.warning(f"airports.json е на {age:.0f} мин — по-стар от {max_age_min} мин")
    return data
