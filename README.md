# airport-nowcast

Летищен nowcasting за LBSF, LBBG, LBWN, LBPD и LBGO. Таблица 0–12 ч, която тръгва от METAR и преминава към моделите. **Експериментален продукт, не е TAF и не е за оперативна употреба.**

## Стъпка 1: сваляне на данните

| Източник | Модул | Бележка |
| --- | --- | --- |
| METAR / SPECI | `src/fetch/metar.py` | AWC Data API, собствен парсер |
| IFS HRES 9 km, GFS | `src/fetch/openmeteo.py` | Open-Meteo, една заявка на модел |
| ICON-EU | `src/fetch/icon_dwd.py` | DWD GRIB2, само при нов run, точки в `cache/icon/` |

## Първо пускане

```
conda env create -f environment.yml
conda activate airport-nowcast
python tests/test_metar.py
python run_fetch.py --no-icon
python choose_icon_cells.py
python choose_icon_cells.py --write
python run_fetch.py
```

`choose_icon_cells.py` показва клетките на ICON около всяко летище (fr_land, hsurf, разстояние) и предлага една. С `--write` я записва в `config/icon_cells.json`.

Изход: `cache/latest_inputs.json` + архивно копие в `archive/YYYY/MM/DD/HHMM.json.gz`.

## Работа в GitHub

- `main` — само код. `cache/`, `archive/`, `output/` не се качват.
- `.github/workflows/nowcast.yml` — пуска се от cron-job.org в :12 и :42 (workflow_dispatch),
  резервен вграден cron на всеки 2 ч. Тегли данните, смята таблицата, публикува
  `output/` в Pages (без commit) и добавя архива в клона `archive`.
- ICON точките се пазят между пусканията с `actions/cache`; нов прогон се тегли само при нужда.
- Клон `archive`: `YYYY/MM/DD/HHMM.json.gz` (входни), `table_HHMM.json.gz`, `icon/icon_YYYYMMDDHH.json.gz`.

Настройка: Settings → Pages → Source: **GitHub Actions**.
