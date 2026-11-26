# `table.json` — полета за METAR

Публикува се в Pages: https://ilianrvd.github.io/airport-nowcast/table.json (и в архива като `table_HHMM.json.gz`).

Тук са описани само полетата за METAR по летища (`airports.<ICAO>`). Пълното описание на `table.json` предстои.

Личен проект, не е официален продукт на сертифициран доставчик.

```json
{
  "airports": {
    "LBSF": {
      "metar_raw": "METAR LBSF 260630Z VRB02KT CAVOK M01/M05 Q1026 NOSIG",
      "metar_source": "awc",
      "stale_metar": false
    }
  }
}
```

| Поле | Описание |
|---|---|
| `metar_raw` | Суровият текст на METAR/SPECI, по който е смятана таблицата (най-новият за летището). `null`, ако няма METAR за последните 3 ч. |
| `metar_source` | Откъде е този METAR: `"awc"` — пряко от Aviation Weather Center (`aviationweather.gov/api/data/metar`), основният път; `"nowcasting-public"` — резервата `obs/metar_latest.json` на nowcasting-public (виж по-долу). `null`, ако няма METAR. Поле от 26.11.2026; в по-старите таблици липсва (тогава всички са от AWC). |
| `stale_metar` | `true`, ако METAR-ът е по-стар от 60 мин — тогава корекцията от METAR не се прилага. |

## Резерва за METAR (от 26.11.2026)

- Основният път е AWC: заявка при всяко пускане (:12 и :42) и втори опит след 10 s за летищата без METAR или с METAR, по-стар от 40 мин.
- Ако и след втория опит за някое летище няма METAR или най-новият е по-стар от 40 мин, се чете https://ilianrvd.github.io/nowcasting-public/obs/metar_latest.json?t=<unix време> (`?t=` заобикаля 10-минутния кеш на Pages). Формат: `docs/formats/obs_metar.md` в nowcasting-public.
- От резервата се ползват само `raw` и `time`; текстът минава през същия парсер, както при AWC (техните `t`, `vis_m`, `wind_kt` и др. не се ползват). Взимат се само съобщения от последните 3 ч и само ако най-новото е по-ново от това от AWC; тогава се сливат със съобщенията от AWC (всяко време веднъж).
- И резервата идва от AWC — помага при неуспешна или кеширана наша заявка, не при липса на данни в AWC.
- Ако резервата не се чете (мрежа, 404, лош JSON), пускането продължава само с AWC. В лога на пускането се пише кога е ползвана резервата.
