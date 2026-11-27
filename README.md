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
  `output/` в Pages (без commit) и добавя архива в клона на месеца `archive-YYYY-MM`.
- ICON точките се пазят между пусканията с `actions/cache`; нов прогон се тегли само при нужда.
- Срок за ICON: 6 мин на пускане (`ICON_DWD["budget_s"]`). Ако DWD е бавен и новият прогон не се свали навреме, изтеглената част остава в `cache/icon/icon_YYYYMMDDHH.partial.json`, следващото пускане тегли само липсващите файлове, а дотогава таблицата ползва предишния пълен прогон от кеша (`meta.icon_fallback: true` в `table.json`). Неуспешен файл се пробва до 3 пъти.
- Клонове `archive-YYYY-MM` (по един на месец; `archive-2026-11` започва от 29.10, старият клон `archive` е до 03.11):
  `YYYY/MM/DD/HHMM.json.gz` (входни; IFS и GFS с `run` — началото на прогона), `table_HHMM.json.gz`,
  `icon/icon_YYYYMMDDHH.json.gz`, `aladin/aladin_YYYYMMDDHH.json.gz`. Пускането клонира само текущия месец.
- Сравнение с METAR: `tools/verify_models.py ../arch-2026-11 [../arch-2026-12 …]` (git worktree на всеки месец).

Настройка: Settings → Pages → Source: **GitHub Actions**.
