"""Per-task source-delivery contracts for the fifteen frozen ADE candidates."""
from pathlib import Path
from dbt_bench import io
ROOT=Path(__file__).resolve().parents[1]
ENTRIES={
 **{'ade__ade-bench-ana-eng'+x:{'policy':'policy-v3.json','checker':'check_v3.py','database':'/app/analytics_engineering.duckdb','submission_kind':'dbt_model_source'}for x in ('003','004','005','006')},
 **{'ade__ade-bench-airbnb'+x:{'policy':'policy-v2.json','checker':'check_v2.py','database':'/app/airbnb.duckdb','submission_kind':'dbt_model_source'}for x in ('005','007')},
 **{'ade__ade-bench-intercom'+x:{'policy':'policy.json','checker':'check.py','database':'/app/intercom.duckdb','submission_kind':'dbt_model_source'}for x in ('001','002','003')},
 **{'ade__ade-bench-'+x:{'policy':'policy.json','checker':'check.py','database':'/app/f1.duckdb','submission_kind':'diagnostic_dbt_model_source'if x in ('f1003','f1003-hard','f1011')else'dbt_model_source','clock_sensitive':x=='f1009'}for x in ('f1003','f1003-hard','f1009','f1010','f1010-medium','f1011')},
}
def contract_record(qualified):
 key=qualified['task_key'];entry=ENTRIES[key];ident=qualified['phase3_identity'];batch=Path(ident['batch_manifest']['path']).parent;task=batch/'tasks'/ident['label'];setup=task/'tests/test-setup.sh';native=task/'tests/mechanical-native-test.sh';instruction=task/'instruction.md'
 text=setup.read_text()
 if 'dbt run'not in text or 'bash /tests/test-setup.sh'not in native.read_text():raise ValueError('Reviewed native source-materialization path changed')
 return {**entry,'task_key':key,'instruction':io.evidence_ref(instruction,ROOT),'native_test_setup':io.evidence_ref(setup,ROOT),'native_wrapper':io.evidence_ref(native,ROOT),'native_verifier_builds':True,'actual_model_dbt_execution_required':False,'actual_pre_native_database_required_for_score':False,'preserve_original_database':True,'reason':'Task requests delivered dbt models (including diagnostic answer models); unchanged native test-setup materializes those models. Clean exact-source build evaluates delivery, not whether the model issued build commands.','scope':'These fifteen reviewed ADE contracts only; not inferred from benchmark name and not reusable for actual-table or explicit agent-command contracts.'}
