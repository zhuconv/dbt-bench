"""Deployment coordinates only. They never supply scoring/model parameters."""
from pathlib import Path
import os
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]


def load(path=None):
    path = Path(path or os.environ.get('DBT_BENCH_LOCAL_CONFIG', ROOT/'configs/local.toml'))
    data = tomllib.loads(path.read_text()) if path.is_file() else {}
    data.setdefault('allocation', '')
    data.setdefault('partition', 'gh')
    defaults = {'repo_root': str(ROOT), 'home': str(Path.home()),
        'scratch_root': os.environ.get('SCRATCH', '/tmp/dbt-bench'),
        'work_root': os.environ.get('WORK', str(ROOT.parent))}
    defaults.update(data)
    defaults.setdefault('storage_root', defaults['scratch_root']+'/dbt-bench-storage')
    defaults.setdefault('artifact_root', defaults['storage_root']+'/artifacts')
    defaults.setdefault('cache_root', defaults['storage_root']+'/cache')
    defaults['allocation_lower'] = defaults['allocation'].casefold()
    for _ in range(4):
        for key,value in list(defaults.items()):
            if isinstance(value,str):
                defaults[key]=re.sub(r'\$\{([A-Z0-9_]+)\}',lambda m: str(defaults.get(m[1].lower(), os.environ.get(m[1],m[0]))),value)
    return defaults


def expand(value):
    if not isinstance(value,str):return value
    settings=load()
    return re.sub(r'\$\{([A-Z0-9_]+)\}',lambda m:str(settings.get(m[1].lower(),os.environ.get(m[1],m[0]))),value)


def required():
    settings=load()
    names=('allocation','artifact_root','cache_root','asset_map','runtime_python','vllm_executable','model_snapshot','qwen_bundle','apptainer_bin','cuda_home','cc','cxx')
    missing=[k for k in names if not settings.get(k) or '${' in str(settings[k])]
    if missing:raise ValueError('Set local config keys: '+', '.join(missing))
    return settings
