"""
Списък с летища и площадки за прогнозата по маршрут
===================================================
Източник: OurAirports (обществено достояние), airports.csv.
Взимат се действащите летища и площадки в България (без затворени,
хеликоптерни площадки и дубликати), без петте летища, които вече са в
route.html. „Най-известните“ (featured) — с код LB** и без военните
бази — излизат в списъка веднага; останалите — при търсене.

Записва масива между маркерите /* AIRFIELDS:BEGIN */ и /* AIRFIELDS:END */
в web/route.html.

Употреба:
  python tools/make_airfields.py                 # тегли airports.csv от GitHub
  python tools/make_airfields.py airports.csv    # от локален файл
"""

import csv, io, json, os, re, sys, urllib.request

URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
ROUTE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "route.html")
MAIN = {"LBSF", "LBBG", "LBWN", "LBPD", "LBGO"}
MILITARY = {"LBIA", "LBPG", "LBPL"}          # Безмер, Граф Игнатиево, Долна Митрополия — само при търсене

# Български имена на най-известните
BG = {
    "LBMV": "Малево", "LBTS": "Цалапица", "LBSW": "София Запад (Дрен)", "LBAM": "Алфа Метал (Габрово)",
    "LBBA": "Баховица", "LBBO": "Бохот", "LBBQ": "Бяла", "LBBZ": "Бъзън", "LBDA": "Даскал Атанасово",
    "LBDB": "Долна баня", "LBGI": "Горски извор", "LBGR": "Гривица", "LBHT": "Ихтиман", "LBKJ": "Кайнарджа",
    "LBKL": "Калвача", "LBLS": "Лесново", "LBMA": "Марица", "LBMO": "Монтана", "LBPR": "Приморско",
    "LBRD": "Ерден", "LBRS": "Русе (Щръклево)", "LBSB": "Слънчев бряг", "LBSL": "Сливен (Бършен)",
    "LBST": "Стряма (Баня)", "LBSZ": "Стара Загора", "LBVD": "Видин (Смърдан)", "LBVR": "Враца",
    "LBWB": "Балчик", "LBIA": "Безмер", "LBPG": "Граф Игнатиево", "LBPL": "Долна Митрополия",
    "LBZM": "Белозем", "LBBB": "Белчински бани", "LBBL": "Благоево", "LBBR": "Брезник", "LBDR": "Драгановци",
    "LBDM": "Две могили", "LBGD": "Градище", "LBPN": "Паничарево", "LBSP": "Сапарева баня", "LBSA": "Сливница",
    "LBSE": "Старо селище",
}


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    text = open(src, encoding="utf-8").read() if src else urllib.request.urlopen(URL, timeout=60).read().decode("utf-8")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if r["iso_country"] != "BG" or r["type"] not in ("small_airport", "medium_airport", "large_airport"):
            continue
        if "[Duplicate]" in r["name"]:
            continue
        code = next((c for c in (r["icao_code"], r["gps_code"]) if re.fullmatch(r"LB[A-Z]{2}", c or "")), r["ident"])
        if code in MAIN:
            continue
        en = re.sub(r"\s+(Airfield|Airstrip|Airport|Air Base|Highway Strip)$", "", r["name"]).strip()
        elev = round(float(r["elevation_ft"]) * 0.3048) if r["elevation_ft"] else None
        featured = 1 if re.fullmatch(r"LB[A-Z]{2}", code) and code not in MILITARY else 0
        out.append([code, BG.get(code, ""), en, round(float(r["latitude_deg"]), 4), round(float(r["longitude_deg"]), 4), elev, featured])
    out.sort(key=lambda x: (-x[6], x[1] or x[2]))
    js = "const AIRFIELDS = [   // [код, име БГ, име EN, lat, lon, височина m, най-известно]\n" \
         + ",\n".join("  " + json.dumps(x, ensure_ascii=False) for x in out) + "\n];"
    s = open(ROUTE, encoding="utf-8").read()
    s2 = re.sub(r"/\* AIRFIELDS:BEGIN \*/.*?/\* AIRFIELDS:END \*/",
                lambda m: f"/* AIRFIELDS:BEGIN */\n{js}\n/* AIRFIELDS:END */", s, flags=re.S)
    if s2 == s and "AIRFIELDS:BEGIN" not in s:
        sys.exit("Маркерите /* AIRFIELDS:BEGIN */ … /* AIRFIELDS:END */ липсват в route.html")
    open(ROUTE, "w", encoding="utf-8").write(s2)
    print(f"{len(out)} летища и площадки ({sum(x[6] for x in out)} най-известни) → {ROUTE}")


if __name__ == "__main__":
    main()
