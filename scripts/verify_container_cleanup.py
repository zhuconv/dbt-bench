#!/usr/bin/env python3
"""Verify completed native Harbor container lifecycles, without killing processes."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
from review_tools import ROOT
from dbt_bench.io import read_json, write_json, evidence_ref
from check_harbor_lifecycle import group_members, port_open


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jobs', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    checks = []
    ids = set()
    for jobs in a.jobs:
        for path in sorted(jobs.rglob('result.json')):
            raw = read_json(path)
            if 'trial_name' not in raw:
                continue
            assert raw['finished_at'] and raw['id'] not in ids
            ids.add(raw['id'])
            log = path.parent / 'trial.log'
            active = None
            for line in log.read_text().splitlines():
                start = re.search(r'Starting Singularity container with server on port (\d+) \(attempt (\d+)/', line)
                stop = re.search(r'Sent SIGTERM to container process group (\d+)', line)
                if start:
                    assert active is None
                    active = {'port': int(start[1]), 'attempt': int(start[2])}
                if stop:
                    assert active is not None
                    active['pgid'] = int(stop[1])
                    members = group_members(active['pgid'])
                    opened = port_open(active['port'])
                    checks.append({**active, 'trial_id': raw['id'], 'log': evidence_ref(log, ROOT),
                                   'remaining_members': members, 'port_open': opened,
                                   'passed': not members and not opened})
                    active = None
            assert active is None, str(log)
    report = {'passed': bool(checks) and all(c['passed'] for c in checks),
              'trials_checked': len(ids), 'containers_checked': len(checks),
              'native_adapter_startup_retries': len(checks) - len(ids), 'checks': checks,
              'observed_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Terminal trial lifecycles only; interrupted executions require a separate retained diagnosis.',
              'checker': evidence_ref(Path(__file__), ROOT)}
    write_json(a.output, report)
    print({k: report[k] for k in ('passed', 'trials_checked', 'containers_checked')})
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
