"""Create a new patched Harbor venv from the locked installed dependency base."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import venv
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from apply_runtime_patches import plan_updates


def install(output):
    import harbor
    if output.exists():raise ValueError('Output must be new; never patch a running/sealed runtime')
    chain=io.read_json(ROOT/'release/runtime-patches.json')
    if importlib.metadata.version('harbor')!=chain['harbor_version']:raise ValueError('Wrong Harbor version')
    source=Path(harbor.__file__).parent.resolve()
    output.mkdir(parents=True);venv.EnvBuilder(with_pip=False,symlinks=True).create(output/'venv')
    site=output/'venv/lib'/('python'+'.'.join(map(str,sys.version_info[:2])))/'site-packages'
    package=site/'harbor';shutil.copytree(source,package,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (site/'sealed-base-dependencies.pth').write_text(str(source.parent)+'\n')
    edits={}
    for binding in chain['patches']:
        if io.file_hash(ROOT/binding['path'])!=binding['sha256']:raise ValueError('Patch metadata changed')
        for edit in io.read_json(ROOT/binding['path'])['files']:
            edits.setdefault(package/edit['path'],[]).append(edit)
    for target,_,text in plan_updates(edits):target.write_text(text)
    for row in io.read_json(ROOT/chain['final_package_inventory']):
        if io.file_hash(package/row['path'])!=row['sha256']:raise ValueError('Final runtime differs: '+row['path'])
    binary=output/'venv/bin/harbor';binary.write_text('#!'+str(output/'venv/bin/python')+'\nfrom harbor.cli.main import app\napp()\n');binary.chmod(0o755)
    manifest={'python':str(output/'venv/bin/python'),'package':str(package),'source_dependency_base':str(source.parent),
        'chain':io.evidence_ref(ROOT/'release/runtime-patches.json',ROOT),'package_inventory':io.evidence_ref(ROOT/chain['final_package_inventory'],ROOT),
        'training_prohibited':True,'model_requests':0}
    io.write_json(output/'manifest.json',manifest);return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    print(json.dumps(install(p.parse_args().output.resolve()),indent=2))
