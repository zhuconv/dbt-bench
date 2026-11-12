"""Small independently calculated counterexamples, never benchmark performance claims."""

from datetime import date, timedelta


def calendar_counterexample():
    # There is an unobserved day. At the last observation, the first day must leave
    # the 28-day window although only 28 rows have been seen.
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(29) if i != 1]
    reviews = [1] + [1000] * (len(dates) - 1)
    calendar = [sum(v for day, v in zip(dates, reviews) if d - timedelta(days=27) <= day <= d) for d in dates]
    rows = [sum(reviews[max(0, i - 27):i + 1]) for i in range(len(dates))]
    different = [i for i, (a, b) in enumerate(zip(calendar, rows)) if a != b]
    aggregate_error = abs(sum(calendar) - sum(rows)) / sum(calendar)
    return {"scope": "synthetic_calendar_fixture", "dates": [d.isoformat() for d in dates],
            "reviews_daily": reviews, "calendar": calendar, "rows_28": rows,
            "independent_expected_last": sum(reviews[1:]), "different_rows": different,
            "sum_relative_error": aggregate_error,
            "aggregate_one_percent_accepts": aggregate_error <= 0.01,
            "native_verifier_executed": False}
