"""
ICON-EU от DWD Open Data — точки за петте летища, кеш по run
=============================================================
ICON-EU тръгва на 3 ч и се публикува ~2 ч 40 мин след началото на run-а.
Тегли се само при нов run: всички нужни GRIB файлове се свалят паралелно,
от всеки се взимат петте точки и файлът се изтрива. Свалянето има срок; каквото
не се свали, остава в cache/icon/icon_<YYYYMMDDHH>.partial.json за следващото пускане. Остава малък JSON:
cache/icon/icon_<YYYYMMDDHH>.json, който таблицата чете при всяко пускане.

Акумулираните полета (tot_prec, rain_*, snow_*) остават акумулирани от
началото на run-а — разликата по часове се смята в обработката.
`ceiling`, `hbas_con`, `htop_con` се пазят както идват (вероятно над MSL —
отворена задача); `hsurf` е в същия файл, за да се превърнат после.

Структура на JSON:
{"run": ISO, "steps": [h, …], "valid": [ISO, …],
 "airports": {icao: {"cell": [i, j], "cell_lat", "cell_lon",
                     "hsurf", "fr_land",
                     "sl": {field: [стойност по стъпка]},
                     "pl": {field: {"850": [..], …}}}}}
"""

import os, re, bz2, json, math, time, logging, tempfile
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests

logger = logging.getLogger("fetch.icon_dwd")
_session = requests.Session()
_session.headers["User-Agent"] = "airport-nowcast/0.1"

PREFIX = "icon-eu_europe_regular-lat-lon"


def _fname_sl(run, step, field):
    return f"{PREFIX}_single-level_{run:%Y%m%d%H}_{step:03d}_{field.upper()}.grib2.bz2"


def _fname_pl(run, step, lev, field):
    return f"{PREFIX}_pressure-level_{run:%Y%m%d%H}_{step:03d}_{lev}_{field.upper()}.grib2.bz2"


def _fname_ti(run, field):
    return f"{PREFIX}_time-invariant_{run:%Y%m%d%H}_{field.upper()}.grib2.bz2"


def _listing(base, run_hh, field):
    r = _session.get(f"{base}/{run_hh:02d}/{field}/", timeout=30)
    r.raise_for_status()
    return set(re.findall(rf"{PREFIX}_[a-z-]+_\d{{10}}_[\w]+\.grib2\.bz2", r.text))


def needed_steps(run, now, horizon_h):
    """Стъпките (часове от run-а), които покриват [now − 1 ч, now + horizon]."""
    h0 = (now - dt.timedelta(hours=1) - run).total_seconds() / 3600
    h1 = (now + dt.timedelta(hours=horizon_h) - run).total_seconds() / 3600
    return list(range(max(0, math.floor(h0)), math.ceil(h1) + 1))


def find_run(now, cfg, horizon_h):
    """Най-новият run, за който последната нужна стъпка е публикувана
    и за едно поле на земята, и за едно поле по нива."""
    base = now.replace(minute=0, second=0, microsecond=0)
    base -= dt.timedelta(hours=base.hour % cfg["run_every_h"])
    for k in range(cfg["max_lag_runs"] + 1):
        run = base - dt.timedelta(hours=cfg["run_every_h"] * k)
        steps = needed_steps(run, now, horizon_h)
        last = steps[-1]
        try:
            sl = _listing(cfg["base_url"], run.hour, "t_2m")
            pl = _listing(cfg["base_url"], run.hour, "t")
        except requests.RequestException as e:
            logger.warning(f"листинг {run:%Y%m%d%H}: {e}")
            continue
        if _fname_sl(run, last, "t_2m") in sl and _fname_pl(run, last, 850, "t") in pl:
            return run, steps
        logger.info(f"run {run:%Y-%m-%d %H}Z не е пълен до стъпка {last} — пробвам предишния")
    return None, None


def _read_grib(path):
    import xarray as xr
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    da = ds[list(ds.data_vars)[0]]
    lat = da["latitude"].values.astype(np.float64)
    lon = da["longitude"].values.astype(np.float64)
    data = np.asarray(da.values, dtype=np.float64)
    ds.close()
    if lon.max() > 180:
        lon = np.where(lon > 180, lon - 360, lon)
    return lat, lon, data


class DeadlineExceeded(Exception):
    """Срокът за ICON изтече — файлът не е свален (не се брои за неуспешен опит)."""


def _get(url, deadline=None):
    """Сваля файла; след deadline (time.monotonic()) прекъсва с DeadlineExceeded,
    вкл. по средата на бавно сваляне."""
    if deadline is not None and time.monotonic() > deadline:
        raise DeadlineExceeded(url)
    r = _session.get(url, timeout=(15, 60), stream=True)
    try:
        r.raise_for_status()
        buf = bytearray()
        for chunk in r.iter_content(1 << 16):
            if deadline is not None and time.monotonic() > deadline:
                raise DeadlineExceeded(url)
            buf += chunk
    finally:
        r.close()
    return bytes(buf)


def _download_points(url, cells, tmpdir, nb=None, deadline=None):
    """Сваля един GRIB, взима стойностите в клетките (и в околността nb, ако е
    подадена: {icao: [(i, j), …]}), изтрива файла."""
    content = _get(url, deadline)
    path = os.path.join(tmpdir, os.path.basename(url)[:-4])
    with open(path, "wb") as f:
        f.write(bz2.decompress(content))
    try:
        lat, lon, data = _read_grib(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    def val(i, j):
        v = float(data[i, j])
        return None if not np.isfinite(v) else v

    vals = {icao: val(i, j) for icao, (i, j) in cells.items()}
    if nb is None:
        return lat, lon, vals
    nbv = {icao: [val(i, j) for i, j in lst] for icao, lst in nb.items()}
    return lat, lon, vals, nbv


def nearest_cell(lat_axis, lon_axis, lat, lon):
    return int(np.abs(lat_axis - lat).argmin()), int(np.abs(lon_axis - lon).argmin())


def load_cells(airports, cells_file, lat_axis, lon_axis):
    """Фиксираните клетки от config/icon_cells.json; липсващите → най-близката."""
    fixed = {}
    if os.path.exists(cells_file):
        with open(cells_file, encoding="utf-8") as f:
            fixed = json.load(f)
    cells = {}
    for icao, a in airports.items():
        if icao in fixed:
            cells[icao] = tuple(fixed[icao]["cell"])
        else:
            cells[icao] = nearest_cell(lat_axis, lon_axis, a["lat"], a["lon"])
            logger.info(f"{icao}: няма фиксирана клетка — ползвам най-близката {cells[icao]}")
    return cells


def _url(run, cfg, m):
    """URL на файла за липсващ запис m = {"kind", "field", "level", "step"}."""
    f, s = m["field"], m["step"]
    name = _fname_sl(run, s, f) if m["kind"] == "sl" else _fname_pl(run, s, m["level"], f)
    return f"{cfg['base_url']}/{run.hour:02d}/{f}/{name}"


def _step_entries(steps, cfg):
    """Записите (файловете) за стъпките steps, подредени по стъпка."""
    out = []
    for s in steps:
        for f in cfg["single_level"]:
            out.append({"kind": "sl", "field": f, "level": None, "step": s, "tries": 0})
        for f, levs in cfg["pressure_level"].items():
            for l in levs:
                out.append({"kind": "pl", "field": f, "level": l, "step": s, "tries": 0})
    return out


def _usable(cached, airports, cfg):
    """Кешът е в текущия формат: околност, същите летища, същите полета и нива."""
    ap = cached.get("airports", {})
    has_nb = all("nb" in a for a in ap.values())
    a0 = next(iter(ap.values()), {})
    same_fields = all(str(l) in a0.get("pl", {}).get(f, {})
                      for f, levs in cfg["pressure_level"].items() for l in levs)
    return (has_nb or not cfg.get("nb_fields")) and set(ap) == set(airports) and same_fields


def _load(path, airports, cfg):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"{os.path.basename(path)}: не се чете ({e}) — тегли се наново")
        return None
    if not _usable(cached, airports, cfg):          # нови полета/нива или стар формат → наново
        return None
    cached.setdefault("missing", [])
    return cached


def _skeleton(run, airports, cfg, cells_file, deadline):
    """Нов прогон без стъпки: мрежата, клетките, hsurf и fr_land от неизменните полета."""
    base, hh = cfg["base_url"], run.hour
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "hsurf.grib2")
        with open(p, "wb") as f:
            f.write(bz2.decompress(_get(f"{base}/{hh:02d}/hsurf/{_fname_ti(run, 'hsurf')}", deadline)))
        lat_ax, lon_ax, hs = _read_grib(p)
        cells = load_cells(airports, cells_file, lat_ax, lon_ax)
        R = cfg.get("nb_radius", 2)
        nbc = {ic: [(i + di, j + dj) for di in range(-R, R + 1) for dj in range(-R, R + 1)]
               for ic, (i, j) in cells.items()}
        frl, frl_nb = _download_points(f"{base}/{hh:02d}/fr_land/{_fname_ti(run, 'fr_land')}",
                                       cells, tmp, nbc, deadline)[2:]
    hsurf = {ic: float(hs[c]) for ic, c in cells.items()}
    hsurf_nb = {ic: [float(hs[c]) for c in nbc[ic]] for ic in cells}
    nb_fields = cfg.get("nb_fields", [])
    res = {ic: {"cell": list(cells[ic]),
                "cell_lat": float(lat_ax[cells[ic][0]]),
                "cell_lon": float(lon_ax[cells[ic][1]]),
                "hsurf": hsurf[ic],
                "fr_land": frl[ic],
                "sl": {f: [] for f in cfg["single_level"]},
                "pl": {f: {str(l): [] for l in levs} for f, levs in cfg["pressure_level"].items()},
                # Околност R×R: координати, маска (hsurf, fr_land) и полетата nb_fields
                "nb": {"cells": [list(c) for c in nbc[ic]],
                       "lat": [float(lat_ax[c[0]]) for c in nbc[ic]],
                       "lon": [float(lon_ax[c[1]]) for c in nbc[ic]],
                       "hsurf": hsurf_nb[ic],
                       "fr_land": frl_nb[ic],
                       **{f: [] for f in nb_fields}}}
           for ic in airports}
    return {"run": run.isoformat(), "steps": [], "valid": [], "failed_files": 0,
            "missing": [], "airports": res}


def _add_steps(work, run, steps, cfg):
    """Добавя в края стъпките от steps, които липсват (празни + записи в missing).
    Липсващи файлове за стъпки преди steps[0] вече не са нужни — отпадат."""
    old = work["steps"]
    new = [s for s in steps if not old or s > old[-1]]
    for a in work["airports"].values():
        for f in a["sl"]:
            a["sl"][f] += [None] * len(new)
        for levs in a["pl"].values():
            for l in levs:
                levs[l] += [None] * len(new)
        for f in cfg.get("nb_fields", []):
            a["nb"][f] += [None] * len(new)
    work["steps"] = old + new
    work["valid"] = [(run + dt.timedelta(hours=s)).isoformat() for s in work["steps"]]
    work["missing"] = [m for m in work["missing"] if m["step"] >= steps[0]] + _step_entries(new, cfg)
    return new


def _fetch_missing(work, run, cfg, deadline):
    """Сваля файловете от work["missing"] паралелно до срока. Неуспешен файл се пробва до
    max_tries пъти: при HTTP грешка — веднага в същото пускане (ако има време), при грешка
    на мрежата — в следващото. После остава None и се брои във failed_files.
    Неизтеглените заради срока остават в missing."""
    res = work["airports"]
    idx = {s: k for k, s in enumerate(work["steps"])}
    cells = {ic: tuple(a["cell"]) for ic, a in res.items()}
    nbc = {ic: [tuple(c) for c in a["nb"]["cells"]] for ic, a in res.items()}
    nb_fields = cfg.get("nb_fields", [])
    max_tries = cfg.get("max_tries", 3)
    todo, timed_out = work["missing"], []

    with tempfile.TemporaryDirectory() as tmp:
        while todo:
            retry = []
            with ThreadPoolExecutor(max_workers=cfg["workers"]) as ex:
                futs = {ex.submit(_download_points, _url(run, cfg, m), cells, tmp,
                                  nbc if (m["kind"] == "sl" and m["field"] in nb_fields) else None,
                                  deadline): m for m in todo}
                for fut in as_completed(futs):
                    m = futs[fut]
                    try:
                        out_ = fut.result()
                    except DeadlineExceeded:
                        timed_out.append(m)
                        continue
                    except Exception as e:
                        m = dict(m, tries=m.get("tries", 0) + 1)
                        logger.debug(f"{os.path.basename(_url(run, cfg, m))} (опит {m['tries']}): {e}")
                        if m["tries"] >= max_tries:
                            work["failed_files"] = work.get("failed_files", 0) + 1
                        elif isinstance(e, requests.HTTPError):
                            retry.append(m)          # отговор от DWD (напр. 404) — веднага отново
                        else:
                            timed_out.append(m)      # мрежа/изчакване (бавен DWD) — в следващото пускане
                        continue
                    k, vals = idx[m["step"]], out_[2]
                    if len(out_) == 4:
                        for ic, lst in out_[3].items():
                            res[ic]["nb"][m["field"]][k] = lst
                    for ic, v in vals.items():
                        if m["kind"] == "sl":
                            res[ic]["sl"][m["field"]][k] = v
                        else:
                            res[ic]["pl"][m["field"]][str(m["level"])][k] = v
            if deadline is not None and time.monotonic() > deadline:
                timed_out += retry               # повторните опити — в следващото пускане
                break
            todo = retry

    order = {s: k for k, s in enumerate(work["steps"])}
    work["missing"] = sorted(timed_out, key=lambda m: order.get(m["step"], 0))
    return work


def _write(path, data):
    data["fetched"] = dt.datetime.now(dt.timezone.utc).isoformat()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _cache_fallback(now, cfg, cache_dir):
    """Последният пълен прогон в кеша, ако не е по-стар от cache_fallback_h. Ползва се,
    когато DWD няма нов пълен run или листингът не се чете (26.11, 12:12Z) и когато новият
    run не се свали в срока (27.11, 03:12Z и 03:42Z — бавен DWD). Частичните файлове
    (*.partial.json) не се ползват."""
    max_h = cfg.get("cache_fallback_h", 0)
    d = os.path.join(cache_dir, "icon")
    best = None
    for fn in os.listdir(d) if os.path.isdir(d) else []:
        mm = re.match(r"^icon_(\d{10})\.json$", fn)
        if not mm:
            continue
        run = dt.datetime.strptime(mm.group(1), "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        if (now - run).total_seconds() <= max_h * 3600 and (best is None or run > best[0]):
            best = (run, os.path.join(d, fn))
    if not best:
        return None
    with open(best[1], encoding="utf-8") as f:
        cached = json.load(f)
    cached["cache_fallback"] = True
    logger.warning(f"ICON: ползвам {best[0]:%Y-%m-%d %H}Z от кеша")
    return best[1], cached


def fetch_icon(now, airports, cfg, horizon_h, cache_dir, cells_file, deadline=None):
    """Връща (път до JSON, dict). Ако run-ът вече е в кеша — само го чете.

    icon_<run>.json съдържа само пълни данни. Каквото не се свали до срока (deadline,
    time.monotonic()), се пази в icon_<run>.partial.json и следващото пускане тегли само
    липсващите файлове. Докато новите данни не са пълни, се ползва пълният файл за
    същия run (по-късите стъпки), а ако го няма — предишният пълен прогон от кеша."""
    d = os.path.join(cache_dir, "icon")
    os.makedirs(d, exist_ok=True)
    run, steps = find_run(now, cfg, horizon_h)
    if run is None:
        logger.warning("ICON: няма нов пълен run в DWD")
        fb = _cache_fallback(now, cfg, cache_dir)
        if fb:
            return fb
        raise RuntimeError("ICON-EU: няма пълен run в последните run-ове")
    main_path = os.path.join(d, f"icon_{run:%Y%m%d%H}.json")
    part_path = os.path.join(d, f"icon_{run:%Y%m%d%H}.partial.json")
    main = _load(main_path, airports, cfg)
    part = _load(part_path, airports, cfg)
    if main is not None and part is None and all(s in main["steps"] for s in steps):
        logger.info(f"ICON {run:%Y-%m-%d %H}Z от кеша")
        return main_path, main

    work = part if part is not None else (json.loads(json.dumps(main)) if main is not None else None)
    try:
        if work is None:
            work = _skeleton(run, airports, cfg, cells_file, deadline)
            src = "нов"
        else:
            src = "продължение" if part is not None else "от кеша +"
        _add_steps(work, run, steps, cfg)
        n = len(work["missing"])
        if n:
            logger.info(f"ICON {run:%Y-%m-%d %H}Z ({src}): {n} файла, стъпки {steps[0]}–{steps[-1]}")
            _fetch_missing(work, run, cfg, deadline)
    except Exception as e:                        # неизменните полета не се свалиха (вкл. срокът)
        logger.warning(f"ICON {run:%Y-%m-%d %H}Z: {type(e).__name__}: {e}")
        if work is None or not work.get("airports"):
            work = None

    if work is not None and not work["missing"]:
        if work.get("failed_files"):
            logger.warning(f"ICON: {work['failed_files']} файла не се свалиха след "
                           f"{cfg.get('max_tries', 3)} опита (полето липсва или мрежа)")
        work.pop("missing", None)
        _write(main_path, work)
        if os.path.exists(part_path):
            os.remove(part_path)
        return main_path, work

    if work is not None:
        _write(part_path, work)
        logger.warning(f"ICON {run:%Y-%m-%d %H}Z: срокът изтече, остават {len(work['missing'])} "
                       f"файла — продължава в следващото пускане")
    if main is not None:
        logger.warning(f"ICON {run:%Y-%m-%d %H}Z: ползвам кешираните стъпки {main['steps'][0]}–"
                       f"{main['steps'][-1]} без новите")
        main["pending_steps"] = True
        return main_path, main
    fb = _cache_fallback(now, cfg, cache_dir)
    if fb:
        return fb
    if work is not None:                          # няма нищо по-добро — частичните данни
        logger.warning("ICON: няма пълен прогон в кеша — ползвам частичния")
        work["partial"] = True
        return part_path, work
    raise RuntimeError(f"ICON-EU {run:%Y-%m-%d %H}Z: не се свали, а в кеша няма прогон")
