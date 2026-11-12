"""One-task process boundary for incompatible frozen module import names.

This executable invokes frozen qualified checker code. Unavailable provenance
is passed as unavailable, never filled from the author's prior successful run.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def invoke(checker, request, root, *, style):
    sys.path[:0] = [str(checker.parent), str(root / 'scripts'), str(root / 'src')]
    if style.startswith('phase4_'):
        suffix={'phase4_customer':'_v4','phase4_pipeline':'_v2','phase4_operations':'_v2'}.get(style,'')
        handler_name='difficulty_deb_'+style+suffix
        handler=__import__(handler_name,fromlist=['run'])
        return handler.run(request,root)
    if style == 'batch03':
        from dbt_bench import io
        from audit_tables import load_observer_table
        policy = io.read_json(checker.parent / 'policy-v1.json')
        if not io.evidence_ok(policy['engine'], root):
            raise ValueError('Qualified delegated engine changed')
        engine_path = root / policy['engine']['path']
        engine_spec = importlib.util.spec_from_file_location('difficulty_batch03_engine', engine_path)
        engine = importlib.util.module_from_spec(engine_spec); engine_spec.loader.exec_module(engine)
        key, raw, _, context, verified = engine.load_context(
            root / request['batch'], root / request['trial'], root, request['parsed_capture'])
        if key != policy['task_key'] or key != request['task_key']:
            raise ValueError('Engine task mismatch')
        snap = io.read_json(root / request['snapshot']['path'])
        if not io.evidence_ok(request['snapshot'], root):
            raise ValueError('Actual submitted snapshot changed')
        tables = {}
        for row in snap['outputs']:
            sp = row['specification']
            if row.get('error'):
                error=row['error']
                if error.get('type')=='CatalogException' and 'does not exist' in error.get('message',''):
                    continue
                raise ValueError('Actual output observation is incomplete: '+str(error))
            tables[sp['relation'].lower()] = load_observer_table(request['snapshot'], task_key=key,
                database=sp['database'], relation=sp['relation'], stage='agent-submission', root=root)
        result = engine.evaluate(key, raw, tables, context)
        result['context_scope'] = 'Fresh exact-source reconstruction context; prescribed original model actions require a separate raw-Qwen gate.'
        result['actual_snapshot'] = request['snapshot']
        return result
    spec = importlib.util.spec_from_file_location('difficulty_frozen_deb_checker', checker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run(request, root)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checker', type=Path, required=True)
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--style', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError('Preserve checker results')
    request = json.loads(a.request.read_text())
    try:
        result = invoke(a.checker, request, a.root, style=a.style)
    except Exception as exc:
        result = {'reward': None, 'status': 'unknown', 'error_type': type(exc).__name__, 'reason': str(exc)}
    result.update(target='audit', task_key=request['task_key'], model_requests=0,
                  native_rewards_modified=False, training_prohibited=True)
    a.output.write_text(json.dumps(result, indent=2, default=str) + '\n')
