"""Independent exact integer accumulation of all raw laps, joined in Python."""
from collections import defaultdict
from fractions import Fraction


def unique(rows, fields):
    out = {tuple(row[field] for field in fields): row for row in rows}
    if len(out) != len(rows):
        raise ValueError('Duplicate original input key: ' + str(fields))
    return out


def rounded(total, count, policy='nearest'):
    whole, remainder = divmod(total, count)
    if policy == 'floor':
        return whole
    if policy == 'ceil':
        return whole + int(remainder > 0)
    if policy == 'half_even':
        return whole + int(2 * remainder > count or (2 * remainder == count and whole % 2))
    if policy != 'nearest':
        raise ValueError('Undeclared integer-average convention')
    return whole + int(2 * remainder >= count)


def rows(groups, policy='nearest', excluded=False):
    prefix = 'excluded_' if excluded else ''
    return [{'circuit_name': g['circuit_name'], 'race_year': g['race_year'],
             'avg_lap_time_in_ms': rounded(g[prefix+'sum_ms'], g[prefix+'laps'], policy)}
            for g in groups if g[prefix+'laps']]


def derive(laps, races, circuits, pits):
    race_map = unique(races, ['raceId'])
    circuit_map = unique(circuits, ['circuitId'])
    pit_keys = set(unique(pits, ['raceId', 'driverId', 'lap']))
    unique(laps, ['raceId', 'driverId', 'lap'])
    totals = defaultdict(lambda: [0, 0, 0, 0])
    per_race = defaultdict(lambda: [0, 0])
    names = defaultdict(set)
    matched_pits = 0
    for lap in laps:
        race = race_map[(lap['raceId'],)]
        circuit = circuit_map[(race['circuitId'],)]
        if type(lap['milliseconds']) is not int or lap['milliseconds'] <= 0:
            raise ValueError('Original lap time must be a positive integer')
        key = (circuit['name'], race['year'])
        names[circuit['name']].add(race['circuitId'])
        total = totals[key]
        total[0] += lap['milliseconds']; total[1] += 1
        pit = (lap['raceId'], lap['driverId'], lap['lap']) in pit_keys
        matched_pits += int(pit)
        if not pit:
            total[2] += lap['milliseconds']; total[3] += 1
        by_race = per_race[(*key, lap['raceId'])]
        by_race[0] += lap['milliseconds']; by_race[1] += 1
    if any(len(ids) != 1 for ids in names.values()):
        raise ValueError('Circuit-name collisions require contract review')
    groups = [dict(circuit_name=k[0], race_year=k[1], sum_ms=v[0], laps=v[1],
                   excluded_sum_ms=v[2], excluded_laps=v[3]) for k, v in sorted(totals.items())]
    unweighted = defaultdict(list)
    for (name, year, race_id), (total, count) in per_race.items():
        unweighted[(name, year)].append(Fraction(total, count))
    wrong = []
    for (name, year), means in sorted(unweighted.items()):
        average = sum(means) / len(means)
        wrong.append(dict(circuit_name=name, race_year=year,
                          avg_lap_time_in_ms=rounded(average.numerator, average.denominator)))
    return {'groups': groups, 'unweighted_race_means': wrong,
            'input_counts': {'raw_laps': len(laps), 'recorded_pits': len(pits),
                             'matched_pit_laps': matched_pits, 'groups': len(groups)},
            'half_tie_groups': [g for g in groups if (g['sum_ms'] % g['laps']) * 2 == g['laps']],
            'rule': 'All recorded laps contribute once to circuit/year integer sums/counts; no pit exclusion'}
