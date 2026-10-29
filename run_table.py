"""
Стъпка 2: вятър, пориви и T/Td за петте летища
==============================================
Чете cache/latest_inputs.json (от run_fetch.py) и ICON JSON-а, смята
таблицата и записва output/table.json. Печата текстова таблица.

Употреба:  python run_table.py               (всички летища)
           python run_table.py LBSF          (едно летище)
"""

import os, sys, json
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config.settings as S
from src.process.series import from_icon, from_openmeteo
from src.process.wind_temp import compute
from src.process.phenomenon import compute as phenomenon
from src.process.phase_cols import compute as phase_cols, apply_phase_to_wx
from src.process.visibility import compute as visibility
from src.process.cloud import compute as cloud


def _metars(raw_list):
    out = []
    for m in raw_list or []:
        m = dict(m)
        if isinstance(m.get("time"), str):
            m["time"] = dt.datetime.fromisoformat(m["time"])
        out.append(m)
    return out


def load_inputs():
    with open(os.path.join(S.CACHE_DIR, "latest_inputs.json"), encoding="utf-8") as f:
        inp = json.load(f)
    icon = None
    if "icon" in inp:
        with open(os.path.join(S.CACHE_DIR, inp["icon"]["file"]), encoding="utf-8") as f:
            icon = json.load(f)
    return inp, icon


def build(inp, icon, now, icaos):
    nc0, fg0 = inp.get("nowcast") or {}, inp.get("fog") or {}
    res = {"created": now.isoformat(), "inputs_created": inp.get("created"),
           "icon_run": icon["run"] if icon else None,
           "meta": {
               "icon_run": icon["run"] if icon else None,
               "ifs": bool(inp.get("ifs")), "gfs": bool(inp.get("gfs")),
               "nowcast_radar": nc0.get("radar_time"), "nowcast_age_min": nc0.get("age_min"),
               "nowcast_stale": nc0.get("stale", True) if nc0 else True,
               "nowcast_method": nc0.get("method"),
               "fog_init": fg0.get("init"), "fog_stale": fg0.get("stale", True) if fg0 else True,
               "errors": inp.get("errors", {}),
           },
           "airports": {}}
    for icao in icaos:
        models = {}
        if icon:
            models["icon"] = from_icon(icon, icao)
        for k in ("ifs", "gfs"):
            if inp.get(k):
                models[k] = from_openmeteo(inp[k], icao)
        metars = _metars(inp.get("metar", {}).get(icao))
        a = compute(icao, metars, models, now, S)
        nc = inp.get("nowcast")
        cols = [dt.datetime.fromisoformat(x["time"]) for x in a["cols"]]
        ph = phenomenon(metars, (nc or {}).get("airports", {}).get(icao), nc, models, cols, now, S)
        a["phenomenon_source"] = ph["source"]
        for col, p in zip(a["cols"], ph["cols"]):
            col["wx"], col["wx_src"], col["rate"] = p["wx"], p["src"], p["rate"]
        phase_cols(models, a["cols"], metars, S.AIRPORTS[icao]["elev_m"])
        for col in a["cols"]:
            if col.get("wx_src") != "M":
                col["wx"] = apply_phase_to_wx(col["wx"], col.get("phase"))
        fog = inp.get("fog")
        fog_ap = (fog or {}).get("airports", {}).get(icao)
        fog_ok = bool(fog and not fog.get("stale") and fog_ap and "error" not in fog_ap)
        a["vis_info"] = visibility(icao, a["cols"], metars, models, fog_ap, fog_ok, S,
                                   ifs_nb=inp.get("ifs_nb"))
        cloud(a["cols"], metars, models,
              (nc or {}).get("airports", {}).get(icao) if (nc and not nc.get("stale")) else None, S)
        a["fog_model"] = None if not fog_ok else {
            "init": fog.get("init"), "summary": fog_ap.get("summary"), "flags": fog_ap.get("flags")}
        res["airports"][icao] = a
    return res


def print_table(a):
    cols = a["cols"]
    w = 7
    print(f"\n{a['icao']}  t0 {a['t0'][11:16]}Z  модели: {', '.join(a['models'])}"
          f"{'  СТАР METAR' if a['stale_metar'] else ''}")
    if a["metar_raw"]:
        print(f"  {a['metar_raw']}")
    c = a["correction"]
    print(f"  корекция: ΔS {c['ds']} kt, Δθ {c['dd']}°, ΔT {c['dt']}, ΔTd {c['dtd']}  "
          f"(METAR-и: {c['n_metar']})" + (f"  нулиране от {a['reset_at'][11:16]}Z" if a["reset_at"] else ""))
    print(f"{'':14}" + "".join(f"{x['time'][11:16]:>{w}}" for x in cols))
    print(f"{'':14}" + "".join(f"{x['lead']:>{w}}" for x in cols))

    def row(name, fn):
        print(f"{name:14}" + "".join(f"{fn(x):>{w}}" for x in cols))

    row("Вятър", lambda x: "—" if x["wind_kt"] is None else
        (f"VRB{x['wind_kt']:02d}" if x["wind_dir"] == "VRB" else f"{x['wind_dir']}/{x['wind_kt']:02d}"))
    row("Пориви kt", lambda x: "—" if x["gust_kt"] is None else str(x["gust_kt"]))
    row("T/Td °C", lambda x: "—" if x["t"] is None else f"{x['t']}/{x['td']}")
    row("Източник", lambda x: x["src"])
    row("Видимост m", lambda x: "—" if x.get("vis_m") is None else
        ("10km" if x["vis_m"] >= 10000 else str(x["vis_m"])))
    row("  източник", lambda x: x.get("vis_src") or "")
    row("P(<2000 m)", lambda x: "—" if x.get("p_lt2000") is None else f"{x['p_lt2000']}")
    row("  от", lambda x: (x.get("p_src2000") or "")[:6])
    row("P(<1000 m)", lambda x: "—" if x.get("p_lt1000") is None else f"{x['p_lt1000']}")
    row("  от", lambda x: (x.get("p_src1000") or "")[:6])
    row("Явление", lambda x: x.get("wx") or "—")
    row("  източник", lambda x: x.get("wx_src") or "")
    row("Таван ft", lambda x: x.get("ceiling_txt") or "—")
    row("  източник", lambda x: x.get("cloud_src") or "")
    row("Ниска обл.", lambda x: x.get("low_cloud") or "—")
    row("P(таван<1500)", lambda x: x.get("cig1500") or "—")
    row("P(таван<200)", lambda x: x.get("cig200") or "—")
    row("CB/TCU", lambda x: x.get("cb_tcu") or "—")
    row("Фаза (профил)", lambda x: x.get("phase") or "—")
    row("I ICON °C·m", lambda x: "—" if x.get("I_icon") is None else str(x["I_icon"]))
    row("P(обледяващ)", lambda x: x.get("icing") or "—")
    print(f"  nowcast източник: {a.get('phenomenon_source') or 'няма — само модели'}")
    vi = a.get("vis_info") or {}
    print(f"  околност: {vi.get('nb_points', 0)} точки след маската (суша, ±150 m)"
          + (f"; {vi['fog_note']}" if vi.get("fog_note") else ""))
    fm = a.get("fog_model")
    if fm:
        s, f = fm.get("summary") or {}, fm.get("flags") or {}
        on = [k for k, v in f.items() if v]
        print(f"  модел за мъгла (init {fm['init'][11:16]}Z): "
              f"{'МЪГЛА ' + (s.get('onset') or '')[11:16] + '–' + (s.get('end') or '')[11:16] + 'Z, мин ' + str(s.get('min_vis_m')) + ' m' if s.get('fog') else 'ясно'}"
              f"{'; флагове: ' + ', '.join(on) if on else ''}")
    else:
        print("  модел за мъгла: не се ползва (няма свеж fog.json)")


def main():
    icaos = [a.upper() for a in sys.argv[1:]] or S.ICAO_LIST
    now = dt.datetime.now(dt.timezone.utc)
    inp, icon = load_inputs()
    res = build(inp, icon, now, icaos)
    with open(os.path.join(S.OUTPUT_DIR, "table.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    # Архив на таблицата — за проверката срещу METAR
    import gzip
    d = os.path.join(S.ARCHIVE_DIR, f"{now:%Y}", f"{now:%m}", f"{now:%d}")
    os.makedirs(d, exist_ok=True)
    with gzip.open(os.path.join(d, f"table_{now:%H%M}.json.gz"), "wt", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    for icao in icaos:
        print_table(res["airports"][icao])


if __name__ == "__main__":
    main()
