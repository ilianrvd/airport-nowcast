"""ICON при бавен DWD: срок, частичен файл, предишният прогон от кеша, продължаване."""
import os, sys, json, time
import datetime as dt
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import src.fetch.icon_dwd as I

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 27, 3, 42, tzinfo=UTC)
RUN = dt.datetime(2026, 11, 27, 0, tzinfo=UTC)
OLD = dt.datetime(2026, 11, 26, 21, tzinfo=UTC)
AIRPORTS = {"LBSF": {"lat": 42.7, "lon": 23.4}, "LBBG": {"lat": 42.6, "lon": 27.5}}
CFG = {"base_url": "https://example/icon-eu/grib", "run_every_h": 3, "max_lag_runs": 1,
       "cache_fallback_h": 9, "workers": 2, "max_tries": 3,
       "single_level": ["t_2m", "vis"], "nb_fields": ["vis"], "nb_radius": 0,
       "pressure_level": {"t": [850]}}
STEPS = [2, 3, 4]
FILES_PER_STEP = 3


def _setup(monkeypatch, tmp_path, steps=STEPS, slow=None, fail=None):
    """slow(url) → True: файлът не се сваля до срока; fail(url) → True: грешка."""
    monkeypatch.setattr(I, "find_run", lambda now, cfg, h: (RUN, list(steps)))
    monkeypatch.setattr(I, "_get", lambda url, deadline=None: __import__("bz2").compress(b"x"))
    lat, lon = np.array([42.6, 42.7]), np.array([23.4, 27.5])
    monkeypatch.setattr(I, "_read_grib", lambda p: (lat, lon, np.full((2, 2), 500.0)))
    monkeypatch.setattr(I, "load_cells", lambda a, f, la, lo: {"LBSF": (1, 0), "LBBG": (0, 1)})
    calls = []

    def fake(url, cells, tmpdir, nb=None, deadline=None):
        calls.append(url)
        if fail and fail(url):
            raise I.requests.HTTPError("404 Not Found")
        if slow and slow(url) and deadline is not None:
            raise I.DeadlineExceeded(url)
        vals = {ic: 1.0 for ic in cells}
        if nb is None:
            return None, None, vals
        return None, None, vals, {ic: [1.0] * len(l) for ic, l in nb.items()}

    monkeypatch.setattr(I, "_download_points", fake)
    os.makedirs(tmp_path / "icon", exist_ok=True)
    return calls


def _fetch(tmp_path):
    return I.fetch_icon(NOW, AIRPORTS, CFG, 12, str(tmp_path), "none.json",
                        deadline=time.monotonic() + 60)


def _write_old_run(tmp_path, monkeypatch):
    """Пълен прогон 21Z в кеша (свален нормално)."""
    monkeypatch.setattr(I, "find_run", lambda now, cfg, h: (OLD, [5, 6, 7]))
    p, d = _fetch(tmp_path)
    assert os.path.basename(p) == "icon_2026112621.json" and "missing" not in d


def test_slow_new_run_uses_cached_run_and_keeps_partial(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _write_old_run(tmp_path, monkeypatch)
    _setup(monkeypatch, tmp_path, slow=lambda u: "_004_" in u)   # последната стъпка не идва навреме
    p, d = _fetch(tmp_path)
    assert os.path.basename(p) == "icon_2026112621.json" and d["cache_fallback"]
    assert not os.path.exists(tmp_path / "icon" / "icon_2026112700.json")
    part = json.load(open(tmp_path / "icon" / "icon_2026112700.partial.json"))
    assert len(part["missing"]) == FILES_PER_STEP
    assert {m["step"] for m in part["missing"]} == {4}
    assert part["airports"]["LBSF"]["sl"]["t_2m"] == [1.0, 1.0, None]

    # Следващото пускане: DWD пак е бърз → тегли само липсващите 3 файла
    calls = _setup(monkeypatch, tmp_path)
    p, d = _fetch(tmp_path)
    assert os.path.basename(p) == "icon_2026112700.json" and not d.get("cache_fallback")
    assert len(calls) == FILES_PER_STEP
    assert not os.path.exists(tmp_path / "icon" / "icon_2026112700.partial.json")
    a = json.load(open(p))["airports"]["LBSF"]
    assert a["sl"]["t_2m"] == [1.0, 1.0, 1.0] and a["pl"]["t"]["850"] == [1.0, 1.0, 1.0]
    assert a["nb"]["vis"] == [[1.0], [1.0], [1.0]]


def test_failed_file_retried_then_given_up(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, fail=lambda u: "_003_VIS" in u)
    p, d = _fetch(tmp_path)
    assert os.path.basename(p) == "icon_2026112700.json"
    assert sum("_003_VIS" in u for u in calls) == CFG["max_tries"]
    assert d["failed_files"] == 1
    assert d["airports"]["LBSF"]["sl"]["vis"] == [1.0, None, 1.0]


def test_new_steps_late_keep_cached_steps(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _fetch(tmp_path)                                               # пълен 00Z, стъпки 2–4
    _setup(monkeypatch, tmp_path, steps=[3, 4, 5], slow=lambda u: "_005_" in u)
    p, d = _fetch(tmp_path)
    assert os.path.basename(p) == "icon_2026112700.json" and d["pending_steps"]
    assert d["steps"] == [2, 3, 4]
    part = json.load(open(tmp_path / "icon" / "icon_2026112700.partial.json"))
    assert part["steps"] == [2, 3, 4, 5] and {m["step"] for m in part["missing"]} == {5}


def test_no_cache_uses_partial(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, slow=lambda u: "_004_" in u)
    p, d = _fetch(tmp_path)
    assert p.endswith("icon_2026112700.partial.json") and d["partial"]


def test_network_error_waits_for_next_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, fail=lambda u: "_003_VIS" in u)
    monkeypatch.setattr(I, "_download_points", _wrap_timeout(I._download_points))
    p, d = _fetch(tmp_path)
    assert d["partial"]
    part = json.load(open(tmp_path / "icon" / "icon_2026112700.partial.json"))
    assert [(m["step"], m["field"], m["tries"]) for m in part["missing"]] == [(3, "vis", 1)]


def _wrap_timeout(fn):
    def w(url, *a, **k):
        try:
            return fn(url, *a, **k)
        except I.requests.HTTPError:
            raise I.requests.ConnectionError("read timeout")
    return w
