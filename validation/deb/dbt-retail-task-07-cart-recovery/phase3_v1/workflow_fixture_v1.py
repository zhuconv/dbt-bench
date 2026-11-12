"""Executed workflow-component counterexamples; never a complete dbt witness."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import importlib.util
import json
from dbt_bench import io
R = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/validation/phase3-review-v1/shared_projects/cart-trust-phase3-v1'))
OUT = D / 'workflow-fixtures-v1'

def ref(p):
    return io.evidence_ref(p, R)

def save(p, value):
    assert not p.exists(), p
    io.write_json(p, value)
    return ref(p)

def module(p):
    spec = importlib.util.spec_from_file_location(p.parent.parent.name + p.stem, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def walk(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)

def main():
    summary = io.read_json(D / 'actual-review-v3/summary.json')
    seen, records = (set(), [])
    for trial in summary['trials']:
        task = trial['task_key']
        if task in seen:
            continue
        seen.add(task)
        directory = R / 'validation/deb' / task.removeprefix('deb__') / 'phase3_v1'
        checker = module(directory / 'project_workflow_v2.py')
        old = module(directory / 'project_workflow_v1.py')
        actual = trial['records'][0]
        request = io.read_json(R / actual['spec']['path'])['workflow_request']
        facts = io.read_json(R / request['workflow']['path'])
        manifest = io.read_json(R / facts['manifest']['path'])
        parsed = io.read_json(R / request['parsed_snapshot']['path'])
        nodes = {n['name']: n for n in manifest['nodes'].values() if n.get('resource_type') == 'model'}
        models = old.MODELS

        def run(name, target, change, expected=False, reason=None):
            folder = OUT / task / name
            f, m, p = (deepcopy(facts), deepcopy(manifest), deepcopy(parsed))
            nd = {n['name']: n for n in m['nodes'].values() if n.get('resource_type') == 'model'}
            f['producer'] = 'task_image_selftest_clean_build'
            change(f, nd, p, folder)
            f['manifest'] = save(folder / 'manifest.json', m)
            p['manifest_sha256'] = f['manifest']['sha256']
            q = {**request, 'workflow': save(folder / 'workflow.json', f), 'parsed_snapshot': save(folder / 'parsed.json', p)}
            qr = save(folder / 'request.json', q)
            answer = checker.evaluate(q, R)
            assert answer['checks'][target] is expected, (task, name, target, answer)
            proof = {'task_key': task, 'case': name, 'target': target, 'independently_expected_check': expected, 'actual_target_check': answer['checks'][target], 'reason': reason, 'scope': 'Synthetic trusted-capture component fixture derived from actual captured source/manifest/parsed SQL. It checks one workflow predicate; no native run, no new complete SQL solution, no semantic score claim.', 'actual_capture': actual['spec'], 'request': qr, 'checker': ref(directory / 'project_workflow_v2.py'), 'background_failures': [k for k, v in answer['checks'].items() if not v and k != target], 'proof_established_before_checker': True, 'model_requests': 0}
            if name == 'deleted-current-model':
                former = old.evaluate(q, R)
                proof['previous_checker_reward'] = former['reward']
                proof['previous_checker_omitted_target'] = target not in former['checks']
                assert proof['previous_checker_omitted_target']
            proof['audit'] = save(folder / 'audit.json', answer)
            records.append({'task_key': task, 'case': name, 'target': target, 'expected': expected, 'passed': True, 'proof': save(folder / 'proof.json', proof)})
        chosen = models[-1]
        path = nodes[chosen]['original_file_path']
        run('deleted-current-model', chosen + ':delivered_source', lambda f, n, p, o: f['observed_files'][path].update(exists=False, file=None), reason='The required current SQL file has been removed; an old successful manifest cannot establish delivery.')

        def stale(f, n, p, o):
            q = o / 'changed.sql'
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text(nodes[chosen]['raw_code'] + '\n-- changed after the captured successful build\n')
            f['observed_files'][path] = {'exists': True, 'file': ref(q)}
        run('changed-after-build', chosen + ':delivered_source_matches_build', stale, reason='The delivered bytes differ from the successful captured manifest raw_code; score cannot attest that build for the new source.')
        run('missing-tags', chosen + ':tags', lambda f, n, p, o: n[chosen]['config'].update(tags=[]), reason='Required tags are empty in the captured model configuration.')
        run('wrong-materialization', chosen + ':materialization', lambda f, n, p, o: n[chosen]['config'].update(materialized='table' if '-10-' in task else 'view'), reason='The named model materialization is the opposite of the explicit task requirement.')
        run('wrong-folder', chosen + ':folder', lambda f, n, p, o: n[chosen].update(original_file_path='models/wrong/' + chosen + '.sql'), reason='Required SQL directory differs; model name does not make its folder legal.')
        run('wrong-profiles-env', 'profiles_environment', lambda f, n, p, o: f['environment'].update(DBT_PROFILES_DIR='/tmp/wrong-profile'), reason='Captured DBT_PROFILES_DIR is outside the prescribed project directory.')
        run('late-backend-echo', 'echo_before_code', lambda f, n, p, o: f.update(source_install_after_utc='2000-01-01T00:00:00+00:00'), reason='The captured source installation precedes the actual backend echo completion.')
        run('forbidden-source-dependency', chosen + ':no_source', lambda f, n, p, o: n[chosen].update(sources=[['enterprise_db', 'ORDERS']]), reason='A declared source() dependency contradicts the explicit ref-only workflow.')
        if '-07-' in task:
            run('nonstaging-ref', chosen + ':staging_or_own_refs', lambda f, n, p, o: n[chosen]['refs'].append({'name': 'unallowed_nonstaging_relation'}), reason='A new upstream ref is neither staging nor one of the five allowed outputs.')
            run('macro-business-logic', chosen + ':logic_in_models', lambda f, n, p, o: n[chosen]['depends_on']['macros'].append('macro.unallowed.business_logic'), reason='Business logic has acquired a macro dependency despite the explicit model-only rule.')
            for label, model, alias, target in [('missing-event-sum-case', models[1], 'checkout_event_count', 'conditional_sum_case'), ('missing-consent-max-case', models[3], 'email_consent', 'consent_max_case')]:

                def change(f, n, p, o, model=model, alias=alias):
                    for v in walk(p['parsed_compiled_sql'][model]['ast']):
                        if v.get('alias') == alias:
                            v['alias'] = '__wrong_alias'
                run(label, target, change, reason='Required named conditional aggregate is absent from the active AST fixture.')
        else:
            schema = 'models/marts/reviews/schema.yml'
            run('missing-schema-yaml', schema, lambda f, n, p, o: f['observed_files'][schema].update(exists=False), reason='The required delivered schema YAML has been removed.')
            run('missing-staging-ref', models[0] + ':refs', lambda f, n, p, o: n[models[0]].update(refs=[]), reason='The enriched model no longer declares the required ten staging dependencies.')
            run('actual-else-true', 'all_flags_case_else_false', lambda f, n, p, o: None, reason='The actual author CASE uses ELSE TRUE. Values are correct, but this explicit literal form is not.')

            def legal_form(f, n, p, o):
                count = 0
                for v in walk(p['parsed_compiled_sql'][models[1]]['ast']):
                    if v.get('alias') == 'unverified_purchase_flag' and old.bare(v).get('class') == 'CASE':
                        old.bare(v)['else_expr'] = {'class': 'CONSTANT', 'value': {'value': False}}
                        count += 1
                assert count
            run('case-form-else-false', 'all_flags_case_else_false', legal_form, True, reason='A synthetic ELSE FALSE AST form is accepted for the structural predicate only; it is not a rewritten author solution or a value-equivalent claim.')
            for label, model, alias, target in [('missing-cte-review-status', models[0], 'review_status', 'reviews_cte_mapping'), ('missing-same-cte-score', models[1], 'score_deductions', 'same_cte_score_and_deductions'), ('missing-kpi-percentage-form', models[2], 'verified_purchase_share', 'kpi_round_nullif')]:

                def change(f, n, p, o, model=model, alias=alias):
                    for v in walk(p['parsed_compiled_sql'][model]['ast']):
                        if v.get('alias') == alias:
                            v['alias'] = '__wrong_alias'
                run(label, target, change, reason='The mandated named SQL structural expression is absent in this AST fixture.')
    save(OUT / 'summary.json', {'records': records, 'all_passed': True, 'count': len(records), 'code': ref(Path(__file__)), 'model_requests': 0})
    print(json.dumps({'count': len(records), 'all_passed': True}))
if __name__ == '__main__':
    main()
