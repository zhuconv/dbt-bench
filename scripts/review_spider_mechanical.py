#!/usr/bin/env python3
"""Bind executed Spider mechanical evidence; task reviewers retain semantic judgment."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from dbt_bench import io
from dbt_bench.catalog import load_catalog
from dbt_bench.validation import identity
from review_tools import ROOT, cached_hashes


def check(status, reason, evidence):
    assert status == 'pending' or evidence and all(io.evidence_ok(r, ROOT) for r in evidence)
    return {'status': status, 'reason': reason, 'evidence': evidence}


def load_batch(path):
    manifest, reconciliation = io.read_json(path/'manifest.json'), io.read_json(path/'reconciliation.json')
    assert reconciliation['execution_backed'] and not reconciliation['missing'] and not reconciliation['duplicates']
    assert reconciliation['manifest'] == io.evidence_ref(path/'manifest.json', ROOT)
    assert reconciliation['model_requests'] == 0
    trials = defaultdict(list)
    for trial in reconciliation['trials']:
        assert io.evidence_ok(trial['result'], ROOT)
        trials[trial['task_key']].append(trial)
    assert all(len(v) == 2 for v in trials.values())
    return manifest, reconciliation, trials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--nop-batch', type=Path, required=True)
    parser.add_argument('--witness-batch', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists() and str(args.output.resolve()).startswith('/scratch/')
    baseline = load_batch(args.batch)
    witnesses = {}
    for path in args.witness_batch:
        data = load_batch(path)
        for key, entry in data[0]['tasks'].items():
            assert key not in witnesses and entry['reference_kind'] == 'built_from_inputs'
            assert entry.get('mode') != 'fixed-clock', 'Clock diagnostics cannot replace real-clock reference observations'
            witnesses[key] = path, data
    nop_manifest = io.read_json(args.nop_batch/'manifest.json')
    nop_reconciliation = io.read_json(args.nop_batch/'reconciliation.json')
    assert nop_reconciliation['coverage_complete']
    nop_trials = defaultdict(list)
    for trial in nop_reconciliation['trials']:
        if trial['agent'] == 'nop':
            nop_trials[trial['task_key']].append(trial)
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    records = []
    args.output.mkdir(parents=True)
    with cached_hashes():
        for key in sorted(baseline[0]['tasks']):
            row = rows[key]
            directory = (ROOT/row['validation_ref']).parent
            current = identity(ROOT,row,directory/'contract.yaml','apptainer')
            base_entry = baseline[0]['tasks'][key]
            assert base_entry['backend_binding'] == current['backend_binding']
            assert base_entry['original_task_hash'] == current['task']
            batch, data = witnesses.get(key, (args.batch,baseline))
            entry = data[0]['tasks'][key]
            assert entry['backend_binding'] == current['backend_binding']
            assert entry['original_task_hash'] == current['task']
            for field in ('normalized_specification','visibility_specification','native_verifier','wrapper'):
                assert io.evidence_ok(entry[field],ROOT)
            observed = data[2][key]
            task_observation = next(t for t in data[1]['tasks'] if t['task_key'] == key)
            refs = [io.evidence_ref(batch/'manifest.json',ROOT),io.evidence_ref(batch/'reconciliation.json',ROOT)]
            # Keep the actually executed unmodified-project result, including
            # all eleven known environment failures, rather than hiding it
            # behind a successful copy-gold import.
            env = io.read_json(directory/'environment.json')
            assert io.evidence_ok(env['actual_agent_probe'],ROOT)
            probe = io.read_json(ROOT/env['actual_agent_probe']['path'])
            assert io.evidence_ok(probe['environment_identity'],ROOT)
            environment_check = deepcopy(probe['environment_agent'])
            assert all(io.evidence_ok(r,ROOT) for r in environment_check['evidence'])
            environment_check['evidence'].append(env['actual_agent_probe'])
            checks = {'assets':check('pass','Current task, image/environment binding and unchanged native verifier match the retained executions. Output coverage must also be reviewed against the task contract.',refs+[entry['normalized_specification'],entry['native_verifier']])}
            visibility, verifier_refs, shell_refs, reference_facts = [], [], [], []
            for index, trial in enumerate(observed):
                path = (ROOT/trial['result']['path']).parent
                assert not trial['exception_info'] and io.read_json(path/'agent/no-model.json')['model_requests'] == 0
                for method in ('context-pipe','context-pty'):
                    shell = io.read_json(path/'agent'/f'{method}-shell.json')
                    assert shell['exit_code'] == 0
                    context = __import__('json').loads(shell['output'].strip())
                    assert context['duckdb_version'] == '1.2.2'
                    assert all(d.get('rows') == [[1]] for d in context['databases'])
                    shell_refs.append(io.evidence_ref(path/'agent'/f'{method}-shell.json',ROOT))
                for phase in ('verifier_before','verifier_after'):
                    ref=trial['snapshots'][phase];assert io.evidence_ok(ref,ROOT)
                    context=io.read_json(ROOT/ref['path'])['context']
                    assert context['duckdb_version']=='1.2.2' and context['dbt']['version']['returncode']==0
                    assert all(d.get('rows')==[[1]] for d in context['databases'])
                    verifier_refs.append(ref)
                scan_dir=path/'agent/initial-visibility'
                scan,facts=io.read_json(scan_dir/'scan.json'),io.read_json(scan_dir/'facts.json')
                assert not scan['errors'] and not scan['matches']
                assert all(facts['declared_absent_paths'].values()) and not any(facts['host_paths_visible'].values())
                visibility.extend(io.evidence_ref(scan_dir/name,ROOT) for name in ('scan.json','facts.json','files.jsonl','mountinfo.txt','structured.json'))
                submission=io.read_json(ROOT/trial['snapshots']['submission']['path'])
                errors=[o['error'] for o in submission['outputs'] if o.get('error')]
                actual_shell=trial['shell_results']['reference']
                input_build=entry['reference_kind']=='built_from_inputs'
                passed=input_build and actual_shell['exit_code']==0 and not errors and trial['native_reward']=={'reward':1.0}
                reference_status='pass' if passed else 'fail' if input_build else 'gap'
                reason=('An actual clean input-based dbt candidate built the declared outputs and native reward is 1. Frozen dependencies were used; runtime dbt deps was not executed. Independent semantics and literal workflow requirements remain separately reviewed.' if passed else
                    'The actual input-based candidate failed to build required outputs or was rejected by the unchanged native verifier. Build errors and rejected-but-built outputs are recorded separately; no semantic verdict is inferred solely from reward.' if input_build else
                    'The locked Qwen shell actually ran the native copy-gold oracle. Accepted fixture output does not establish an input-based dbt reference; no true build is certified from this execution.')
                name='reference' if index==0 else 'reference_repeat'
                checks[name]=check(reference_status,reason,refs+[trial['result'],actual_shell['evidence'],trial['snapshots']['submission']])
                build=path/'agent/witness-build.json'
                build_ref=None
                if build.exists():
                    build_data=io.read_json(build)
                    assert build_data['model_requests']==0 and build_data['packages_unchanged']
                    assert build_data['mode']=='real-clock'
                    build_ref=io.evidence_ref(build,ROOT)
                    checks[name]['evidence'].append(build_ref)
                reference_facts.append({'trial_id':trial['trial_id'],'native_reward':trial['native_reward'],
                    'shell_exit':actual_shell['exit_code'],'output_errors':errors,'build':build_ref})
            checks['environment_agent']=environment_check
            checks['environment_agent']['evidence']+=shell_refs
            checks['environment_verifier']=check('pass','Actual native test.sh observer phases resolved dbt/Python/DuckDB and read each declared database; native rewards remain unchanged.',refs+verifier_refs)
            transfer=[t['producer_to_verifier']['equal'] for t in observed]
            checks['cross_context']=check('pass' if all(x is True for x in transfer) else 'fail',
                'Actual producer-to-verifier typed output comparisons: '+str(transfer)+'. Copy-gold transfer does not certify a dbt producer.',refs+[t['snapshots'][s] for t in observed for s in ('submission','verifier_before')])
            checks['agent_visibility']=check('pending','Actual initial filesystem/hash/mount scans were clean. Separate initial database metadata and task-specific interpretation are still required before a complete visibility judgment.',visibility)
            stable=task_observation['stability']['equal']
            built=entry['reference_kind']=='built_from_inputs'
            status=('pass' if stable is True else 'fail' if stable is False else 'gap') if built else 'gap'
            checks['stability']=check(status,
                'Two actual clean output comparisons gave '+str(stable)+'. '+('Input-based candidate output scope; missing outputs remain explicit.' if built else 'These are copy-gold fixtures, so input-based construction stability remains unestablished despite the executed fixture comparison.'),refs+[t['snapshots']['submission'] for t in observed])
            untouched=nop_trials[key]
            assert len(untouched)==2
            nop_entry=nop_manifest['tasks'][key]
            # Native reference preparation records its original backend binding.
            assert nop_entry.get('backend_binding',nop_entry.get('binding'))==current['backend_binding']
            nop_refs=[]
            for trial in untouched:
                ref=trial.get('result') or trial.get('raw_result')
                assert io.evidence_ok(ref,ROOT)
                assert io.read_json(ROOT/ref['path'])['verifier_result']['rewards']=={'reward':0.0}
                nop_refs.append(ref)
            checks['nop']=check('pass','Two retained clean untouched trials on the same current task/image have native reward 0.',nop_refs+[io.evidence_ref(args.nop_batch/'manifest.json',ROOT)])
            value={'schema_version':1,'task_key':key,'target':'native','base_identity':current,'checks':checks,
                'reference_facts':reference_facts,'reference_kind_observed':entry['reference_kind'],
                'source_batch':io.evidence_ref(batch/'manifest.json',ROOT),'model_requests':0,
                'scope':'Execution-backed mechanical baseline. Task reviewer must finish visibility and check capture coverage; native fixtures do not certify solvability.'}
            io.write_json(args.output/key/'mechanical.json',value)
            records.append({'task_key':key,'checks':{k:v['status'] for k,v in checks.items()},'review':io.evidence_ref(args.output/key/'mechanical.json',ROOT)})
            print(key,{k:v['status'] for k,v in checks.items()},flush=True)
    io.write_json(args.output/'summary.json',{'execution_backed':True,'tasks':records,'task_count':len(records),
        'check_counts':dict(Counter(s for r in records for s in r['checks'].values())),
        'model_requests':0,'canonical_modified':False,'reviewer':io.evidence_ref(Path(__file__),ROOT)})


if __name__=='__main__':main()
