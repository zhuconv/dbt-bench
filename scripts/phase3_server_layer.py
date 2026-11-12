#!/usr/bin/env python3
"""Add only locked Harbor transport dependencies to an immutable ADE SIF."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess
from dbt_bench import io
from review_tools import ROOT

PROBE = '''import hashlib,importlib.metadata,json,os
from pathlib import Path
def files(base):
    result=[]
    for p in sorted(Path(base).rglob('*')):
        if p.is_symlink(): result.append([str(p.relative_to(base)), 'link',os.readlink(p)])
        elif p.is_file():
            with p.open('rb') as f: value=hashlib.file_digest(f,'sha256').hexdigest()
            result.append([str(p.relative_to(base)),value,p.stat().st_mode&0o777])
    return result
print(json.dumps({'files':{p:files(p) for p in ['/app','/scripts','/opt/dbt-bench-initial']},
 'python':__import__('sys').version,'packages':sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions()),
 'server_exists':Path('/opt/harbor-server/bin/python').exists()},sort_keys=True))
'''


def build(before, output):
    before, output = Path(before).resolve(), Path(output).resolve()
    if output.exists() or not str(output).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH directory')
    apptainer = '/opt/apps/tacc-apptainer/1.4.1/bin/apptainer'
    output.mkdir(parents=True)
    shutil.copyfile(__file__, output / 'builder.py')
    lock = ROOT / 'environments/apptainer/harbor-server-requirements.txt'
    shutil.copyfile(lock, output / 'requirements.txt')
    (output / 'probe.py').write_text(PROBE)
    definition = output / 'image.def'
    definition.write_text(f'''Bootstrap: localimage
From: {before}

%files
    {output / 'requirements.txt'} /opt/dbt-bench-phase3-server-requirements.txt

%post
    set -eu
    test ! -e /opt/harbor-server
    /usr/local/bin/python3 -m venv /opt/harbor-server
    /opt/harbor-server/bin/python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /opt/dbt-bench-phase3-server-requirements.txt
    /opt/harbor-server/bin/python -m pip freeze
    rm /opt/dbt-bench-phase3-server-requirements.txt
    for folder in dbt_packages target logs; do
        if [ -d "/opt/dbt-bench-initial/$folder" ]; then rm -rf "/app/$folder"; fi
    done
''')
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    env.update(APPTAINER_TMPDIR=str(output / 'tmp'), PYTHONDONTWRITEBYTECODE='1')
    (output / 'tmp').mkdir()
    after = output / 'task.sif'
    with (output / 'build.log').open('wb') as log:
        run = subprocess.run([apptainer, 'build', '--fakeroot', str(after), str(definition)],
                             env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    if run.returncode:
        raise ValueError('Image layer build failed; retain build.log')
    observations = {}
    for name, image in [('before', before), ('after', after)]:
        command = [apptainer, 'exec', '--fakeroot', '--writable-tmpfs', '--pid',
            '--no-mount', 'home,tmp,cwd,hostfs,bind-paths', '--bind', str(output) + ':/review:ro',
            str(image), '/usr/local/bin/python3', '/review/probe.py']
        r = subprocess.run(command, env=env, capture_output=True, timeout=300)
        (output / (name + '.stderr')).write_bytes(r.stderr)
        (output / (name + '.json')).write_bytes(r.stdout)
        if r.returncode:
            raise ValueError('Image comparison failed: ' + name)
        observations[name] = io.read_json(output / (name + '.json'))
    old, new = observations['before'], observations['after']
    same = all(old[k] == new[k] for k in ('files', 'python', 'packages'))
    if not same or old['server_exists'] or not new['server_exists']:
        raise ValueError('Transport layer changed task software/data or did not install transport')
    result = {'schema_version': 1, 'passed': True, 'patch_type': 'environment_transport_only',
        'before_image': io.evidence_ref(before, ROOT), 'after_image': io.evidence_ref(after, ROOT),
        'task_files_exact': True, 'task_packages_exact': True, 'task_python_exact': True,
        'before_probe': io.evidence_ref(output / 'before.json', ROOT),
        'after_probe': io.evidence_ref(output / 'after.json', ROOT),
        'builder': io.evidence_ref(output / 'builder.py', ROOT),
        'probe': io.evidence_ref(output / 'probe.py', ROOT),
        'lock': io.evidence_ref(output / 'requirements.txt', ROOT),
        'definition': io.evidence_ref(definition, ROOT), 'build_log': io.evidence_ref(output / 'build.log', ROOT),
        'model_requests': 0, 'old_bindings_modified': False,
        'scope': 'Exact initialized task files/packages and isolated server layer. Formal Qwen/verifier probes remain required.'}
    io.write_json(output / 'summary.json', result)
    return {'passed': True, 'summary': str(output / 'summary.json')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(build(a.before, a.output))
