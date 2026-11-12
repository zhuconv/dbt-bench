"""Independently derive top-N membership ambiguity from the native raw F1 inputs.

This uses Python joins/counts, never the dbt models, answer fixture or grader.
Equal sort values inside an entirely included group do not change membership.
"""
from collections import Counter
from datetime import date


def unique(rows, key):
    result = {r[key]: r for r in rows}
    assert len(result) == len(rows), f'Duplicate input key: {key}'
    return result


def boundary(scores, limit):
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    assert len(ordered) >= limit
    threshold = ordered[limit - 1][1]
    higher = sum(value > threshold for _, value in ordered)
    tied = [key for key, value in ordered if value == threshold]
    return {'limit': limit, 'candidate_rows': len(ordered), 'cutoff': threshold,
            'strictly_above': higher, 'equal_to_cutoff': len(tied),
            'available_cutoff_slots': limit - higher, 'cutoff_keys': tied,
            'membership_ambiguous': len(tied) > limit - higher}


def derive(inputs):
    drivers = unique(inputs['drivers'], 'driverId')
    races = unique(inputs['races'], 'raceId')
    circuits = unique(inputs['circuits'], 'circuitId')
    unique(inputs['status'], 'statusId')
    unique(inputs['results'], 'resultId')
    positions = unique(inputs['position_descriptions'], 'position_text')
    metric_names = ('most_wins', 'most_retirements', 'most_podiums',
                    'most_pole_positions', 'most_fastest_laps', 'most_races')
    metrics = {name: Counter() for name in metric_names}
    oldest = {}
    for row in inputs['results']:
        driver = drivers[row['driverId']]
        driver_id = row['driverId']
        description = positions.get(row['positionText'], {}).get('position_desc')
        contributions = (row['positionOrder'] == 1, description == 'retired',
                         1 <= row['positionOrder'] <= 3, row['grid'] == 1,
                         row['rank'] == 1, description is None)
        for name, contribution in zip(metric_names, contributions):
            metrics[name][driver_id] += int(contribution)
        if row['positionOrder'] == 1:
            race = races[row['raceId']]
            assert race['circuitId'] in circuits
            race_date = date.fromisoformat(str(race['date']))
            if race_date >= date(1983, 1, 1):
                birth = date.fromisoformat(str(driver['dob']))
                oldest[row['resultId']] = race_date.year - birth.year
    laps = Counter()
    for row in inputs['lap_times']:
        assert row['driverId'] in drivers
        laps[row['driverId']] += 1
    metrics['most_laps'] = laps
    metrics['oldest_race_winners_in_modern_era'] = oldest
    reports = {name: boundary(scores, 120 if name == 'most_races' else 20)
               for name, scores in metrics.items()}
    answer = sorted(name for name, report in reports.items()
                    if report['membership_ambiguous'] and name != 'most_fastest_laps')
    return {'metrics': reports, 'other_inconsistent_models': answer,
            'given_inconsistent_model': 'most_fastest_laps',
            'semantics': 'Top-N row membership; unspecified physical ordering of equally ranked included rows is not an additional requirement.'}


RAW_TABLES = ('drivers', 'races', 'circuits', 'status', 'results', 'position_descriptions', 'lap_times')


def read_inputs(connection):
    inputs = {}
    for table in RAW_TABLES:
        cursor = connection.execute('SELECT * FROM "' + table + '"')
        columns = [c[0] for c in cursor.description]
        inputs[table] = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return inputs


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path
    import duckdb
    p = argparse.ArgumentParser()
    p.add_argument('--db', default='/app/f1.duckdb')
    p.add_argument('--output', required=True)
    args = p.parse_args()
    with duckdb.connect(args.db, read_only=True) as connection:
        result = derive(read_inputs(connection))
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
