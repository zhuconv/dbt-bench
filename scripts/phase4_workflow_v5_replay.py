"""Replay frozen task scorers with only their workflow helper dependency replaced.

Original modules and artifacts stay immutable. Clone function globals instead of
patching shared modules: task-specific semantic policies retain their own version.
"""
from pathlib import Path
import importlib.util
import sys
from types import FunctionType

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'validation/deb/dbt-customer-cohort-retention/phase4_v1'
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(SHARED)]
import review_support_v5 as support


def with_workflow_v5(function):
    globals_ = {**function.__globals__, 'workflow': support.workflow}
    # Revised business reviewers call this wrapper as well as workflow directly.
    if 'check_workflow' in globals_:
        globals_['check_workflow'] = with_workflow_v5(globals_['check_workflow'])
    result = FunctionType(function.__code__, globals_, function.__name__,
                          function.__defaults__, function.__closure__)
    result.__kwdefaults__ = function.__kwdefaults__
    return result


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def driver_for(record):
    """Select the exact scorer recorded in an old result, not the latest policy."""
    refs = record['checker_code']
    revisions = [ROOT / r['path'] for r in refs if Path(r['path']).name.startswith('run_revision')]
    seals = [ROOT / r['path'] for r in refs if Path(r['path']).name.startswith('seal_candidate')]
    if revisions:
        if len(revisions) != 1:
            raise ValueError('Ambiguous frozen revision scorer')
        return load_module(revisions[0], 'workflow_v5_frozen_revision'), 'revision'
    if len(seals) != 1:
        raise ValueError('Ambiguous frozen candidate scorer')
    return load_module(seals[0], 'workflow_v5_frozen_candidate'), 'candidate'
