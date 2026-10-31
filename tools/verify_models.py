"""
Сравнение на моделите срещу METAR от клона archive
==================================================
За всяко архивирано пускане (на 30 мин) се взема прогнозата на всеки модел,
каквато е била налична в този момент (ICON и ALADIN по run-а във входните
данни, IFS и GFS от самите входни данни). Тя се сравнява с всеки METAR в
следващите 12 ч. Водещото време е от момента на пускането, не от run-а:
така ALADIN (run на 6 ч) и ICON (run на 3 ч) се мерят при еднакви условия,
както ги вижда таблицата.

Моделите се гледат сурови, без корекция от METAR.

Какво се смята (по модел × водещо време, общо и по летища):
  вятър     MAE и bias на скоростта; MAE на посоката при METAR ≥ 6 kt (без VRB)
  пориви    излишък: порив на модела − (G от METAR или средния вятър);
            P(порив ≥ 25 kt) като събитие срещу G ≥ 25 в METAR
  T / Td    MAE и bias
  видимост  < 5000 / 2000 / 1000 m като събитие (POD, FAR, CSI)
  ниска обл. BKN/OVC < 1500 ft в METAR срещу ниска облачност ≥ 56% в модела
            (приблизително: cloud_cover_low в Open-Meteo е до ~2 km);
            за ICON отделно и `ceiling` < 1500 ft
  валеж     има / няма в часа (модел ≥ 0.1 mm срещу валеж в METAR)
  фаза      при валеж и в двете: SN / RASN / RA / FZ (FZRA или FZDZ)
            от интеграла на мокрия термометър по профила на модела

Отделно:
  „Всички грешат посоката“ — METAR ≥ 6 kt, а ICON, IFS и GFS са с грешка
  > 60°. Колко пъти ALADIN е в рамките на 60° — и обратното: колко пъти
  само ALADIN греши, когато другите са прави. Брои се всеки METAR веднъж
  (при най-късото водещо време), плюс брой различни дни.
  С --hourly: bias на вятъра и T по час на денонощието (за B_stat).

Употреба (архивът е разархивиран или е git worktree на клона archive):
  git worktree add ../archive archive
  python tools/verify_models.py ../archive
  python tools/verify_models.py ../archive --from 2026-11-01 --to 2026-11-14 --icao LBSF
  python tools/verify_models.py ../archive --hourly --csv rows.csv.gz
"""

import os, sys, csv, glob, gzip, json, argparse, math
import datetime as dt
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config.settings as S
from src.process.series import from_icon, from_openmeteo, at, gust_at, uv_to_dirspd, profile_at
from src.process.phase import wet_bulb_from_td, wb_integral, classify, p_surface

MODELS = ("icon", "aladin", "ifs", "gfs")
LEADS = ((0, 3), (3, 6), (6, 12))
DIR_MIN_KT = 6
DIR_BAD = 60
GUST_THR = 25
LOW_FT = 1500
LOW_PCT = 56
VIS_THR = (5000, 2000, 1000)
PRECIP_MM = 0.1


def load_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def ts(s):
    return dt.datetime.fromisoformat(s)


def wdiff(a, b):
    return abs((a - b + 180) % 360 - 180)


def fin(x):
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


# ------------------------------------------------------------------
# METAR
# ------------------------------------------------------------------

def metar_phase(wx):
    """Фаза на валежа от METAR: FZ / SN / RASN / RA или None (без валеж)."""
    s = " ".join(wx)
    if not any(p in s for p in ("RA", "DZ", "SN", "SG", "PL", "GR", "GS", "UP")):
        return None
    if "FZRA" in s or "FZDZ" in s:
        return "FZ"
    snow = any(p in s for p in ("SN", "SG"))
    liquid = any(p in s for p in ("RA", "DZ"))
    if snow and liquid:
        return "RASN"
    if snow:
        return "SN"
    if liquid:
        return "RA"
    return "OTHER"


def low_cloud(m):
    """BKN/OVC или VV под 1500 ft. None, ако облачността липсва."""
    if m.get("cavok") or m.get("nsc"):
        return False
    if m.get("vv_ft") is not None:
        return m["vv_ft"] < LOW_FT
    if "clouds" in (m.get("missing") or []):
        return None
    return any(c["cover"] in ("BKN", "OVC") and c.get("base_ft") is not None
               and c["base_ft"] < LOW_FT for c in m.get("clouds", []))


def collect_metars(inputs):
    out = defaultdict(dict)
    for inp in inputs:
        for icao, lst in (inp.get("metar") or {}).items():
            for m in lst or []:
                if m.get("time"):
                    out[icao][m["time"]] = m
    return {icao: sorted(d.values(), key=lambda m: m["time"]) for icao, d in out.items()}


# ------------------------------------------------------------------
# Модели
# ------------------------------------------------------------------

class ModelStore:
    """Серии по модел и run, с кеш; ICON и ALADIN се четат от файловете на run-а."""

    def __init__(self, root):
        self.root = root
        self.cache = {}

    def _run_file(self, sub, ref):
        if not ref:
            return None
        base = os.path.basename(ref["file"]).replace(".json", "")
        path = os.path.join(self.root, sub, base + ".json.gz")
        if path not in self.cache:
            self.cache[path] = load_gz(path) if os.path.exists(path) else None
        return self.cache[path]

    def series(self, inp, icao):
        out = {}
        icon = self._run_file("icon", inp.get("icon"))
        if icon and icao in icon.get("airports", {}):
            out["icon"] = from_icon(icon, icao)
        ala = self._run_file("aladin", inp.get("aladin"))
        if ala and icao in ala.get("airports", {}):
            out["aladin"] = from_openmeteo(ala, icao)
        for k in ("ifs", "gfs"):
            if inp.get(k) and icao in inp[k].get("airports", {}):
                out[k] = from_openmeteo(inp[k], icao)
        return out


def precip_hour(s, t):
    """Валеж (mm) за часа, който съдържа момента t: стойността в края на часа."""
    if "precip" not in s:
        return float("nan")
    end = math.ceil(t / 3600) * 3600
    k = np.where(np.abs(s["t"] - end) < 60)[0]
    return float(s["precip"][k[0]]) if len(k) else float("nan")


def model_phase(s, t, z_sfc):
    levels = profile_at(s, t)
    t2, td = at(s, "t2m", t), at(s, "td2m", t)
    if not levels or not (math.isfinite(t2) and math.isfinite(td)):
        return None
    tw = wet_bulb_from_td(t2, min(td, t2), p_surface(z_sfc))
    I, _ = wb_integral(levels, z_sfc, tw)
    ph = classify(I, t2)
    return "FZ" if ph == "FZRA" else ph


def model_values(key, s, t, z_sfc):
    u, v = at(s, "u", t), at(s, "v", t)
    d, spd = uv_to_dirspd(u, v) if math.isfinite(u) and math.isfinite(v) else (float("nan"), float("nan"))
    r = {"dir": d, "spd": spd, "gust": gust_at(s, t) if "gust" in s else float("nan"),
         "t2m": at(s, "t2m", t), "td2m": at(s, "td2m", t),
         "vis": at(s, "vis", t) if "vis" in s else float("nan"),
         "clcl": at(s, "clcl", t) if "clcl" in s else float("nan"),
         "precip": precip_hour(s, t)}
    if key == "icon":
        c = at(s, "ceiling", t)
        if math.isfinite(c):
            c_ft = (c - (s["hsurf"] if S.CEILING_REF == "msl" else 0)) / 0.3048
            r["ceil_ft"] = c_ft
    r["phase"] = model_phase(s, t, z_sfc) if (s.get("profile") and fin(r["precip"])
                                             and r["precip"] >= PRECIP_MM) else None
    return r


# ------------------------------------------------------------------
# Натрупване
# ------------------------------------------------------------------

def lead_bin(h):
    for a, b in LEADS:
        if a < h <= b:
            return f"{a}-{b}"
    return None


class Acc:
    def __init__(self):
        self.err = defaultdict(list)          # (модел, елемент, водещо, летище) → грешки
        self.ct = defaultdict(lambda: [0, 0, 0, 0])   # H M F C
        self.phase = defaultdict(lambda: defaultdict(int))
        self.hourly = defaultdict(list)

    def e(self, m, el, lb, icao, x):
        if fin(x):
            self.err[(m, el, lb, icao)].append(x)

    def c(self, m, el, lb, icao, fc, ob):
        if fc is None or ob is None:
            return
        k = 0 if fc and ob else 1 if ob else 2 if fc else 3
        self.ct[(m, el, lb, icao)][k] += 1


def compare(acc, key, mv, m, lb, icao, hourly):
    obs_dir, obs_spd = m.get("wind_dir"), m.get("wind_kt")
    if fin(obs_spd) and fin(mv["spd"]):
        acc.e(key, "spd_err", lb, icao, mv["spd"] - obs_spd)
        if hourly:
            acc.hourly[(key, "spd", icao, ts(m["time"]).hour)].append(mv["spd"] - obs_spd)
    if isinstance(obs_dir, int) and fin(obs_spd) and obs_spd >= DIR_MIN_KT and fin(mv["dir"]):
        acc.e(key, "dir_err", lb, icao, wdiff(mv["dir"], obs_dir))
    if fin(mv["gust"]) and fin(obs_spd):
        ref = m.get("gust_kt") or obs_spd
        acc.e(key, "gust_excess", lb, icao, mv["gust"] - ref)
        acc.c(key, f"gust>={GUST_THR}", lb, icao, mv["gust"] >= GUST_THR,
              (m.get("gust_kt") or 0) >= GUST_THR)
    for el, mk, ok in (("t2m", "t2m", "temp_c"), ("td2m", "td2m", "dew_c")):
        if fin(m.get(ok)) and fin(mv[mk]):
            acc.e(key, el + "_err", lb, icao, mv[mk] - m[ok])
            if hourly and el == "t2m":
                acc.hourly[(key, "t2m", icao, ts(m["time"]).hour)].append(mv[mk] - m[ok])
    vis_o = 10000 if m.get("cavok") else m.get("vis_m")
    if fin(vis_o) and fin(mv["vis"]):
        for thr in VIS_THR:
            acc.c(key, f"vis<{thr}", lb, icao, mv["vis"] < thr, vis_o < thr)
    lc = low_cloud(m)
    if fin(mv["clcl"]):
        acc.c(key, f"low>={LOW_PCT}%", lb, icao, mv["clcl"] >= LOW_PCT, lc)
    if "ceil_ft" in mv:
        acc.c(key, f"ceil<{LOW_FT}ft", lb, icao, mv["ceil_ft"] < LOW_FT, lc)
    ph_o = metar_phase(m.get("wx") or [])
    if fin(mv["precip"]):
        acc.c(key, "precip", lb, icao, mv["precip"] >= PRECIP_MM, ph_o is not None)
        if ph_o and mv["phase"]:
            acc.phase[(key, lb)][(ph_o, mv["phase"])] += 1


# ------------------------------------------------------------------
# Отчет
# ------------------------------------------------------------------

def scores(c):
    H, M, F, C = c
    pod = H / (H + M) if H + M else float("nan")
    far = F / (H + F) if H + F else float("nan")
    csi = H / (H + M + F) if H + M + F else float("nan")
    return pod, far, csi


def fmt(x, n=1):
    return "—" if not fin(x) else f"{x:.{n}f}"


def report(acc, icaos, models):
    lbs = [f"{a}-{b}" for a, b in LEADS]
    for scope in ["ВСИЧКИ"] + icaos:
        sel = icaos if scope == "ВСИЧКИ" else [scope]
        print(f"\n{'=' * 78}\n{scope}\n{'=' * 78}")
        print(f"{'':22}" + "".join(f"{m:>14}" for m in models))
        for lb in lbs:
            print(f"--- водещо {lb} ч")
            for el, lab, f in (("spd_err", "скорост MAE / bias", "mb"),
                               ("dir_err", f"посока MAE (≥{DIR_MIN_KT} kt)", "m"),
                               ("gust_excess", "порив излишък", "mb"),
                               ("t2m_err", "T MAE / bias", "mb"),
                               ("td2m_err", "Td MAE / bias", "mb")):
                row = f"{lab:22}"
                for m in models:
                    xs = sum((acc.err.get((m, el, lb, i), []) for i in sel), [])
                    if not xs:
                        row += f"{'—':>14}"
                    elif f == "m":
                        row += f"{fmt(np.mean(xs), 0)}° n{len(xs)}".rjust(14)
                    else:
                        row += f"{fmt(np.mean(np.abs(xs)))}/{fmt(np.mean(xs))}".rjust(14)
                print(row)
            for el in ([f"gust>={GUST_THR}"] + [f"vis<{t}" for t in VIS_THR]
                       + [f"low>={LOW_PCT}%", f"ceil<{LOW_FT}ft", "precip"]):
                row = f"{el + ' POD/FAR':22}"
                any_ = False
                for m in models:
                    c = [0, 0, 0, 0]
                    for i in sel:
                        for k, v in enumerate(acc.ct.get((m, el, lb, i), [0, 0, 0, 0])):
                            c[k] += v
                    if sum(c) == 0:
                        row += f"{'—':>14}"
                        continue
                    any_ = True
                    pod, far, _ = scores(c)
                    row += f"{fmt(pod, 2)}/{fmt(far, 2)} ({c[0] + c[1]})".rjust(14)
                if any_:
                    print(row)
    print("\n(POD/FAR, в скоби броят на наблюдаваните събития)")

    print(f"\n{'=' * 78}\nФаза при валеж и в двете (METAR → модел), всички летища\n{'=' * 78}")
    cats = ("SN", "RASN", "RA", "FZ")
    for m in models:
        for lb in lbs:
            tab = acc.phase.get((m, lb))
            if not tab:
                continue
            tot = sum(tab.values())
            ok = sum(v for (o, f), v in tab.items() if o == f)
            print(f"{m:7} {lb:>5} ч: вярно {ok}/{tot}  "
                  + "  ".join(f"{o}→{f}:{v}" for (o, f), v in sorted(tab.items()) if o != f))


def consensus_report(cons):
    print(f"\n{'=' * 78}\nПосока: всички грешат (ICON, IFS, GFS > {DIR_BAD}°, METAR ≥ {DIR_MIN_KT} kt)\n{'=' * 78}")
    if not cons:
        print("Няма случаи с ALADIN в архива.")
        return
    tab = defaultdict(int)
    days = defaultdict(set)
    for (icao, t), (others_bad, ala_bad) in cons.items():
        tab[(others_bad, ala_bad)] += 1
        days[(others_bad, ala_bad)].add((icao, t[:10]))
    lines = (((True, False), "другите грешат, ALADIN познава"),
             ((True, True), "всички грешат"),
             ((False, True), "другите познават, ALADIN греши"),
             ((False, False), "всички познават"))
    for k, lab in lines:
        print(f"{lab:34} {tab[k]:5} METAR-а, {len(days[k]):3} летище-дни")
    a, b = tab[(True, False)], tab[(True, True)]
    if a + b:
        print(f"\nКогато ICON, IFS и GFS грешат, ALADIN познава в {100 * a / (a + b):.0f}% "
              f"({a} от {a + b}).")


def hourly_report(acc, models, icaos):
    print(f"\n{'=' * 78}\nBias по час на денонощието (UTC): скорост kt / T °C\n{'=' * 78}")
    for icao in icaos:
        print(f"\n{icao}")
        print("час  " + "".join(f"{m:>14}" for m in models))
        for h in range(24):
            row = f"{h:02d}   "
            for m in models:
                s = acc.hourly.get((m, "spd", icao, h), [])
                t = acc.hourly.get((m, "t2m", icao, h), [])
                row += (f"{fmt(np.mean(s)) if s else '—'}/{fmt(np.mean(t)) if t else '—'}").rjust(14)
            print(row)


# ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive", help="папка с разархивирания клон archive")
    ap.add_argument("--from", dest="d0", default=None, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="d1", default=None, help="YYYY-MM-DD (включително)")
    ap.add_argument("--icao", default=None)
    ap.add_argument("--hourly", action="store_true")
    ap.add_argument("--csv", default=None, help="всички сравнения ред по ред (.csv или .csv.gz)")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.archive, "20??", "??", "??", "[0-9][0-9][0-9][0-9].json.gz")))
    def day(p):
        parts = p.replace("\\", "/").split("/")
        return "-".join(parts[-4:-1])
    if args.d0:
        files = [p for p in files if day(p) >= args.d0]
    if args.d1:
        files = [p for p in files if day(p) <= args.d1]
    if not files:
        sys.exit("Няма входни файлове в архива за този период.")
    print(f"{len(files)} архивирани пускания, {day(files[0])} … {day(files[-1])}")

    inputs = [load_gz(p) for p in files]
    metars = collect_metars(inputs)
    icaos = [args.icao.upper()] if args.icao else [i for i in S.ICAO_LIST if i in metars]
    store = ModelStore(args.archive)
    acc = Acc()
    cons = {}            # (icao, време на METAR) → (другите грешат, ALADIN греши), при най-късо водещо
    cons_lead = {}
    rows = []
    present = set()

    for inp in inputs:
        t0 = ts(inp["created"])
        for icao in icaos:
            ser = store.series(inp, icao)
            present.update(ser)
            z = S.AIRPORTS[icao]["elev_m"]
            for m in metars.get(icao, []):
                tm = ts(m["time"])
                h = (tm - t0).total_seconds() / 3600
                lb = lead_bin(h)
                if lb is None:
                    continue
                t = tm.timestamp()
                mvs = {k: model_values(k, s, t, z) for k, s in ser.items()}
                for k, mv in mvs.items():
                    compare(acc, k, mv, m, lb, icao, args.hourly)
                    if args.csv:
                        rows.append({"run_time": inp["created"], "icao": icao, "metar_time": m["time"],
                                     "lead_h": round(h, 2), "model": k,
                                     **{f"m_{x}": (None if not fin(v) else round(v, 2)) if not isinstance(v, str) else v
                                        for x, v in mv.items()},
                                     "o_dir": m.get("wind_dir"), "o_spd": m.get("wind_kt"),
                                     "o_gust": m.get("gust_kt"), "o_t": m.get("temp_c"),
                                     "o_td": m.get("dew_c"), "o_vis": m.get("vis_m"),
                                     "o_ceil_ft": m.get("ceiling_ft"), "o_wx": " ".join(m.get("wx") or [])})
                # Посока: всички грешат?
                od, os_ = m.get("wind_dir"), m.get("wind_kt")
                if isinstance(od, int) and fin(os_) and os_ >= DIR_MIN_KT and "aladin" in mvs \
                        and all(k in mvs and fin(mvs[k]["dir"]) for k in ("icon", "ifs", "gfs", "aladin")):
                    key = (icao, m["time"])
                    if key not in cons_lead or h < cons_lead[key]:
                        cons_lead[key] = h
                        others = all(wdiff(mvs[k]["dir"], od) > DIR_BAD for k in ("icon", "ifs", "gfs"))
                        others_ok = all(wdiff(mvs[k]["dir"], od) <= DIR_BAD for k in ("icon", "ifs", "gfs"))
                        ala = wdiff(mvs["aladin"]["dir"], od) > DIR_BAD
                        if others or others_ok:
                            cons[key] = (others, ala)
                        else:
                            cons.pop(key, None)

    models = [m for m in MODELS if m in present]
    report(acc, icaos, models)
    consensus_report(cons)
    if args.hourly:
        hourly_report(acc, models, icaos)

    if args.csv and rows:
        keys = sorted({k for r in rows for k in r})
        op = gzip.open if args.csv.endswith(".gz") else open
        with op(args.csv, "wt", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print(f"\n{len(rows)} реда → {args.csv}")


if __name__ == "__main__":
    main()
