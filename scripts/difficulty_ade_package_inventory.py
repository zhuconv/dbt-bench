"""Read-only hashes for packages resolved by the original native deps step."""
from pathlib import Path
import argparse
import hashlib
import json


def run(project,output):
    project,output=Path(project),Path(output);output.mkdir(parents=True,exist_ok=False)
    package=project/'dbt_packages';files=[]
    if not package.is_dir():raise ValueError('Native dependency resolution produced no package directory')
    for p in sorted(package.rglob('*')):
        if p.is_symlink():raise ValueError('Unbound package symlink')
        if p.is_file():
            with p.open('rb')as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            files.append({'path':str(p.relative_to(package)),'sha256':digest,'bytes':p.stat().st_size})
    value={'kind':'native_dbt_dependency_inventory','project':str(project),'files':files,
        'tree_sha256':hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'model_requests':0,'database_access':False}
    (output/'packages.json').write_text(json.dumps(value,indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);p.add_argument('--output',required=True);a=p.parse_args();run(a.project,a.output)
