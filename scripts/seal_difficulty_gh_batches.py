#!/usr/bin/env python3
"""Seal reviewed batch/member sources before any gh submission (no model calls)."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from review_tools import cached_hashes


def seal(plan):
    plan = Path(plan).resolve()
    summary = io.read_json(plan / 'summary.json')
    outputs = []
    for batch in summary['batches']:
        directory = Path(batch['batch'])
        if not io.evidence_ok(batch['manifest'], ROOT):
            raise ValueError('Batch manifest changed')
        manifest = io.read_json(directory / 'manifest.json')
        outputs.append(directory)
        for member in manifest['identity']['members']:
            if not io.evidence_ok(member['manifest'], ROOT):
                raise ValueError('Member manifest changed')
            outputs.append(Path(member['run']))
    for directory in outputs:
        if any((directory / name).exists() for name in ('preflight.json', 'submission.json', 'submission-intent.json', 'launch.json')):
            raise ValueError('Preparation already sealed or submitted: ' + str(directory))
    refs = {'runner': io.evidence_ref(ROOT / 'scripts/run_difficulty_gh_batch.py', ROOT),
            'slurm_script': io.evidence_ref(ROOT / 'scripts/slurm/gh-difficulty-batch.sbatch', ROOT),
            'sealer': io.evidence_ref(__file__, ROOT),
            'execution_dependencies': [io.evidence_ref(ROOT / name, ROOT) for name in
                ('scripts/run_difficulty_gpu_v2.py', 'scripts/run_gh_smoke.py',
                 'scripts/review_tools.py', 'src/dbt_bench/io.py')]}
    for directory in outputs:
        io.write_json(directory / 'preflight.json', {**refs,
            'manifest': io.evidence_ref(directory / 'manifest.json', ROOT),
            'model_requests': 0, 'training_prohibited': True})
    from run_difficulty_gh_batch import validate_batch
    with cached_hashes():
        for batch in summary['batches']:
            validate_batch(Path(batch['batch']))
    result = {'batches': len(summary['batches']), 'sealed_directories': len(outputs),
              'model_requests': 0, 'training_prohibited': True,
              'summary': io.evidence_ref(plan / 'summary.json', ROOT),
              'preflights': [io.evidence_ref(p / 'preflight.json', ROOT) for p in outputs]}
    io.write_json(plan / 'sealed.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--plan', type=Path, required=True)
    result = seal(p.parse_args().plan)
    print({k: result[k] for k in ('batches', 'sealed_directories', 'model_requests')})
