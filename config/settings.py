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
    "past_hours": 3,
    "forecast_hours": HORIZON_H + 2,
    "models": {
        "ifs": {
            "model": "ecmwf_ifs",
            "hourly": [
                "temperature_2m", "dew_point_2m", "relative_humidity_2m",
                "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                "visibility", "cloud_cover", "cloud_cover_low",
                "weather_code", "precipitation", "snowfall",
            ],
        },
        "gfs": {
            "model": "gfs_seamless",
            "hourly": [
                "temperature_2m", "dew_point_2m",
                "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                "cloud_cover_low", "weather_code", "precipitation",
                "freezing_level_height",
            ] + [f"{v}_{p}hPa" for p in (1000, 950, 925, 900, 850, 800, 700)
                 for v in ("temperature", "relative_humidity", "geopotential_height")],
        },
    },
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
    # Профил за фазата (до 600 hPa) + вятър на 700 hPa за CB поривите
    "pressure_level": {
        "t":      [1000, 950, 925, 900, 850, 800, 700, 600],
        "relhum": [1000, 950, 925, 900, 850, 800, 700, 600],
        "fi":     [1000, 950, 925, 900, 850, 800, 700, 600],
        "u":      [700],
        "v":      [700],
    },
}

# ICON `ceiling`: над терена ("agl") или над морето ("msl").
# Временно "agl" (LBSF и LBWN на 28.10 сочат натам); решава се по архива.
# hbas_con / htop_con = −500 означава „няма конвекция“.
CEILING_REF = "agl"

# ------------------------------------------------------------
# Обработка: колони, тегла, корекции
# ------------------------------------------------------------
TIMELINE = {"step_min_0_6": 30, "step_h_6_12": 1, "horizon_h": HORIZON_H}

# Тегла на моделите по елемент (при липса се пренормират)
WEIGHTS = {
    "wind": {"icon": 0.4, "ifs": 0.4, "gfs": 0.2},
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

WIND = {"vrb_below_kt": 3, "gust_show_diff_kt": 10}

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
