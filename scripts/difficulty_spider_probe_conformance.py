"""Physical missing-column regression on a copy of a captured fixture DB.

This is an explicitly synthetic audit fixture, never an actual model trial.
The source capture and original native scores remain immutable.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from difficulty_spider_binding import verify
from difficulty_spider_score import APPTAINER, save


def run(audit_dir, output):
    context = verify(audit_dir)
    key = context['binding']['task_key']
    if key != 'spider2__lever001' or context['binding']['fixture_only'] is not True:
        raise ValueError('This physical regression requires the explicitly marked Lever calibration fixture')
    output.mkdir(parents=True, exist_ok=False)
    image = context['production'][key]['image']
    copied = output / 'lever.duckdb'
    item = next(x for x in context['original_capture']['databases'] if x['path'] == '/workspace/lever.duckdb')
    original = context['original_trial'] / 'artifacts/difficulty-submission' / item['file']
    shutil.copyfile(original, copied)
    if io.file_hash(copied) != item['sha256']:
        raise ValueError('Capture copy differs')
    base = [APPTAINER, 'exec', '--cleanenv', '--containall', '--no-mount',
            'home,tmp,cwd,hostfs,bind-paths', '--pwd', '/', '--bind', str(ROOT) + ':/repo:ro',
            '--bind', str(output) + ':/audit:rw', '--bind', str(output) + ':/workspace:rw',
            str(ROOT / image['path']), '/usr/local/bin/python3']
    code = "import duckdb,json; c=duckdb.connect('/workspace/lever.duckdb'); before=c.execute('DESCRIBE lever__posting_enhanced').fetchall(); c.execute('ALTER TABLE lever__posting_enhanced DROP COLUMN count_posting_applications'); after=c.execute('DESCRIBE lever__posting_enhanced').fetchall(); c.close(); print(json.dumps({'before':before,'after':after,'independently_violated_requirement':'Required count_posting_applications column removed physically; no tested checker defines validity.'}))"
    p = subprocess.run(base + ['-c', code], capture_output=True, text=True, timeout=300)
    (output / 'mutation.log').write_text(p.stdout + p.stderr)
    if p.returncode:
        raise ValueError('Physical fixture mutation failed')
    proof = json.loads(p.stdout.strip().splitlines()[-1])
    if 'count_posting_applications' not in [r[0] for r in proof['before']] or 'count_posting_applications' in [r[0] for r in proof['after']]:
        raise ValueError('Mutation did not establish required-column removal')
    save(output / 'case-proof.json', {**proof, 'fixture_only': True, 'model_requests': 0,
        'source_capture': io.evidence_ref(original, ROOT), 'original_sha256': item['sha256'],
        'mutated_copy': io.evidence_ref(copied, ROOT)})
    observation = deepcopy(context['request']['observation'])
    observation['files'] = []
    observation['observation_sql_types'] = True
    raw = context['qualified']['raw']
    save(output / 'probe-request.json', {'observation': observation,
        'raw_queries': [{'database': raw['database'], 'relation': t['relation'], 'query': t['query']} for t in raw['tables']],
        'fixture_only': True, 'model_requests': 0})
    p = subprocess.run(base + ['/repo/scripts/difficulty_spider_probe.py', '--request', '/audit/probe-request.json',
                              '--output', '/audit/observed'], capture_output=True, text=True, timeout=300)
    (output / 'probe.log').write_text(p.stdout + p.stderr)
    if p.returncode:
        raise ValueError('Physical fixture observation failed')
    save(output / 'checker-request.json', {'task_key': key, 'task_version': context['binding']['task_version'],
        'qualified_review': context['binding']['qualified_review'],
        'candidate_snapshot': io.evidence_ref(output / 'observed/snapshot.json', ROOT),
        'actual_probe': io.evidence_ref(output / 'observed/probe.json', ROOT), 'fixture_only': True, 'model_requests': 0})
    p = subprocess.run([sys.executable, str(ROOT / 'scripts/difficulty_spider_worker.py'),
        '--request', str(output / 'checker-request.json'), '--output', str(output / 'checker-result.json')],
        capture_output=True, text=True, timeout=300, env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'})
    (output / 'checker.log').write_text(p.stdout + p.stderr)
    if p.returncode:
        raise ValueError('Frozen component execution failed')
    result = io.read_json(output / 'checker-result.json')
    if result['reward'] != 0 or result['table_component']['table_audit']['reward'] != 0:
        raise ValueError('Frozen component did not reject physically missing required column')
    if io.file_hash(original) != item['sha256']:
        raise ValueError('Original capture changed')
    save(output / 'summary.json', {'task_key': key, 'passed': True, 'frozen_component_reward': 0,
        'case_proof': io.evidence_ref(output / 'case-proof.json', ROOT),
        'checker_result': io.evidence_ref(output / 'checker-result.json', ROOT),
        'fixture_only': True, 'model_requests': 0, 'original_capture_unchanged': True,
        'scope': 'Physical malformed submission fixture only; not another model attempt or benchmark score.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.audit_dir, args.output)
