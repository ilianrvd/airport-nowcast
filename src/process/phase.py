"""
Фаза на валежа: интеграл на мокрия термометър (по IMPROVER)
==========================================================
Положителната част на Tw (°C) се интегрира по височина отгоре надолу до
височината на летището. Най-долната точка е Tw на 2 m.

  I < 90 °C·m          → SN   (снегът не се топи)
  90 ≤ I ≤ 202.5 °C·m  → RASN (частично топене)
  I > 202.5 °C·m       → RA   (напълно разтопен)
  + T2m ≤ 0 °C и I ≥ 90 → FZRA (разтопено горе, замръзва на земята;
                               PL не се отделя, влиза във FZRA)

Профилът: списък от нива (p_hPa, z_m над морето, T_°C, RH_%). Нивата под
летището се изрязват.
"""

import math

SNOW_LEVEL = 90.0      # °C·m
RAIN_LEVEL = 202.5     # °C·m


def _es(t):
    """Наситено налягане на парите над вода, hPa (Bolton 1980)."""
    return 6.112 * math.exp(17.67 * t / (t + 243.5))


def wet_bulb(t, rh, p):
    """Tw (°C) от T (°C), RH (%) и p (hPa): психрометрично уравнение,
    решено с бисекция. Точност ~0.01 °C."""
    e = max(rh, 0.1) / 100.0 * _es(t)
    gamma = 6.6e-4 * p                          # психрометрична константа, hPa/K
    lo, hi = t - 40.0, t
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        f = _es(mid) - gamma * (t - mid) - e
        if f > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def wet_bulb_from_td(t, td, p):
    rh = 100.0 * _es(td) / _es(t)
    return wet_bulb(t, min(rh, 100.0), p)


def _pos_integral(z0, w0, z1, w1):
    """∫ max(Tw, 0) dz между две нива (z1 > z0), с линейно Tw и точно
    отрязване при пресичане на нулата."""
    if z1 <= z0:
        return 0.0
    if w0 >= 0 and w1 >= 0:
        return 0.5 * (w0 + w1) * (z1 - z0)
    if w0 <= 0 and w1 <= 0:
        return 0.0
    zc = z0 + (z1 - z0) * (0 - w0) / (w1 - w0)   # височина на нулата
    if w0 > 0:
        return 0.5 * w0 * (zc - z0)
    return 0.5 * w1 * (z1 - zc)


def wb_integral(levels, z_sfc, tw_sfc):
    """levels: [(p, z, T, RH), …]. Връща (интеграл °C·m, max Tw горе °C)."""
    pts = [(z_sfc, tw_sfc)]
    for p, z, t, rh in levels:
        if None in (p, z, t, rh) or z <= z_sfc + 5:
            continue
        pts.append((z, wet_bulb(t, rh, p)))
    pts.sort()
    total = sum(_pos_integral(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                for i in range(len(pts) - 1))
    warm_aloft = max((w for z, w in pts[1:]), default=float("nan"))
    return total, warm_aloft


def p_surface(z):
    """Приблизително налягане на височина z (m), стандартна атмосфера."""
    return 1013.25 * (1 - 2.25577e-5 * z) ** 5.25588


def corrected_levels(levels, z_sfc, dT, depth):
    """Корекцията на T от METAR (dT) се прилага изцяло на земята и линейно
    до 0 на височина depth над летището. RH на нивата се запазва."""
    out = []
    for p, z, t, rh in levels:
        h = z - z_sfc
        w = max(0.0, 1.0 - h / depth) if h >= 0 else 1.0
        out.append((p, z, t + dT * w, rh))
    return out


def classify(integral, t2m):
    if integral != integral:          # NaN
        return None
    if t2m is not None and t2m <= 0.0 and integral >= SNOW_LEVEL:
        return "FZRA"
    if integral < SNOW_LEVEL:
        return "SN"
    if integral <= RAIN_LEVEL:
        return "RASN"
    return "RA"
