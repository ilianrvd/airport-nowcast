"""
airport-nowcast — конфигурация
==============================
Летищен nowcasting за петте международни летища. Експериментален продукт,
не е TAF. Спецификация: claude/летищен-nowcasting-спецификация.md в проекта.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
for _d in (CACHE_DIR, ARCHIVE_DIR):
    os.makedirs(_d, exist_ok=True)

# ------------------------------------------------------------
# Летища: ICAO → име, координати (°), височина на прага (m)
# ------------------------------------------------------------
AIRPORTS = {
    "LBSF": {"name": "София",        "lat": 42.6952, "lon": 23.4064, "elev_m": 531},
    "LBBG": {"name": "Бургас",       "lat": 42.5696, "lon": 27.5152, "elev_m": 41},
    "LBWN": {"name": "Варна",        "lat": 43.2321, "lon": 27.8251, "elev_m": 70},
    "LBPD": {"name": "Пловдив",      "lat": 42.0678, "lon": 24.8508, "elev_m": 182},
    "LBGO": {"name": "Г. Оряховица", "lat": 43.1514, "lon": 25.7129, "elev_m": 87},
}
ICAO_LIST = list(AIRPORTS)

# Хоризонт на таблицата
HORIZON_H = 12

# ------------------------------------------------------------
# METAR — AWC Data API
# ------------------------------------------------------------
METAR = {
    "url": "https://aviationweather.gov/api/data/metar",
    "hours": 3,          # колко часа назад (последните 2–3 съобщения + SPECI)
    "stale_min": 60,     # METAR по-стар от това → само модели, с флаг
}

# ------------------------------------------------------------
# nowcasting-public — airports.json (Pages)
# ------------------------------------------------------------
NOWCAST = {
    "url": "https://ilianrvd.github.io/nowcasting-public/embed/data/airports.json",
    "max_age_min": 30,
}

# ------------------------------------------------------------
# Модел за мъгла — fog.json (Pages), веднъж на нощ, init 18Z, 18Z → 06Z
# ------------------------------------------------------------
FOG = {
    "url": "https://ilianrvd.github.io/fog-model-bulgaria/data/fog.json",
    "max_age_h": 14,          # init по-стар от това → не се ползва
}

# ------------------------------------------------------------
# Open-Meteo — IFS HRES 9 km и GFS
# ------------------------------------------------------------
OPEN_METEO = {
    "url": "https://api.open-meteo.com/v1/forecast",
    "meta_url": "https://api.open-meteo.com/data/{domain}/static/meta.json",
    "past_hours": 3,
    "forecast_hours": HORIZON_H + 2,
    "models": {
        "ifs": {
            "model": "ecmwf_ifs",
            # прогонът (за водещото време от run-а) — първият отговорил meta.json
            "meta": ["ecmwf_ifs", "ecmwf_ifs025"],
            "hourly": [
                "temperature_2m", "dew_point_2m", "relative_humidity_2m",
                "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                "visibility", "cloud_cover", "cloud_cover_low",
                "weather_code", "precipitation", "snowfall",
            ],   # ecmwf_ifs в Open-Meteo не дава вятър по нива → 925/850 от ICON и GFS
        },
        "gfs": {
            "model": "gfs_seamless",
            "meta": ["ncep_gfs013", "ncep_gfs025"],     # seamless = GFS 0.11° (+HRRR в САЩ)
            "hourly": [
                "temperature_2m", "dew_point_2m",
                "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                "cloud_cover_low", "weather_code", "precipitation",
                "freezing_level_height",
                "categorical_freezing_rain",    # 0/1 — само в архива (флаг на модела за FZRA)
            ] + [f"{v}_{p}hPa" for p in (1000, 950, 925, 900, 850, 800, 700)
                 for v in ("temperature", "relative_humidity", "geopotential_height")]
              + [f"wind_{v}_{p}hPa" for p in (925, 850) for v in ("speed", "direction")],
        },
    },
}

# ------------------------------------------------------------
# ALADIN (CHMI, Central Europe 2.3 km). Влиза във вятъра (WEIGHTS["wind"]); останалото — в архива
# за сравнение с ICON. Run на 6 ч; в архива се записва веднъж на run, 72 ч напред.
# ------------------------------------------------------------
ALADIN = {
    "model": "chmi_aladin_central_europe_2km",
    "meta_url": "https://api.open-meteo.com/data/chmi_aladin_central_europe_2km/static/meta.json",
    "run_every_h": 6,
    "forecast_hours": 72,
    "cell_selection": "land",      # за LBWN: клетка на суша
    # Околност само за архива: (2R+1)² точки около летището през ~2.3 km (решетката на ALADIN),
    # cell_selection=nearest. Служи да се види дали съседни клетки хващат мъгла, която точката пропуска.
    "nb": {"radius": 1, "step_lat": 0.021, "step_lon": 0.028,
           "hourly": ["visibility", "wind_speed_10m", "cloud_cover_low"],
           "forecast_hours": 24, "timeout": 25},     # 13.11: със 72 ч и 90 s изтичаше всеки път (+90 s към пускането)
    "hourly": [
        "temperature_2m", "dew_point_2m", "relative_humidity_2m",
        "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "visibility", "cloud_cover", "cloud_cover_low",
        "precipitation", "rain", "snowfall", "showers", "cape", "weather_code",
    ] + [f"{v}_{p}hPa" for p in (1000, 950, 925, 850, 800, 700, 600)
         for v in ("temperature", "relative_humidity", "geopotential_height")]
      + [f"wind_{v}_{p}hPa" for p in (925, 850, 700) for v in ("speed", "direction")],
}

# ------------------------------------------------------------
# ICON-EU — DWD Open Data (GRIB2)
# ------------------------------------------------------------
ICON_DWD = {
    "base_url": "https://opendata.dwd.de/weather/nwp/icon-eu/grib",
    "run_every_h": 3,
    "max_lag_runs": 4,       # колко стари run-а да пробва
    "workers": 8,            # паралелни сваляния
    "single_level": [
        "u_10m", "v_10m", "vmax_10m", "t_2m", "td_2m", "vis", "ww",
        "ceiling", "clcl", "clct", "hbas_con", "htop_con", "cape_ml",
        "lpi_con_max", "tot_prec", "rain_gsp", "snow_gsp", "rain_con", "snow_con",
    ],
    "time_invariant": ["hsurf", "fr_land"],
    # Околност за вероятността за видимост: 5×5 клетки (~15 km при 0.0625°)
    "nb_radius": 2,
    "nb_fields": ["vis", "ceiling"],
    # Регулярната мрежа на ICON-EU (за координатите на околността без сваляне)
    "grid": {"lat0": 29.5, "lon0": -23.5, "d": 0.0625},
    # Профил за фазата (до 600 hPa) + вятър на 925/850/700 hPa
    "pressure_level": {
        "t":      [1000, 950, 925, 900, 850, 800, 700, 600],
        "relhum": [1000, 950, 925, 900, 850, 800, 700, 600],
        "fi":     [1000, 950, 925, 900, 850, 800, 700, 600],
        "u":      [925, 850, 700],     # 925/850 — за статистиката на вятъра
        "v":      [925, 850, 700],
    },
}

# ICON `ceiling`: над терена ("agl") или над морето ("msl").
# "msl" от 16.11.2026 (одит): архив 01–16.11, ICON срещу METAR при таван < 5000 ft —
# LBSF (hsurf 546 m, n 435): медианна разлика +506 m като "agl", −40 m като "msl"; и другите четири са по-добре с "msl".
# hbas_con / htop_con = −500 означава „няма конвекция“.
CEILING_REF = "msl"

# ------------------------------------------------------------
# Обработка: колони, тегла, корекции
# ------------------------------------------------------------
TIMELINE = {"step_min_0_6": 30, "step_h_6_12": 1, "horizon_h": HORIZON_H}

# Тегла на моделите по елемент (при липса се пренормират)
WEIGHTS = {
    # Вятър (скорост, посока, пориви): от 13.11.2026 ALADIN влиза със 0.4, GFS отпада (тегло 0).
    # ALADIN е най-добрият по скорост/посока в архива и единственият, видял фьона в LBSF (13.11).
    "wind": {"icon": 0.3, "ifs": 0.3, "aladin": 0.4, "gfs": 0.0},
    # Валеж (скорост на валежа) и ниска облачност: както преди (само ICON/IFS/GFS)
    "wx":   {"icon": 0.4, "ifs": 0.4, "gfs": 0.2},
    "temp": {"icon": 0.4, "ifs": 0.4, "gfs": 0.2},
    "vis":  {"icon": 0.5, "ifs": 0.5, "gfs": 0.0},
}

CORRECTION = {
    "tau_h": {"wind": 2.5, "temp": 2.5, "gust": 1.5},
    "metar_weights": [0.8, 0.15, 0.05],   # най-новият първи
    "max_metar_age_h": 2.0,               # по-стари METAR не влизат в грешката
    "speci_jump_dir": 60, "speci_jump_kt": 10, "speci_jump_t": 3,
    # нулиране при смяна на режима (ICON и IFS едновременно, в 2 ч прозорец)
    "reset_window_h": 2, "reset_dir": 60, "reset_dir_min_kt": 6, "reset_kt": 10,
}

WIND = {"vrb_below_kt": 3, "gust_show_diff_kt": 10,
        # Конвективен порив от nowcasting-public (20.11.2026, опит — оценява се по архива):
        # конвективно ехо ≥ dbz в 3/10 km в прогнозата на nowcast за времето на колоната (≤ lead_max_min)
        # или мълнии в 16 km (за първите ltg_max_min) → порив ≥ max(min_kt, w850_frac × вятър на 850 hPa).
        # w850_frac 0.7: LBSF 18.11 и 19→20.11 — METAR G22–33 при 850 hPa 36–48 kt (отношение ~0.7).
        # Записва се "gust_src": "Н" в колоната.
        "conv_gust": {"lead_max_min": 120, "dbz": 45, "ltg_r16": 1, "ltg_max_min": 30,
                      "min_kt": 25, "w850_frac": 0.7, "match_min": 15, "show_above_kt": 5},
        # Предупреждение за срез на вятъра (WS) — 20.11.2026:
        # • модели: векторна разлика 10 m → 850 hPa ≥ diff_kt (среднопретеглено по моделите с 850 hPa;
        #   само за летищата в model_icao — за останалите 850 hPa е твърде високо над терена).
        #   LBSF, ALADIN 0–9 ч, 01–20.11: при „WS ALL RWY“ в METAR медиана 30 kt, без WS 13 kt;
        #   ≥ 20 kt хваща 26 от 30 часа с WS (103 часа сигнал без WS в METAR).
        # • METAR с WS → задържа се metar_hold_h;
        # • конвекция по nowcast (същото условие като конвективния порив).
        "ws": {"diff_kt": 20, "model_icao": ["LBSF"], "metar_hold_h": 3}}

# Видимост
VIS = {
    "max_m": 10000,
    "tau_h": 3.0,                       # корекция от METAR (лог-скала)
    # Модел за мъгла като коректор
    "fog_shift_h": 2.0,                 # моделът подранява ~2 ч → кривата се измества напред
    "fog_spread_h": 1.0,                # несигурност на момента ± (рампа 2 × spread)
    "fog_w_max": 0.9,                   # максимално тегло на модела за мъгла
    "fog_w_max_coast": 0.6,             # LBWN, LBBG: морска константа надценява мъглата
    "coastal": ["LBWN", "LBBG"],
    "metar_dd_fast": 1.0,               # T−Td ≤ 1 и падаща видимост → рампата напред
    "metar_dd_slow": 3.0,               # T−Td ≥ 3 → рампата се задържа (първите 3 ч)
    # Видимост при валеж (m), по интензивност: слаб / умерен / силен
    "precip_vis": {"RA": [8000, 5000, 2500], "RASN": [4000, 2000, 1000],
                   "SN": [3000, 1200, 600], "FZRA": [6000, 3000, 1500],
                   "DZ": [5000, 3000, 1500]},
    # ICON ceiling ≈ 0 като сигнал за мъгла (по IMPROVER)
    "ceiling_fog_m": 60,          # ICON ceiling под това = облак до земята
    "nb_elev_m": 150,             # околност: само клетки с височина ±150 m от летището
    # Модел, който в t0 дава мъгла (< model_max_m в точката или ≥ nb_share от околността),
    # докато METAR е ≥ metar_min_m, играе с тегло `floor` до hold_h ч, после линейно до 1 при release_h.
    # Основание: архив 29.10–15.11 — 0% реална мъгла след такова противоречие (ICON/IFS/ALADIN).
    # ОДИТ при прегледа след ~2 седмици (виж документа със състоянието).
    "contra": {"metar_min_m": 5000, "model_max_m": 1000, "nb_share": 0.5,
               "floor": 0.2, "hold_h": 6, "release_h": 12,
               "wind_min_kt": 8, "dd_min": 2.0},   # само при вятър ≥ 8 kt ИЛИ T−Td ≥ 2 °C (не-мъглоопасни условия)
}

# Облачност
CLOUD = {
    "high_ft": 5000,          # над това само „висока“
    "cap_ft": 6000,           # таван за лог-корекцията (≥ това = „няма нисък таван“)
    "tau_h": 3.0,             # корекция от METAR (LAMP: устойчивостта е силна 2–3 ч)
    "tau_h_collapse": 6.0,    # ICON сваля облачността, а METAR е BKN/OVC → по-бавно
    "collapse_drop": 0.5,     # спад на clcl на ICON за 2 ч
    # METAR количество → % (за корекцията на ниската облачност)
    "cover_pct": {"FEW": 19, "SCT": 44, "BKN": 75, "OVC": 100},
    # % → количество (окти): < 6 нищо, 6–31 FEW, 31–56 SCT, 56–94 BKN, > 94 OVC
    "pct_bins": [(6, None), (31, "FEW"), (56, "SCT"), (94, "BKN"), (101, "OVC")],
    # CB/TCU от ICON: hbas_con / htop_con са над морето (−500 = няма конвекция)
    "conv_ref": "msl", "cb_top_m": 6000, "tcu_top_m": 3000, "cb_cape": 500,
}

# Вероятност за видимост: максимум от явни източници (%), до калибрирането
FOG_PROB = {
    "precip": 70,                 # видимост при валеж под прага
    "fog_2000": 50, "fog_1000": 35,   # модел за мъгла в областта си (верификация: ~49 % при 2000 m)
    "cf_collapse_factor": 0.6,    # ICON сваля облачността → фалшива мъгла
    "borderline_factor": 0.8,     # минималната видимост на модела е 900–1100 m (само за < 1000)
    "river": 25,                  # LBGO, речен режим, без валеж — долна граница
    "value": 50,                  # изчислената видимост в колоната е под прага → поне УМЕР
    "degree": {"high": 60, "mid": 30},   # ВИС ≥ 60 > УМЕР ≥ 30 > НИС
}

# Явление
PHENOMENON = {
    "radar_sources": ["radar_ro", "radar_iabg"],   # тези „виждат“ слабия валеж
    "match_min": 15,             # колона ↔ срок от airports.json (± мин)
    "ts_max_lead_min": 30,       # мълниите важат за сроковете 0 и +30 мин
    "metar_hold_h": 1.0,         # задържане на валежа от METAR при сателит/модел
    "dz_hold_h": 2.0,            # DZ от METAR се задържа и при радар (не го вижда)
    # интензивност на дъжда (mm/h, WMO): под rain_min → няма валеж
    "rain_min": 0.1, "rain_moderate": 2.5, "rain_heavy": 7.6,
    "conv_share": 0.5,           # дял конвективен валеж в ICON → SH
    "ts_ww": [95, 96, 97, 98, 99],
}

# Статична поправка по летище и час (B_stat). Празно до калибрирането.
STATIC_BIAS = {}

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Фиксирани клетки на ICON (i = индекс по ширина, j = по дължина).
# Попълва се от choose_icon_cells.py; празно → най-близката клетка.
ICON_CELLS_FILE = os.path.join(BASE_DIR, "config", "icon_cells.json")
