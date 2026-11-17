"""Launch-day Twitch audience forecaster (gta6-twitch-ccu-forecast-01).

Idea: a big console launch that unlocks at midnight local time, rolling
around the globe, has a recognisable UTC shape when every hour is divided
by that launch's own hour-1 print.  New Zealand and Europe are already in
during hour 1, the curve dips and then climbs as North America wakes up,
and it peaks in the transatlantic evening.  The case file carries dozens
of past launches with their full hourly curves, so this program learns
that normalised shape from them at run time instead of hard-coding it:

  * keep only comparables whose unlock had largely happened by hour 1
    UTC -- their hour-1 print is at least 15% of their release-day peak.
    Launches with a single global unlock late in the UTC day start near
    zero and rise a hundredfold; their shape says nothing about a
    rolling-midnight launch, so they are left out of the shape pool.
  * for each forecast hour, take the log ratios ln(viewers_h / viewers_1)
    of the kept comparables and blend the empirical quantiles of those
    ratios with a normal curve fitted to their median and IQR, which
    steadies the noisy 5% and 95% tails of a modest sample.
  * shrink every log quantile 10% toward zero (toward persistence):
    the target launch is never exactly the average comparable.
  * widen the shrunk spread by 15% around its median: backtesting this
    exact recipe leave-one-out over the comparables beats the plain
    persistence forecast, and the extra width is what the pinball loss
    charges for when an hour lands outside the band.

If a case somehow carries no usable comparable, the program falls back
to persistence with a random-walk spread so it still writes a valid
forecast.  Standard library only, deterministic, no network.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z_AT_LEVEL = [-1.645, -0.674, 0.0, 0.674, 1.645]

# Shape-pool and calibration constants, chosen by leave-one-out
# backtesting over the comparables themselves (see module docstring).
MIN_HOUR1_FRACTION = 0.15
MIN_POOL = 6
MIN_DAY_HOURS = 20
SHRINK = 0.9
SPREAD_SCALE = 1.15
SIGMA_FLOOR = 0.08
LOG_CAP = 3.0


def parse_instant(text):
    """Parse an ISO-8601 UTC timestamp into an aware datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def release_day_curve(comparable):
    """Hour (1..24) -> viewers for the comparable's own release day.

    Returns None when the comparable has no usable release day: its
    hour 1 is missing, too few of the day's hours were sampled, or a
    print is not positive.  The Grand Theft Auto VI category's own
    pre-release history fails these tests on its own, as it should.
    """
    try:
        day_start = parse_instant(comparable["day_start"])
        by_hour = {}
        for point in comparable.get("hourly") or []:
            viewers = point.get("viewers")
            if viewers is None or viewers <= 0:
                continue
            offset = (parse_instant(point["from"]) - day_start).total_seconds() / 3600.0
            hour = int(round(offset)) + 1
            if 1 <= hour <= 24 and abs(offset - (hour - 1)) < 0.01:
                by_hour[hour] = float(viewers)
    except (KeyError, TypeError, ValueError):
        return None
    if 1 not in by_hour or len(by_hour) < MIN_DAY_HOURS:
        return None
    return by_hour


def shape_pool(comparables):
    """Normalised curves of the comparables that match a rolling unlock."""
    usable = []
    for comparable in comparables or []:
        curve = release_day_curve(comparable)
        if curve is not None:
            usable.append(curve)
    aligned = [c for c in usable if c[1] >= MIN_HOUR1_FRACTION * max(c.values())]
    return aligned if len(aligned) >= MIN_POOL else usable


def interpolated_quantile(sorted_values, tau):
    """The tau quantile of a sorted sample, linearly interpolated."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    position = tau * (n - 1)
    low = int(math.floor(position))
    high = min(low + 1, n - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def hour_log_quantiles(pool, hour):
    """Blended, shrunk, widened log-ratio quantiles for one hour."""
    ratios = sorted(math.log(c[hour] / c[1]) for c in pool if hour in c)
    if len(ratios) < 4:
        # Too little comparable evidence for this hour to trust: let the
        # caller fall back to persistence for it rather than fit a
        # spread to a handful of launches.
        return None
    median = interpolated_quantile(ratios, 0.5)
    iqr = interpolated_quantile(ratios, 0.75) - interpolated_quantile(ratios, 0.25)
    sigma = max(iqr / 1.349, SIGMA_FLOOR)
    blended = []
    for tau, z in zip(LEVELS, Z_AT_LEVEL):
        empirical = interpolated_quantile(ratios, tau)
        normal = median + z * sigma
        blended.append(0.5 * empirical + 0.5 * normal)
    centre = blended[2] * SHRINK
    widened = [centre + (value * SHRINK - centre) * SPREAD_SCALE for value in blended]
    return [max(-LOG_CAP, min(LOG_CAP, value)) for value in widened]


def round_cents(value):
    """Round to two decimals, half up, returning an int when whole."""
    scaled = math.floor(value * 100 + 0.5) / 100
    return int(scaled) if float(scaled).is_integer() else scaled


def to_quantiles(p1, log_quantiles):
    """Turn log quantiles around hour 1's print into viewer quantiles."""
    values = []
    for log_q in log_quantiles:
        value = p1 * math.exp(log_q)
        value = max(0.01, min(1e9, value))
        if values and value < values[-1]:
            value = values[-1]
        values.append(round_cents(value))
    return values


def forecast_case(case):
    first_hour = None
    for row in case.get("known") or []:
        if row.get("hour") == 1:
            first_hour = row
            break
    if first_hour is None:
        raise ValueError("case has no hour-1 print")
    p1 = max(1.0, float(first_hour["viewers"]))

    pool = shape_pool(case.get("comparables"))
    rows = []
    for target in case.get("forecast") or []:
        hour = int(target["hour"])
        log_quantiles = hour_log_quantiles(pool, hour) if pool else None
        if log_quantiles is None:
            # No comparable evidence at all: persistence, widening slowly.
            spread = 0.15 * math.sqrt(max(1, hour - 1))
            log_quantiles = [z * spread for z in Z_AT_LEVEL]
        rows.append({"hour": hour, "q": to_quantiles(p1, log_quantiles)})
    rows.sort(key=lambda row: row["hour"])
    return {"hours": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        result = forecast_case(case)
        text = json.dumps(result, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / (path.stem + ".out")).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
