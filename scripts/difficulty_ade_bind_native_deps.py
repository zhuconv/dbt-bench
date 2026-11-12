"""Seal native dependency preparation code into an unlaunched audit manifest."""
from pathlib import Path
import argparse
from dbt_bench import io
ROOT=Path(__file__).resolve().parents[1]

def bind(directory):
    directory=Path(directory);batch=directory/'batch';path=batch/'manifest.json'
    if (batch/'jobs').exists()or any((directory/n).exists()for n in ('launch.json','submission.json','preflight.json')):
        raise ValueError('Audit already launched or sealed')
    m=io.read_json(path);deps=m['native_dependency_setup']
    refs=[deps['preparer'],deps['collector'],io.evidence_ref(Path(__file__),ROOT)]
    if any(not io.evidence_ok(r,ROOT)for r in refs):raise ValueError('Preparation dependency changed')
    before=directory/'native-deps-binding-before.json'
    if before.exists():raise ValueError('Already bound')
    io.write_json(before,m)
    m['tools'].extend(r for r in refs if r not in m['tools'])
    m['native_dependency_setup']['binding_prior']=io.evidence_ref(before,ROOT)
    io.write_json(path,m)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--audit-dir',type=Path,required=True);bind(p.parse_args().audit_dir)
