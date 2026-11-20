"""
ICON-EU от DWD Open Data — точки за петте летища, кеш по run
=============================================================
ICON-EU тръгва на 3 ч и се публикува ~2 ч 40 мин след началото на run-а.
Тегли се само при нов run: всички нужни GRIB файлове се свалят паралелно,
от всеки се взимат петте точки и файлът се изтрива. Остава малък JSON:
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

import os, re, bz2, json, math, logging, tempfile
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


def _download_points(url, cells, tmpdir, nb=None):
    """Сваля един GRIB, взима стойностите в клетките (и в околността nb, ако е
    подадена: {icao: [(i, j), …]}), изтрива файла."""
    r = _session.get(url, timeout=120)
    r.raise_for_status()
    path = os.path.join(tmpdir, os.path.basename(url)[:-4])
    with open(path, "wb") as f:
        f.write(bz2.decompress(r.content))
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


def _run_jobs(jobs, cells, nbc, nb_fields, cfg, tmp, put):
    """Сваля файловете паралелно; put(kind, f, l, k, vals, nbv) записва стойностите. Връща броя неуспешни."""
    failed = 0
    with ThreadPoolExecutor(max_workers=cfg["workers"]) as ex:
        futs = {ex.submit(_download_points, j[4], cells, tmp,
                          nbc if (j[0] == "sl" and j[1] in nb_fields) else None): j for j in jobs}
        for fut in as_completed(futs):
            kind, f, l, k, url = futs[fut]
            try:
                out_ = fut.result()
            except Exception as e:
                failed += 1
                logger.debug(f"{os.path.basename(url)}: {e}")
                continue
            put(kind, f, l, k, out_[2], out_[3] if len(out_) == 4 else None)
    return failed


def _jobs(run, step_idx, cfg):
    """step_idx: [(индекс в масивите, стъпка)] → (вид, поле, ниво, индекс, url)."""
    base, hh = cfg["base_url"], run.hour
    jobs = []
    for k, s in step_idx:
        for f in cfg["single_level"]:
            jobs.append(("sl", f, None, k, f"{base}/{hh:02d}/{f}/{_fname_sl(run, s, f)}"))
        for f, levs in cfg["pressure_level"].items():
            for l in levs:
                jobs.append(("pl", f, l, k, f"{base}/{hh:02d}/{f}/{_fname_pl(run, s, l, f)}"))
    return jobs


def _extend(cached, run, steps, cfg, out_path):
    """Добавя към кеширания прогон стъпките от steps, които липсват (само в края)."""
    old = cached["steps"]
    new = [s for s in steps if s > old[-1]]
    if not new:                                   # липсват само по-ранни стъпки — не са нужни
        return cached
    res = cached["airports"]
    cells = {ic: tuple(a["cell"]) for ic, a in res.items()}
    nbc = {ic: [tuple(c) for c in a["nb"]["cells"]] for ic, a in res.items()}
    nb_fields = cfg.get("nb_fields", [])
    n0 = len(old)
    for a in res.values():
        for f in a["sl"]:
            a["sl"][f] += [None] * len(new)
        for levs in a["pl"].values():
            for l in levs:
                levs[l] += [None] * len(new)
        for f in nb_fields:
            a["nb"][f] += [None] * len(new)

    def put(kind, f, l, k, vals, nbv):
        if nbv is not None:
            for ic, lst in nbv.items():
                res[ic]["nb"][f][k] = lst
        for ic, v in vals.items():
            if kind == "sl":
                res[ic]["sl"][f][k] = v
            else:
                res[ic]["pl"][f][str(l)][k] = v

    jobs = _jobs(run, [(n0 + i, s) for i, s in enumerate(new)], cfg)
    logger.info(f"ICON {run:%Y-%m-%d %H}Z от кеша + {len(jobs)} файла за стъпки {new[0]}–{new[-1]}")
    with tempfile.TemporaryDirectory() as tmp:
        failed = _run_jobs(jobs, cells, nbc, nb_fields, cfg, tmp, put)
    if failed:
        logger.warning(f"ICON: {failed} от {len(jobs)} файла не се свалиха (полето липсва или мрежа)")
    cached["steps"] = old + new
    cached["valid"] = [(run + dt.timedelta(hours=s)).isoformat() for s in cached["steps"]]
    cached["fetched"] = dt.datetime.now(dt.timezone.utc).isoformat()
    cached["failed_files"] = cached.get("failed_files", 0) + failed
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cached, f, ensure_ascii=False)
    return cached


def fetch_icon(now, airports, cfg, horizon_h, cache_dir, cells_file):
    """Връща (път до JSON, dict). Ако run-ът вече е в кеша — само го чете."""
    os.makedirs(os.path.join(cache_dir, "icon"), exist_ok=True)
    run, steps = find_run(now, cfg, horizon_h)
    if run is None:
        raise RuntimeError("ICON-EU: няма пълен run в последните run-ове")
    out_path = os.path.join(cache_dir, "icon", f"icon_{run:%Y%m%d%H}.json")
    cached = None
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            cached = json.load(f)
        has_nb = all("nb" in a for a in cached.get("airports", {}).values())
        a0 = next(iter(cached.get("airports", {}).values()), {})
        same_fields = all(str(l) in a0.get("pl", {}).get(f, {}) for f, levs in cfg["pressure_level"].items() for l in levs)
        if (not (has_nb or not cfg.get("nb_fields")) or set(cached.get("airports", {})) != set(airports)
                or not same_fields):            # нови полета/нива в настройките → тегли се наново
            cached = None                       # стар формат → тегли се наново
        elif not [s for s in steps if s not in cached["steps"]]:
            logger.info(f"ICON {run:%Y-%m-%d %H}Z от кеша")
            return out_path, cached
    if cached is not None:
        # Същият прогон, но с нови часове в края (хоризонтът се мести с всеки час):
        # теглят се само липсващите стъпки и се добавят към кеша.
        return out_path, _extend(cached, run, steps, cfg, out_path)

    base, hh = cfg["base_url"], run.hour
    with tempfile.TemporaryDirectory() as tmp:
        # 1) Неизменните полета: дават мрежата, клетките, hsurf и fr_land
        url = f"{base}/{hh:02d}/hsurf/{_fname_ti(run, 'hsurf')}"
        r = _session.get(url, timeout=120)
        r.raise_for_status()
        p = os.path.join(tmp, "hsurf.grib2")
        with open(p, "wb") as f:
            f.write(bz2.decompress(r.content))
        lat_ax, lon_ax, hsurf = _read_grib(p)
        cells = load_cells(airports, cells_file, lat_ax, lon_ax)
        R = cfg.get("nb_radius", 2)
        nbc = {ic: [(i + di, j + dj) for di in range(-R, R + 1) for dj in range(-R, R + 1)]
               for ic, (i, j) in cells.items()}
        _, _, frl, frl_nb = _download_points(
            f"{base}/{hh:02d}/fr_land/{_fname_ti(run, 'fr_land')}", cells, tmp, nbc)
        nb_fields = cfg.get("nb_fields", [])

        res = {ic: {"cell": list(cells[ic]),
                    "cell_lat": float(lat_ax[cells[ic][0]]),
                    "cell_lon": float(lon_ax[cells[ic][1]]),
                    "hsurf": float(hsurf[cells[ic]]),
                    "fr_land": frl[ic],
                    "sl": {f: [None] * len(steps) for f in cfg["single_level"]},
                    "pl": {f: {str(l): [None] * len(steps) for l in levs}
                           for f, levs in cfg["pressure_level"].items()},
                    # Околност R×R: координати, маска (hsurf, fr_land) и полетата nb_fields
                    "nb": {"cells": [list(c) for c in nbc[ic]],
                           "lat": [float(lat_ax[c[0]]) for c in nbc[ic]],
                           "lon": [float(lon_ax[c[1]]) for c in nbc[ic]],
                           "hsurf": [float(hsurf[c]) for c in nbc[ic]],
                           "fr_land": frl_nb[ic],
                           **{f: [None] * len(steps) for f in nb_fields}}}
               for ic in airports}

        # 2) Сваляне на всички стъпки
        def put(kind, f, l, k, vals, nbv):
            if nbv is not None:
                for ic, lst in nbv.items():
                    res[ic]["nb"][f][k] = lst
            for ic, v in vals.items():
                if kind == "sl":
                    res[ic]["sl"][f][k] = v
                else:
                    res[ic]["pl"][f][str(l)][k] = v

        jobs = _jobs(run, list(enumerate(steps)), cfg)
        logger.info(f"ICON {run:%Y-%m-%d %H}Z: {len(jobs)} файла, стъпки {steps[0]}–{steps[-1]}")
        failed = _run_jobs(jobs, cells, nbc, nb_fields, cfg, tmp, put)
        if failed:
            logger.warning(f"ICON: {failed} от {len(jobs)} файла не се свалиха (полето липсва или мрежа)")

    out = {"run": run.isoformat(), "steps": steps,
           "valid": [(run + dt.timedelta(hours=s)).isoformat() for s in steps],
           "fetched": dt.datetime.now(dt.timezone.utc).isoformat(),
           "failed_files": failed, "airports": res}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out_path, out
