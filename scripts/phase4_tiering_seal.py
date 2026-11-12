"""Adjudicate the actually wrong phase4 tiering witness; never admit it to a pool."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import argparse, sys, json
from dbt_bench import io
from phase4_review import binding, precheck
from phase4_publish import reproduction, candidate_evaluation
from phase4_customer4_review_v3 import ROOT, ref, write, verify_trial, raw_rows, outputs
B = Path(_release_expand('${ARTIFACT_ROOT}/validation'))
P = B / 'phase4-review-v1/pipeline'
OLD = B / 'phase2-review-v1/deb_commerce/rfm-predictive-tiering-v1'
TASK = 'dbt-rfm-customer-tiering'
KEY = 'deb__' + TASK

def main():
    folder = P / 'reviews' / TASK
    out = folder / 'sealed-v1'
    if out.exists():
        raise ValueError('Already sealed')
    fresh = io.read_json(folder / 'fresh-v3/fresh-review.json')
    freshref = ref(folder / 'fresh-v3/fresh-review.json')
    if fresh['six_stage_rewards'] != [0] * 6 or not fresh['repeat_equal'] or (not fresh['all_context_values_equal']):
        raise ValueError('Current semantic counterexample or stability not established')
    batch = B / 'phase4-deb-customer-batch01-v1'
    ident = binding(batch, fresh['label'])
    rebuilt = reproduction(batch / 'reconciliation-v1.json', ident)
    if not rebuilt['native1_both_clean_builds']:
        raise ValueError('Not actual native1 reproductions')
    previous = io.read_json(OLD / TASK / 'audit-overlay-v1.json')
    original = io.read_json(ROOT / 'validation/deb' / TASK / 'contract.yaml')
    base = io.read_json(ROOT / ident['base_record']['path'])
    for e in previous['code_refs']:
        if not io.evidence_ok(e, ROOT):
            raise ValueError('Retained audit code changed')
    sys.path.insert(0, str(OLD))
    import audit_suite_v3 as prior
    replay = []
    for specpath in sorted((OLD / TASK).glob('*-spec-v3.json')):
        spec = io.read_json(specpath)
        if not spec.get('case_proof'):
            continue
        result = prior.replay(spec, ROOT, TASK)
        oldpath = Path(str(specpath).replace('-spec-v3.json', '-audit-v3.json'))
        old = io.read_json(oldpath)
        if result.get('audit_reward') != old.get('audit_reward'):
            raise ValueError('Retained component result changed')
        ar = write(out / 'retained-replay' / oldpath.name, result)
        replay.append({'spec': ref(specpath), 'old_result': ref(oldpath), 'new_result': ar, 'reward': result.get('audit_reward')})
    if not replay:
        raise ValueError('No actual prior cases replayed')
    replayref = write(out / 'retained-case-replay.json', {'task_key': KEY, 'records': replay, 'model_requests': 0, 'scope': 'Reexecuted unchanged finite old audit components on their exact recorded native case snapshots; no new native result and no claim of a complete correct baseline.'})
    current = []
    for record in fresh['records']:
        trial = batch / 'jobs' / batch.name / record['trial']
        v = verify_trial(batch, trial)
        raw = raw_rows(v)
        legacyraw = {**raw, 'mapping': raw['dim_customer']}
        for stage in ('agent-submission', 'verifier-before', 'verifier-after'):
            sr, o = outputs(v, stage)
            companions = {'main.' + n: item['table'] for n, item in o.items()}
            components = {rel: prior.inspect(table, rel, legacyraw, TASK, companions) for rel, table in companions.items()}
            proof = io.read_json(ROOT / next((s['audit_result']['path'] for s in record['stages'] if s['stage'] == stage)))
            if proof['reward'] != 0 or len(proof['counterexamples']) != 10:
                raise ValueError('Literal default-channel proof absent')
            ar = write(out / (record['trial'] + '-' + stage + '-audit.json'), {'task_key': KEY, 'candidate_snapshot': sr, 'reward': 0, 'status': 'scored', 'literal_default_counterexample': proof, 'retained_finite_components': components, 'model_requests': 0, 'scope': 'Conjunctive fixed-input review: definitive violated default-weight/cost rule rejects. Retained finite business checks remain partial; if none rejects, full acceptance is not established.'})
            current.append({'trial': record['trial'], 'stage': stage, 'candidate_snapshot': sr, 'audit_result': ar, 'audit_reward': 0, 'audit_field_paths': {'reward': ['reward'], 'candidate_snapshot': ['candidate_snapshot'], 'task_key': ['task_key']}})
    currentref = write(out / 'current-review.json', {'task_key': KEY, 'records': current, 'fresh': freshref, 'retained_replay': replayref, 'model_requests': 0, 'finding': 'For all ten actual sold channels, raw channel_type is DIGITAL. Literal default weight1/cost.20 implies raw_revenue=weighted_revenue and contribution margin80.00. The blind build reclassifies DIGITAL as Online, yielding weight1.2 and margin87.50. Independently grouped original raw revenues match every candidate channel exactly; native still returns1 twice.'})
    requirements = [{k: deepcopy(r[k]) for k in ('id', 'description', 'critical') if k in r} for r in original['audit']['requirements']]
    contractref = write(out / 'contract.json', {'schema_version': 1, 'task_key': KEY, 'review_status': 'reviewed', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'workflow': original['workflow'], 'inputs': original['inputs'], 'deliverables': original['deliverables'], 'requirements': requirements, 'instruction': ref(batch / 'tasks' / fresh['label'] / 'instruction.md'), 'source_contract': ref(ROOT / 'validation/deb' / TASK / 'contract.yaml'), 'scope': 'Current blind witness is an actual input build but not a correct reference. Finite channel-default counterexample is conclusive. Existing unresolved formulas and failed old references remain explicit; no attempt to invent a complete solution or force a candidate through.', 'model_requests': 0})
    codes = list({str((ROOT / e['path']).resolve()): e for e in previous['code_refs'] + fresh['code_refs'] + [ref(Path(__file__))]}.values())
    bindingref = write(out / 'retained-binding.json', {'task_key': KEY, 'phase4_identity': ident, 'old_overlay': ref(OLD / TASK / 'audit-overlay-v1.json'), 'current_review': currentref, 'case_replay': replayref, 'reason': 'The current task instruction/image/native verifier binding equals the old record. Every retained code ref was rehashed and all available actual v3 case specs rerun without score changes. Partial coverage does not become full certification.'})
    overlays = {}
    for target in ('native', 'audit'):
        cs = deepcopy(base['native']['checks'] if target == 'native' else previous['checks'])
        for name, c in cs.items():
            if not c.get('execution_evidence') and name not in ('assets', 'contract'):
                c['execution_evidence'] = deepcopy(c['evidence'])
        for name in ('assets', 'contract', 'environment_agent', 'environment_verifier', 'cross_context', 'agent_visibility', 'stability'):
            cs[name] = {'status': 'pass', 'reason': 'Current locked-shell double blind build, raw input hash and complete typed submission/native-stage comparison actually executed; retained scoped contract observations remain unresolved where stated.', 'evidence': [freshref, currentref, contractref], 'execution_evidence': [freshref, currentref]}
        for name in ('reference', 'reference_repeat', 'independent_semantics'):
            cs[name] = {'status': 'fail', 'reason': 'Two clean blind builds are executable and native1 but violate explicit channel default weight/cost in all ten channels; independent raw arithmetic rejects all six actual snapshots. This is not a trustworthy correct witness.', 'evidence': [currentref, freshref], 'execution_evidence': [currentref, freshref]}
        cs['equivalent']['reason'] += ' Current blind witness was also executed and independently disproved; no complete lawful equivalent established. The previous actual finite equivalence experiments remain evidence, not a new unexecuted gap.'
        cs['equivalent']['evidence'] += [currentref]
        cs['equivalent']['execution_evidence'] += [currentref]
        if target == 'audit':
            cs['negatives']['evidence'] += [replayref, currentref]
            cs['negatives']['execution_evidence'] += [replayref, currentref]
            cs['comparator']['evidence'] += [ref(P / 'comparator-selftest-v3/result.json')]
            cs['comparator']['execution_evidence'] += [ref(P / 'comparator-selftest-v3/result.json')]
        coverage = deepcopy({r['id']: r['coverage']['native'] for r in original['audit']['requirements']} if target == 'native' else previous['requirement_coverage'])
        for r in coverage.values():
            r['evidence'] += [bindingref, replayref]
        if target == 'native':
            for rule in ('R-channel-current', 'R-channel-acquisition'):
                coverage[rule] = {'status': 'missed_wrong', 'case_validity': 'business_error', 'reason': 'New blind witness itself is independently wrong under explicit default channel CASE despite native1. No output mutation or tested-verifier circularity used.', 'evidence': [currentref, freshref], 'independent_proof': currentref, 'checker_evidence': fresh['records'][0]['result']}
        overlay = {'schema_version': 1, 'task_key': KEY, 'target': target, 'phase4_identity': ident, 'base_identity': ident['base_identity'], 'checker_version': ident['native_verifier']['sha256'] if target == 'native' else 'phase4-tiering-finite-rejection-v1', 'contract': contractref, 'review_status': 'reviewed', 'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs', 'checks': cs, 'requirement_coverage': coverage, 'code_refs': codes, 'model_requests': 0, 'scope': 'Complete protocol investigation, partial unqualified audit with conclusive current-candidate rejection. All 14 checks have executed evidence; audit remains pending because no correct reference or complete equivalent is established.'}
        path = out / (target + '-overlay.json')
        write(path, overlay)
        overlays[target] = path
        write(out / (target + '-precheck.json'), precheck(path))
    candidate = {'schema_version': 1, 'task_key': KEY, 'phase4_identity': ident, 'qualified_overlay': ref(overlays['audit']), 'checker_version': 'phase4-tiering-finite-rejection-v1', 'code_refs': codes, 'records': current, 'candidate_pass': False, 'model_requests': 0}
    cp = out / 'formal-candidate-review.json'
    write(cp, candidate)
    candidate_evaluation(cp, ident, rebuilt, qualified_overlay=io.read_json(overlays['audit']))
    handoff = {'task_key': KEY, 'native': ref(overlays['native']), 'audit': ref(overlays['audit']), 'candidate_review': ref(cp), 'reconciliation': rebuilt['reconciliation'], 'model_requests': 0, 'stage_by_root_only': True, 'eligible_for_sampling': False}
    write(out / 'handoff.json', handoff)
    print(handoff)
if __name__ == '__main__':
    main()
