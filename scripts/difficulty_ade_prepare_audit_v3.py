"""Preserve native deps-before-build when actual delivered packages require it."""
from pathlib import Path
import shutil
import yaml
from dbt_bench import io
from difficulty_ade_prepare_audit_v2 import prepare as prepare_prior

ROOT=Path(__file__).resolve().parents[1]


def locked_dependencies(extracted):
    files={x['path']:ROOT/x['captured_source']['path']for x in extracted['source_files']}
    if 'packages.yml'not in files or 'package-lock.yml'not in files:
        raise ValueError('Both original package declaration and resolved lock are required')
    requested=yaml.safe_load(files['packages.yml'].read_text())['packages']
    locked=yaml.safe_load(files['package-lock.yml'].read_text())['packages']
    pairs=[]
    for row in requested:
        name,version=row.get('package'),row.get('version')
        if not isinstance(name,str)or not isinstance(version,str)or any(x in version for x in ('>','<','*','^','~',',')):
            raise ValueError('Only original exactly versioned registry dependencies are supported')
        matches=[x for x in locked if x.get('package')==name and x.get('version')==version]
        if len(matches)!=1:raise ValueError('Package declaration differs from resolved original lock')
        pairs.append({'package':name,'version':version})
    if not pairs or len(pairs)!=len(locked):raise ValueError('Incomplete package closure')
    return {'resolved':pairs,'declaration':io.evidence_ref(files['packages.yml'],ROOT),'lock':io.evidence_ref(files['package-lock.yml'],ROOT)}


def prepare(run,trial,output,fixture=False):
    output=Path(output);result=prepare_prior(run,trial,output,fixture=fixture)
    binding=io.read_json(output/'source-binding.json');dependencies=locked_dependencies(binding['extracted'])
    original=io.read_json(Path(run)/'manifest.json');native=Path(original['identity']['config']['tasks'][0]['path'])/'tests/test-setup.sh'
    if not native.is_file()or 'dbt deps'not in native.read_text().splitlines():
        raise ValueError('No reviewed native dependency-setup command')
    batch=output/'batch';bm=io.read_json(batch/'manifest.json');label,entry=next(iter(bm['tasks'].items()));task=batch/'tasks'/label;payload=task/'audit-rebuild';rp=payload/'request.json';r=io.read_json(rp);requests=io.read_json(output/'requests.json')
    history=output/'native-deps-preparation-before';history.mkdir();shutil.copyfile(batch/'manifest.json',history/'manifest.json');shutil.copyfile(rp,history/'request.json')
    if any(c[:2]==['dbt','deps']for c in r['commands']):raise ValueError('Dependency step already present')
    for request in (r,requests['tasks'][0]['rebuild']):
        request['commands'].insert(0,['dbt','deps'])
        for hook in request['capture_hooks']:
            if hook.get('command_index')is not None:hook['command_index']+=1
    source=ROOT/'scripts/difficulty_ade_package_inventory.py';name=f"capture-hook-{len(r['capture_hooks']):02d}.py";shutil.copyfile(source,payload/name)
    args=['--project',r['project_root'],'--output','{output}']
    r['capture_hooks'].append({'phase':'final','args':args,'payload_path':name,'sha256':io.file_hash(source)})
    requests['tasks'][0]['rebuild']['capture_hooks'].append({'phase':'final','args':args,'source':io.evidence_ref(source,ROOT)})
    io.write_json(rp,r);io.write_json(output/'requests.json',requests)
    entry.update(request=io.evidence_ref(rp,ROOT),validation_copy_hash=io.tree_hash(task))
    bm.update(requests=io.evidence_ref(output/'requests.json',ROOT),native_dependency_setup={
        'native_setup':io.evidence_ref(native,ROOT),'dependencies':dependencies,
        'preparer':io.evidence_ref(Path(__file__),ROOT),'collector':io.evidence_ref(source,ROOT),
        'previous_unlaunched_preparation':io.evidence_ref(history/'manifest.json',ROOT),
        'scope':'Original exact package pins, unchanged delivered source/profile/model paths. Restore the dependency step already in the native verifier; no model retry.'})
    io.write_json(batch/'manifest.json',bm)
    return result
