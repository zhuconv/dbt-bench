"""Explicit physical type/order fixtures for the final audit implementation."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import ast
import importlib.util
import json
from audit_tables import CapturedTable, decode, load_observer_table
from dbt_bench import io
from phase3_capture import verify_trial
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/shared_projects/cart-trust-phase3-v1'))
s = importlib.util.spec_from_file_location('ct_typed_engine', D / 'cart_trust_engine_v3.py')
e = importlib.util.module_from_spec(s)
s.loader.exec_module(e)

def main():
    functions = ['expected', 'table_contract', 'normalize', 'score_table', 'semantic_values', 'evaluate']
    trees = [ast.parse((D / ('cart_trust_engine_v' + v + '.py')).read_text()) for v in ('2', '3')]
    equality = {name: ast.dump(next((n for n in trees[0].body if isinstance(n, ast.FunctionDef) and n.name == name))) == ast.dump(next((n for n in trees[1].body if isinstance(n, ast.FunctionDef) and n.name == name))) for name in functions}
    assert all(equality.values())
    records = []
    for task, trialname in [('deb__dbt-retail-task-07-cart-recovery', 'deb__dbt-retail-task-07-cart-rec__UZsMsty'), ('deb__dbt-retail-task-10-trust-risk', 'deb__dbt-retail-task-10-trust-ri__GYMBiX8')]:
        b = R / 'artifacts/validation/phase3-deb-batch06-rebuild-v2'
        trial = b / 'jobs' / b.name / trialname
        d = verify_trial(b, trial)
        cap = d['capture']
        raw = {name.removeprefix('main__'): [dict(zip([c.lower() for c in v['columns']], map(decode, json.loads(line)))) for line in cap.runtime(v).read_text().splitlines()] for name, v in d['initial']['raw'].items()}
        ex, _ = e.expected(task, raw)
        snapshot = e.ref(trial / 'agent/audit-submission/snapshot.json')
        for model, rows in ex.items():
            actual = load_observer_table(snapshot, task_key=task, database='/app/database/retail.duckdb', relation='main.' + model, stage='agent-submission', root=R)
            tc = e.table_contract(task, model)
            names = tuple((c.name for c in tc.columns))
            types = {n: actual.physical_sql_types.get(n, 'DECIMAL(38,4)') for n in names}
            if model == 'rpt_review_moderation_kpis':
                types['review_month'] = 'TIMESTAMP'
            base = CapturedTable(names, types, deepcopy(rows), names, types, actual.relation_type, {'fixture': True, 'source_snapshot': snapshot})

            def run(label, table, want, reason):
                answer = e.score_table(task, model, table, rows)
                assert answer['reward'] == want, (task, model, label, answer)
                proof = {'task_key': task, 'model': model, 'case': label, 'expected_reward': want, 'audit_reward': answer['reward'], 'independent_proof': reason, 'proof_established_before_checker': True, 'audit': answer, 'scope': 'Constructed physical-interface fixture on independently expected output rows; does not supply a complete legal dbt build.'}
                rr = e.write(D / 'typed-fixtures-v1' / task / (model + '--' + label + '.json'), proof)
                records.append({'task_key': task, 'model': model, 'case': label, 'evidence': rr, 'reward': want})
            run('positive-declared-interface', base, 1, 'Explicit contract columns/types with independently recomputed values.')
            run('reversed-physical-columns', replace(base, columns=tuple(reversed(names)), physical_columns=tuple(reversed(names))), 0 if '-10-' in task else 1, 'Trust explicitly mandates column order; Cart requires column names but permits a different physical order.')
            selected = next((c for c in tc.columns if c.kind == 'datetime'), tc.columns[0])
            wrong = {**types, selected.name: 'VARCHAR' if selected.kind != 'text' else 'INTEGER'}
            run('wrong-sql-type', replace(base, sql_types=wrong, physical_sql_types=wrong), 0, {'column': selected.name, 'declared_kind': selected.kind, 'wrong_physical_type': wrong[selected.name], 'scope': 'Values kept exact; forbidden physical SQL type alone must reject.'})
    e.write(D / 'typed-fixtures-v1/summary.json', {'records': records, 'count': len(records), 'all_expected': True, 'table_functions_identical_to_executed_389_fixture_version': equality, 'earlier_table_fixtures': e.ref(D / 'fixtures-v1/summary.json'), 'code': e.ref(Path(__file__)), 'model_requests': 0})
    print(json.dumps({'count': len(records), 'all_expected': True}))
if __name__ == '__main__':
    main()
