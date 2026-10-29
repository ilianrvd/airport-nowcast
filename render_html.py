"""
Стъпка 7: HTML страница от output/table.json → output/index.html
================================================================
Самостоятелен файл (вграден CSS/JS). Дизайнът е одобреният: тъмна лента с
предупреждението, ред с ICAO кодовете, оцветени по най-лошото в следващите
6 ч, табове за летищата, карти (METAR, модели, nowcast/мъгла), таблица с
групи и легенда на един ред.

Употреба:  python render_html.py      → отвори output\\index.html в браузъра
"""

import os, sys, json, html
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config.settings as S

E = html.escape

# Цветове (фон, текст)
PAL = {"ok": ("transparent", "#14181F"), "none": ("transparent", "#6B7280"),
       "info": ("#DCE8F7", "#163A6B"), "warn": ("#FBE3B6", "#5C3A00"),
       "crit": ("#F4C7C1", "#6E160C")}
STATUS = {"red": ("#F4C7C1", "#6E160C", "червено"), "yellow": ("#FBE3B6", "#5C3A00", "жълто"),
          "green": ("#CDEBD3", "#14532D", "зелено")}


# ---------- класификация на клетките ----------
def k_vis(v):
    if v is None:
        return "none"
    return "crit" if v < 1000 else "warn" if v < 2000 else "info" if v < 5000 else "ok"


def k_cig(c):
    if c is None:
        return "ok"
    return "crit" if c < 200 else "warn" if c < 1500 else "info" if c < 5000 else "ok"


def k_wx(w):
    if not w:
        return "none"
    if any(x in w for x in ("FG", "TS", "FZ", "+")):
        return "crit"
    return "info"


def k_gust(g):
    return "warn" if g is not None and g >= 25 else "ok"


def k_p_crit(d):     # P(<1000), P(таван<200), обледяващ
    return {"ВИС": "crit", "УМЕР": "warn"}.get(d, "ok") if d else "none"


def k_p_warn(d):     # P(<2000), P(таван<1500)
    return {"ВИС": "warn", "УМЕР": "info"}.get(d, "ok") if d else "none"


def status(cols):
    """Най-лошото в колоните до +6 ч (вкл. METAR)."""
    lvl = "green"
    for c in cols[:13]:
        red = (k_vis(c.get("vis_m")) == "crit" or k_cig(c.get("ceiling_ft")) == "crit"
               or k_wx(c.get("wx")) == "crit" or c.get("fog1000") == "ВИС"
               or c.get("cig200") == "ВИС" or c.get("icing") == "ВИС")
        if red:
            return "red"
        yel = (k_vis(c.get("vis_m")) == "warn" or k_cig(c.get("ceiling_ft")) == "warn"
               or k_gust(c.get("gust_kt")) == "warn" or c.get("fog2000") == "ВИС"
               or c.get("cig1500") == "ВИС" or c.get("fog1000") == "УМЕР"
               or c.get("cig200") == "УМЕР" or c.get("icing") == "УМЕР"
               or (c.get("wx") and "SN" in c["wx"]))
        if yel:
            lvl = "yellow"
    return lvl


# ---------- текст на клетките ----------
def t_wind(c):
    if c.get("wind_kt") is None:
        return "—"
    return f"VRB{c['wind_kt']:02d}" if c["wind_dir"] == "VRB" else f"{c['wind_dir']}/{c['wind_kt']:02d}"


def t_vis(c):
    v = c.get("vis_m")
    return "—" if v is None else ("10 km" if v >= 10000 else str(v))


def t_src(c, k):
    if k == 0:
        return "M"
    s = []
    if "К" in (c.get("src"), c.get("cloud_src")):
        s.append("К")
    if c.get("vis_src") == "Ф":
        s.append("Ф")
    if c.get("wx_src") == "Н":
        s.append("Н")
    if c.get("wx_src") == "M":
        s.append("M")
    return "·".join(s) if s else "N"


ROWS = [
    ("ВЯТЪР", [
        ("Посока / скорост", "° / kt", t_wind, lambda c: "ok"),
        ("Пориви", "kt", lambda c: "—" if c.get("gust_kt") is None else str(c["gust_kt"]),
         lambda c: "none" if c.get("gust_kt") is None else k_gust(c["gust_kt"])),
    ]),
    ("ВИДИМОСТ", [
        ("Видимост", "m", t_vis, lambda c: k_vis(c.get("vis_m"))),
        ("P(< 2000 m)", "процедури", lambda c: c.get("fog2000") or "—", lambda c: k_p_warn(c.get("fog2000")),
         lambda c: _ptitle(c.get("p_lt2000"), c.get("p_src2000"))),
        ("P(< 1000 m)", "мъгла", lambda c: c.get("fog1000") or "—", lambda c: k_p_crit(c.get("fog1000")),
         lambda c: _ptitle(c.get("p_lt1000"), c.get("p_src1000"))),
    ]),
    ("ЯВЛЕНИЕ", [
        ("Явление", "", lambda c: c.get("wx") or "—", lambda c: k_wx(c.get("wx"))),
        ("P(обледяващ валеж)", "", lambda c: c.get("icing") or "—", lambda c: k_p_crit(c.get("icing"))),
    ]),
    ("ОБЛАЧНОСТ", [
        ("Таван", "ft", lambda c: c.get("ceiling_txt") or "—", lambda c: k_cig(c.get("ceiling_ft"))),
        ("Ниска облачност", "", lambda c: c.get("low_cloud") or "—", lambda c: "ok" if c.get("low_cloud") else "none"),
        ("P(таван < 1500 ft)", "", lambda c: c.get("cig1500") or "—", lambda c: k_p_warn(c.get("cig1500"))),
        ("P(таван < 200 ft)", "", lambda c: c.get("cig200") or "—", lambda c: k_p_crit(c.get("cig200"))),
        ("CB / TCU", "", lambda c: c.get("cb_tcu") or "—",
         lambda c: "crit" if c.get("cb_tcu") == "CB" else "warn" if c.get("cb_tcu") == "TCU" else "none"),
    ]),
    ("ТЕМПЕРАТУРА И ИЗТОЧНИК", [
        ("T / Td", "°C", lambda c: "—" if c.get("t") is None else f"{c['t']}/{c['td']}", lambda c: "ok"),
        ("Източник", "", None, lambda c: "none"),
    ]),
]


SRC_NAMES = {"METAR": "METAR (устойчивост)", "околност": "околност ~15 km (ICON + IFS)",
             "валеж": "видимост при валеж", "мъгла": "модел за мъгла", "река": "речен режим (LBGO)"}


def _ptitle(p, src):
    if p is None:
        return None
    return f"{p} % · " + (SRC_NAMES.get(src, src) if src else "няма сигнал")


def cell(text, kind, k, title=None):
    bg, fg = PAL[kind]
    cls = " now" if k == 0 else (" sep6" if k == 12 else "")
    t = f' title="{E(title)}"' if title else ""
    return f'<td class="v{cls}"{t} style="background:{bg};color:{fg}">{E(text)}</td>'


def table(a):
    cols = a["cols"]
    h = ['<table><thead><tr><th class="lab"></th>'
         '<th class="grp now">НАБЛЮДЕНИЕ</th><th class="grp sep6" colspan="12">0–6 ч · ПРЕЗ 30 МИН</th>'
         f'<th class="grp" colspan="{len(cols) - 13}">6–12 ч · ПРЕЗ 1 Ч</th></tr><tr><th class="lab">Елемент</th>']
    for k, c in enumerate(cols):
        cls = " now" if k == 0 else (" sep6" if k == 12 else "")
        h.append(f'<th class="t{cls}"><div class="hh">{c["time"][11:16]}</div><div class="ld">{E(c["lead"])}</div></th>')
    h.append("</tr></thead>")
    for gname, rows in ROWS:
        h.append(f'<tbody><tr><th class="gr" colspan="{len(cols) + 1}">{gname}</th></tr>')
        for row in rows:
            label, unit, ft, fk = row[:4]
            ftitle = row[4] if len(row) > 4 else None
            h.append(f'<tr><th class="lab" scope="row">{E(label)} <span class="u">{E(unit)}</span></th>')
            for k, c in enumerate(cols):
                txt = t_src(c, k) if ft is None else ft(c)
                h.append(cell(txt, fk(c), k, ftitle(c) if ftitle else None))
            h.append("</tr>")
        h.append("</tbody>")
    h.append("</table>")
    return "".join(h)


def age_txt(iso, now):
    if not iso:
        return "—"
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    m = int((now - t).total_seconds() // 60)
    return f"{t:%H}Z · {m // 60} ч" if m >= 60 else f"{t:%H:%M}Z · {m} мин"


def panel(icao, a, meta, now, active):
    name = S.AIRPORTS[icao]["name"]
    cols = a["cols"]
    fm = a.get("fog_model")
    if fm:
        s = fm.get("summary") or {}
        fog_txt = (f"мъгла {(s.get('onset') or '')[11:16]}–{(s.get('end') or '')[11:16]}Z, мин {s.get('min_vis_m')} m"
                   if s.get("fog") else "без мъгла") + f" · run {fm['init'][11:16]}Z"
        names = {"cf_collapse": "ICON сваля облачността", "cf_high": "плътна облачност в ICON",
                 "borderline": "гранична видимост", "saturated_start": "наситен старт",
                 "precip": "валеж в ICON", "river_regime": "речен режим"}
        flags = [names.get(k, k) for k, v in (fm.get("flags") or {}).items() if v]
        if flags:
            fog_txt += " · " + ", ".join(flags)
    else:
        fog_txt = "няма свеж fog.json (пуска се 18:15Z)"
    vi = a.get("vis_info") or {}
    if vi.get("fog_note"):
        fog_txt += " · " + vi["fog_note"]
    src = a.get("phenomenon_source")
    src_txt = {"radar_ro": "радар (Румъния)", "radar_iabg": "радар (ИАБГ)", "h40b": "H40B (сателит)",
               "tr34": "радар TR34", "icon_ltg": "ICON + мълнии", "icon": "ICON"}.get(src, "няма — само модели")
    nc = (f"{src_txt} · радар {meta['nowcast_radar'][11:16]}Z"
          if meta.get("nowcast_radar") and not meta.get("nowcast_stale") else src_txt)
    models = [f"ICON-EU {age_txt(meta.get('icon_run'), now)}"]
    models.append("IFS ✓" if meta.get("ifs") else "IFS —")
    models.append("GFS ✓" if meta.get("gfs") else "GFS —")
    stale = ' <span class="bad">СТАР</span>' if a.get("stale_metar") else ""
    return f"""
<section class="panel" id="p-{icao}" {'hidden' if not active else ''}>
  <div class="cards">
    <div class="card"><h2>{icao} {E(name)}</h2><code>{E(a.get('metar_raw') or 'няма METAR')}</code>{stale}</div>
    <div class="card"><h2>МОДЕЛИ</h2><div>{' · '.join(E(x) for x in models)}</div></div>
    <div class="card"><h2>ЯВЛЕНИЕ 0–6 Ч</h2><div>{E(nc)}</div></div>
    <div class="card"><h2>МОДЕЛ ЗА МЪГЛА</h2><div>{E(fog_txt)}</div></div>
  </div>
  <div class="tbl">{table(a)}</div>
</section>"""


CSS = """
:root{--ink:#14181F;--mut:#4B5563;--line:#E3E6EB;--bg:#F3F4F6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:'IBM Plex Sans',system-ui,sans-serif;font-feature-settings:"locl" 0}
header{background:#121821;color:#fff}
.bar{padding:12px 20px;display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;justify-content:space-between}
.brand{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.brand b{font-size:19px}
.warn{background:#F5C451;color:#3D2A00;font-size:12px;font-weight:700;padding:4px 9px;border-radius:4px;letter-spacing:.4px}
.upd{font-size:13px;color:#C9D1DC}.upd b{color:#fff;font-family:'IBM Plex Mono',monospace}
nav{background:#fff;border-bottom:1px solid #DDE1E7;padding:8px 20px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
nav button{font:inherit;border:2px solid transparent;border-radius:6px;min-width:104px;min-height:46px;padding:3px 12px;cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center}
nav button b{font-family:'IBM Plex Mono',monospace;font-size:15px}nav button span{font-size:12px}
nav button[aria-selected=true]{border-color:#121821}
nav .hint{font-size:12px;color:var(--mut)}
main{padding:14px 20px 30px;display:flex;flex-direction:column;gap:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-bottom:12px}
.card{background:#fff;border:1px solid #DDE1E7;border-radius:8px;padding:10px 14px;font-size:13px;line-height:1.45}
.card h2{margin:0 0 4px;font-size:12px;color:var(--mut);letter-spacing:.4px}
.card code{font-family:'IBM Plex Mono',monospace;font-size:12.5px}
.bad{background:#F4C7C1;color:#6E160C;font-size:11px;font-weight:700;padding:1px 6px;border-radius:3px}
.tbl{background:#fff;border:1px solid #DDE1E7;border-radius:8px;overflow-x:auto}
table{border-collapse:separate;border-spacing:0;width:100%;min-width:1100px}
th,td{border-bottom:1px solid var(--line)}
th.lab{position:sticky;left:0;z-index:1;background:#fff;text-align:left;padding:6px 10px;font-size:13px;font-weight:500;white-space:nowrap;min-width:150px}
thead th{background:#F8F9FB}thead th.lab{background:#F8F9FB;font-size:12px;color:var(--mut)}
th.grp{font-size:11px;font-weight:600;color:var(--mut);padding:5px 4px;letter-spacing:.3px}
th.t{padding:5px 3px;min-width:48px;border-bottom:2px solid #121821}
th.t .hh{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600}th.t .ld{font-size:11px;color:var(--mut)}
th.gr{text-align:left;padding:9px 10px 5px;font-size:12px;font-weight:700;color:#1F5FBF;letter-spacing:.5px;background:#fff}
td.v{padding:6px 4px;text-align:center;font-family:'IBM Plex Mono',monospace;font-size:13px;white-space:nowrap}
.now{border-right:2px solid #121821;font-weight:600}.sep6{border-right:2px solid #9AA3AF}
thead th.now{background:#EEF1F5}
.u{color:#6B7280;font-weight:400;font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12px;color:#374151;align-items:center}
.legend i{display:inline-block;width:14px;height:14px;border-radius:2px;vertical-align:-2px;margin-right:6px}
footer{font-size:12px;color:var(--mut);line-height:1.6}
"""

JS = """
const tabs=[...document.querySelectorAll('nav button')];
function show(id){tabs.forEach(b=>b.setAttribute('aria-selected',b.dataset.id===id));
document.querySelectorAll('.panel').forEach(p=>p.hidden=p.id!=='p-'+id);
try{localStorage.setItem('ap',id)}catch(e){}}
tabs.forEach(b=>b.addEventListener('click',()=>show(b.dataset.id)));
try{const s=localStorage.getItem('ap');if(s&&document.getElementById('p-'+s))show(s)}catch(e){}
"""


def main():
    with open(os.path.join(S.OUTPUT_DIR, "table.json"), encoding="utf-8") as f:
        T = json.load(f)
    now = dt.datetime.fromisoformat(T["created"])
    meta = T.get("meta", {})
    aps = T["airports"]
    first = next(iter(aps))

    nav = []
    for icao, a in aps.items():
        st = status(a["cols"])
        bg, fg, word = STATUS[st]
        nav.append(f'<button data-id="{icao}" aria-selected="{str(icao == first).lower()}" '
                   f'aria-label="{icao} {E(S.AIRPORTS[icao]["name"])}, условия: {word}" '
                   f'style="background:{bg};color:{fg}"><b>{icao}</b><span>{E(S.AIRPORTS[icao]["name"])}</span></button>')
    panels = "".join(panel(i, a, meta, now, i == first) for i, a in aps.items())
    nxt = now + dt.timedelta(minutes=30)

    page = f"""<!doctype html>
<html lang="bg"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="600">
<title>Летищен nowcasting</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<header><div class="bar"><div class="brand"><b>Летищен nowcasting</b>
<span class="warn">ЕКСПЕРИМЕНТАЛЕН · НЕ ЗА ОПЕРАТИВНА УПОТРЕБА</span></div>
<div class="upd">Обновено <b>{now:%H:%M} UTC</b> · следващо около <b>{nxt:%H:%M} UTC</b></div></div></header>
<nav aria-label="Летища">{''.join(nav)}<span class="hint">цвят = най-лошото в следващите 6 ч</span></nav>
<main>{panels}
<section class="legend" aria-label="Легенда">
<span><i style="background:#F4C7C1"></i>&lt; 1000 m · &lt; 200 ft · FG, TS, FZ</span>
<span><i style="background:#FBE3B6"></i>&lt; 2000 m · &lt; 1500 ft · порив ≥ 25 kt</span>
<span><i style="background:#DCE8F7"></i>&lt; 5000 m · &lt; 5000 ft · BR, RA, SN</span>
<span>ВИС / УМЕР / НИС: степен, до калибриране (посочи клетката за % и източник)</span>
<span>Източник: M METAR · К корекция · Ф мъгла · Н nowcast · N модели</span>
</section>
<footer>Експериментален продукт за обучение и проучване. Не е TAF и не замества официалните прогнози.
Източници: METAR (aviationweather.gov), ICON-EU (DWD), ECMWF IFS и GFS (Open-Meteo), nowcasting-public, модел за мъгла.</footer>
</main><script>{JS}</script></body></html>"""
    out = os.path.join(S.OUTPUT_DIR, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Записано: {out}")


if __name__ == "__main__":
    main()
