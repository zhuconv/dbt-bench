"""Read the independently reviewed Phase3 delivery contracts without changing them."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from dbt_bench import io
MANIFEST = Path(_release_expand('${ARTIFACT_ROOT}/difficulty/qwen36-v1/deb-adapter-v1/delivery-contracts-phase3-v1/manifest.json'))

def resolve(context, root):
    root = Path(root)
    manifest = io.read_json(MANIFEST)
    key = context['binding']['task_key']
    matches = []
    for reference in manifest['contracts']:
        if not io.evidence_ok(reference, root):
            raise ValueError('Delivery review changed')
        row = io.read_json(root / reference['path'])
        if row['task_key'] == key:
            matches.append((row, reference))
    if len(matches) != 1:
        raise ValueError('Missing or duplicate delivery review')
    row, ref = matches[0]
    task = Path(context['prepared']['identity']['config']['tasks'][0]['path'])
    if row['task_version'] != context['binding']['task_version'] or row['qualified_review'] != context['binding']['qualified_review']:
        raise ValueError('Delivery policy has another task version/qualification')
    for field, filename in [('instruction', 'instruction.md'), ('native_build_path', 'tests/test_outputs.py'), ('native_entrypoint', 'tests/test.sh')]:
        if not io.evidence_ok(row[field], root) or io.file_hash(task / filename) != row[field]['sha256']:
            raise ValueError('Actual delivery instruction/native differs from reviewed version')
    if row['scoring_basis'] not in ('actual_database', 'submitted_source_reconstruction'):
        raise ValueError('Unsupported delivery mode')
    return (row, ref, io.evidence_ref(MANIFEST, root))
