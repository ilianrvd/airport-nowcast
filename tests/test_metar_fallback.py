import os, sys
import datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import src.fetch.metar as M

NOW = dt.datetime(2026, 11, 26, 6, 42, tzinfo=dt.timezone.utc)
FB_URL = "https://example/obs/metar_latest.json"
AWC_URL = "https://example/awc"


def _awc_rec(ic, hhmm, raw_tail):
    t = NOW.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]))
    return {"icaoId": ic, "obsTime": int(t.timestamp()), "metarType": "METAR",
            "rawOb": f"METAR {ic} 26{hhmm}Z {raw_tail}"}


def _fb_rep(ic, hhmm, raw_tail):
    return {"id": ic, "time": f"2026-11-26T{hhmm[:2]}:{hhmm[2:]}:00Z", "t": 99, "vis_m": 1,
            "raw": f"METAR {ic} 26{hhmm}Z {raw_tail}="}


class _R:
    def __init__(self, data=None, exc=None, status=200):
        self.data, self.exc, self.status_code, self.content = data, exc, status, b"x"

    def raise_for_status(self):
        if self.exc:
            raise self.exc

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def _patch(monkeypatch, awc, fb):
    calls = {"fb": 0}

    def get(url, params=None, timeout=None, headers=None):
        if url == FB_URL:
            calls["fb"] += 1
            assert "t" in params                      # заобикаля кеша на Pages
            return fb() if callable(fb) else _R(fb)
        return awc() if callable(awc) else _R(awc)
    monkeypatch.setattr(M.requests, "get", get)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)

    class D(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(M.dt, "datetime", D)
    return calls


def test_awc_fresh_no_fallback(monkeypatch):
    awc = [_awc_rec(ic, "0630", "VRB02KT CAVOK 01/M03 Q1025") for ic in ("LBSF", "LBBG")]
    calls = _patch(monkeypatch, awc, {"reports": []})
    out = M.fetch_metars(["LBSF", "LBBG"], AWC_URL, fallback_url=FB_URL)
    assert calls["fb"] == 0
    assert out["LBSF"][0]["src"] == "awc"


def test_missing_and_late_taken_from_fallback(monkeypatch):
    awc = [_awc_rec("LBSF", "0630", "VRB02KT CAVOK 01/M03 Q1025"),
           _awc_rec("LBBG", "0500", "27008KT 9999 FEW030 05/01 Q1020")]     # LBBG стар, LBWN липсва
    fb = {"reports": [_fb_rep("LBBG", "0600", "27010KT 9999 SCT030 05/01 Q1020"),
                      _fb_rep("LBBG", "0630", "27012KT 8000 -RA BKN020 05/03 Q1019"),
                      _fb_rep("LBWN", "0630", "09005KT 0800 FG VV002 03/03 Q1022"),
                      _fb_rep("LBWN", "0100", "09005KT 9999 NSC 03/01 Q1022")]}      # извън 3 ч
    calls = _patch(monkeypatch, awc, fb)
    out = M.fetch_metars(["LBSF", "LBBG", "LBWN"], AWC_URL, fallback_url=FB_URL)
    assert calls["fb"] == 1
    assert out["LBSF"][0]["src"] == "awc"
    b = out["LBBG"]
    assert [p["src"] for p in b] == ["nowcasting-public", "nowcasting-public", "awc"]
    assert b[0]["wx"] == ["-RA"] and b[0]["vis_m"] == 8000 and b[0]["temp_c"] == 5   # от raw, не от t/vis_m
    w = out["LBWN"]
    assert len(w) == 1 and w[0]["vis_m"] == 800 and w[0]["raw"].endswith("Q1022")


def test_fallback_not_newer_kept_awc(monkeypatch):
    awc = [_awc_rec("LBSF", "0530", "VRB02KT CAVOK 01/M03 Q1025")]
    fb = {"reports": [_fb_rep("LBSF", "0530", "VRB02KT CAVOK 01/M03 Q1025")]}
    _patch(monkeypatch, awc, fb)
    out = M.fetch_metars(["LBSF"], AWC_URL, fallback_url=FB_URL)
    assert len(out["LBSF"]) == 1 and out["LBSF"][0]["src"] == "awc"


def test_awc_down_fallback_bad_json_no_crash(monkeypatch):
    def awc():
        raise M.requests.ConnectionError("down")
    _patch(monkeypatch, awc, lambda: _R(ValueError("bad json")))
    out = M.fetch_metars(["LBSF"], AWC_URL, fallback_url=FB_URL)
    assert out == {"LBSF": []}


def test_awc_down_fallback_404(monkeypatch):
    awc = lambda: _R(exc=M.requests.ConnectionError("down"))
    _patch(monkeypatch, awc, lambda: _R(exc=M.requests.HTTPError("404")))
    assert M.fetch_metars(["LBSF"], AWC_URL, fallback_url=FB_URL) == {"LBSF": []}


def test_awc_down_fallback_ok(monkeypatch):
    awc = lambda: _R(exc=M.requests.ConnectionError("down"))
    _patch(monkeypatch, awc, {"reports": [_fb_rep("LBSF", "0630", "VRB02KT CAVOK 01/M03 Q1025")]})
    out = M.fetch_metars(["LBSF"], AWC_URL, fallback_url=FB_URL)
    assert out["LBSF"][0]["src"] == "nowcasting-public" and out["LBSF"][0]["cavok"]
