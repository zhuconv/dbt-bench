"""Run one frozen Spider table component in an isolated Python process."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from mechanical_observer import encode
from difficulty_spider_registry import qualified_entry, load, required_output_evidence


def run(request, root=ROOT):
    qualified = qualified_entry(request['qualified_review'], request['task_key'],
                                request['task_version'], root)
    load(request['candidate_snapshot'], root)
    checker = qualified['checker']
    sys.path.insert(0, str(checker.parent))
    module_spec = importlib.util.spec_from_file_location('difficulty_frozen_spider_component', checker)
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    spec = {'raw_snapshot': qualified['raw_ref'], 'candidate_snapshot': request['candidate_snapshot'],
            'candidate_stage': 'agent-submission'}
    if request['task_key'] in {'spider2__apple_store001', 'spider2__asset001', 'spider2__jira001'}:
        import variant_audit_support_v1
        result = variant_audit_support_v1.replay(module, spec, root)
    else:
        result = module.replay(spec, root)
    reward = result.get('audit_reward', result.get('table_audit', {}).get('reward'))
    status = result.get('audit_status', result.get('table_audit', {}).get('status'))
    if status is None:
        status = 'scored' if reward in (0, 1) else 'unknown'
    presence = None
    if request.get('actual_probe'):
        probe = load(request['actual_probe'], root)
        if not probe['database_and_wal_unchanged']:
            raise ValueError('Actual probe did not preserve DB/WAL')
        snapshot = load(request['candidate_snapshot'], root)
        targets = [x['specification'] for x in snapshot['outputs']
                   if not x['specification']['relation'].startswith('main.')]
        absent = []
        for target in targets:
            entries = [x for x in probe['relation_inventory'] if
                       x['database'] == target['database'] and x['relation'] == target['relation']]
            if len(entries) == 1 and entries[0]['query_succeeded'] and entries[0]['exists'] is False:
                absent.append(target['relation'])
        if absent:
            # Keep the frozen component's inability to load a missing table
            # intact. A successful information_schema query independently
            # proves failure of its declared required-relation precondition.
            presence = {'reward': 0, 'missing_required_relations': absent,
                        'actual_probe': request['actual_probe'],
                        'qualified_required_output': required_output_evidence(qualified, root),
                        'scope': 'Qualified required-output precondition; frozen data result retained unchanged.'}
            reward, status = 0, 'scored'
    return {'task_key': request['task_key'], 'target': 'audit', 'reward': reward,
            'status': status, 'table_component': result, 'checker': io.evidence_ref(checker, root),
            'required_output_presence': presence,
            'fixture_only': request['fixture_only'], 'model_requests': 0,
            'training_prohibited': True,
            'scope': 'Table component only. Original source, inputs, workflow and actual model actions require separate binding.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve prior evidence')
    value = run(io.read_json(args.request))
    io.write_json(args.output, json.loads(json.dumps(value, default=encode, allow_nan=False)))
