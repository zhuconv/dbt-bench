"""Pure independent completed-calendar-age counts from original driver DOBs."""
from collections import Counter
from datetime import date


def completed_age(born, today):
    return today.year - born.year - int((today.month, today.day) < (born.month, born.day))


def derive(drivers, as_of):
    today = date.fromisoformat(as_of)
    ids = [row['driverId'] for row in drivers]
    if len(ids) != len(set(ids)) or any(type(i) is not int for i in ids):
        raise ValueError('Original driver IDs must be unique non-null integers')
    counts = Counter()
    year_only = Counter()
    for row in drivers:
        born = date.fromisoformat(str(row['dob']))
        if born > today:
            raise ValueError('Future birth date requires separate input review')
        counts[completed_age(born, today)] += 1
        year_only[today.year - born.year] += 1
    return {'as_of_date': as_of, 'driver_rows': len(ids),
            'rows': [{'current_age_in_years': age, 'drivers': n} for age, n in sorted(counts.items())],
            'year_only_rows': [{'current_age_in_years': age, 'drivers': n} for age, n in sorted(year_only.items())],
            'age_range': [min(counts), max(counts)],
            'rule': 'Completed years; birthday month/day pending subtracts one; no active/living filter'}
