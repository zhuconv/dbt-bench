"""Shared read-only evidence helpers for root reviews and batch publication."""
from contextlib import contextmanager
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench import io


@contextmanager
def cached_hashes():
    """Hash shared files once per invocation, invalidating on stat changes.

    No persistent hash cache or trust in a previous process. Recheck metadata
    before and after reading; concurrent modifications fail closed.
    """
    original = io.file_hash
    cache = {}
    def hashed(path):
        p = Path(path).resolve()
        # GPFS/Lustre metadata can have coarser effective timestamps than the
        # nanosecond API suggests. Small mutable evidence must always be read.
        # Only explicitly immutable container images use this invocation cache.
        if p.suffix != '.sif':
            return original(p)
        def state():
            s = p.stat()
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        before = state()
        key = (str(p), before)
        if key not in cache:
            value = original(p)
            if state() != before:
                raise ValueError('File changed during hashing: ' + str(p))
            cache[key] = value
        return cache[key]
    changed = []
    for module in list(sys.modules.values()):
        if getattr(module, '__name__', '').startswith('dbt_bench') and getattr(module, 'file_hash', None) is original:
            changed.append(module)
            module.file_hash = hashed
    try:
        yield
    finally:
        for module in changed:
            module.file_hash = original


def terminal_result_paths(batch, continuations=()):
    """Associate completion jobs with a batch without modifying any raw file."""
    batch = Path(batch)
    result = [p for p in (batch / 'jobs').rglob('result.json') if 'trial_name' in io.read_json(p)]
    original_refs = {str(p.resolve()): io.file_hash(p) for p in result}
    for continuation in continuations:
        continuation = Path(continuation)
        manifest = io.read_json(continuation / 'completion.json')
        assert io.evidence_ok(manifest['stopped_evidence'], ROOT)
        assert manifest['original_config'] == io.evidence_ref(batch / 'config.yaml', ROOT)
        assert manifest['original_manifest'] == io.evidence_ref(batch / 'manifest.json', ROOT)
        assert all(io.evidence_ok(r, ROOT) for r in manifest['retained_results'])
        assert {str((ROOT / r['path']).resolve()): r['sha256'] for r in manifest['retained_results']} == original_refs
        for plan in manifest['plans']:
            assert io.evidence_ok(plan['config'], ROOT)
            cfg = io.read_json(ROOT / plan['config']['path'])
            from prepare_batch_completion import validate_completion_config
            validate_completion_config(io.read_json(batch / 'config.yaml'), cfg)
            job = Path(cfg['jobs_dir']) / cfg['job_name']
            assert io.read_json(job / 'result.json')['finished_at']
            paths = [p for p in job.glob('*/result.json') if 'trial_name' in io.read_json(p)]
            assert len(paths) == plan['planned_trials']
            from prepare_batch_completion import missing_slots
            assert not missing_slots(cfg, [io.read_json(p) for p in paths])
            result.extend(paths)
    ids = [io.read_json(p)['id'] for p in result]
    assert len(ids) == len(set(ids)), 'Duplicate trial IDs across completion jobs'
    if continuations:
        from prepare_batch_completion import missing_slots
        assert not missing_slots(io.read_json(batch / 'config.yaml'), [io.read_json(p) for p in result])
    return sorted(result)
