import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.fetch.metar import parse_metar


def test_fog_vv():
    p = parse_metar("LBSF 280620Z VRB01KT 0300 FG VV002 04/04 Q1024 NOSIG")
    assert p["wind_dir"] == "VRB" and p["wind_kt"] == 1
    assert p["vis_m"] == 300 and p["wx"] == ["FG"]
    assert p["vv_ft"] == 200 and p["ceiling_ft"] == 200
    assert (p["temp_c"], p["dew_c"]) == (4, 4) and p["qnh_hpa"] == 1024


def test_cavok_negative_temp():
    p = parse_metar("METAR LBBG 280600Z 27008KT CAVOK M02/M05 Q1030=")
    assert p["cavok"] and p["vis_m"] == 10000 and p["ceiling_ft"] is None
    assert (p["temp_c"], p["dew_c"]) == (-2, -5)


def test_layers_gust_cb_trend_ignored():
    p = parse_metar("SPECI LBWN 281214Z 23015G28KT 200V260 4000 -TSRA FEW015 SCT025CB BKN040 "
                    "18/14 Q1008 TEMPO 2000 +TSRA BKN008")
    assert p["type"] == "SPECI"
    assert (p["wind_dir"], p["wind_kt"], p["gust_kt"]) == (230, 15, 28)
    assert p["wind_var"] == (200, 260)
    assert p["vis_m"] == 4000 and p["wx"] == ["-TSRA"]
    assert p["ceiling_ft"] == 4000 and p["cb_tcu"] == "CB"


def test_auto_missing_nsc_9999():
    p = parse_metar("LBGO 280550Z AUTO 00000KT 9999 // NCD 08/07 Q1022")
    assert p["auto"] and p["vis_m"] == 10000 and p["nsc"]
    assert "wx" in p["missing"] and p["ceiling_ft"] is None


def test_mps_and_br():
    p = parse_metar("LBPD 280600Z 09004MPS 1800 BR OVC003 05/05 Q1019")
    assert p["wind_kt"] == 8 and p["vis_m"] == 1800 and p["wx"] == ["BR"]
    assert p["ceiling_ft"] == 300


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
            print("OK", name)
