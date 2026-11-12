"""Bind an actual original submission before any optional audit reconstruction."""
from pathlib import Path
from dbt_bench import io
from difficulty_submission_v2 import verify_capture
from difficulty_submission import extract_source
from difficulty_deb_registry_v2 import qualified_entry,load,ROOT

def verify_original(run,trial,*,fixture=False,root=ROOT):
    run,trial,root=map(Path,(run,trial,root));m=io.read_json(run/'manifest.json');identity=m['identity']
    key=identity['task_key'];version=identity['task_version'];cfg=io.read_json(run/'config.yaml')
    if (m['experiment_id']!=io.digest(identity) or m['config_hash']!=io.file_hash(run/'config.yaml')
            or cfg!=identity['config'] or m.get('training_prohibited') is not True):
        raise ValueError('Prepared actual run identity changed')
    production=identity.get('reviewed_production') or identity['phase3_production']
    entry=production[key];review=entry.get('qualified_review') or entry['pool_row']['phase3_record']
    qualified=qualified_entry(review,key,version,root)
    reviewed=qualified['record'].get('phase4_identity') or qualified['record']['phase3_identity']
    task=Path(cfg['tasks'][0]['path'])
    if (io.tree_hash(task)!=identity['actual_task_hash'] or io.tree_hash(task/'tests')!=identity['native_tests_hash']
            or io.file_hash(task/'instruction.md')!=reviewed['instruction_sha256']):
        raise ValueError('Actual task/version/native verifier changed')
    raw=io.read_json(trial/'result.json')
    if Path(raw['config']['task']['path']).resolve()!=task.resolve():raise ValueError('Wrong original task')
    if not fixture:
        if identity['purpose']!='difficulty_only_no_training':raise ValueError('Not a planned real difficulty attempt')
        if trial.parent.resolve()!=(run/'jobs'/cfg['job_name']).resolve():raise ValueError('Original trial outside planned job')
        expected=cfg['agents'][0];actual=raw['config']['agent']
        if actual.get('name')!='qwen-coder' or actual.get('import_path') or any(actual.get(k)!=expected.get(k) for k in ('name','model_name','base_url','kwargs')):
            raise ValueError('Original agent differs from planned Qwen configuration')
    capture,archive,files=verify_capture(trial,key)
    if capture['project_root']!=identity['capture_spec']['project_root']:raise ValueError('Captured undeclared project root')
    image=entry['image']
    if not io.evidence_ok(image,root):raise ValueError('Locked image changed')
    return {'run':run,'trial':trial,'prepared':m,'task_key':key,'task_version':version,'original':raw,
        'original_result':io.evidence_ref(trial/'result.json',root),'qualified_review':review,
        'qualified':qualified,'reviewed_identity':reviewed,'capture':capture,'archive':archive,'files':files,
        'capture_ref':io.evidence_ref(trial/'artifacts/difficulty-submission/capture.json',root),
        'image':image,'production':production,'fixture_only':fixture}
