"""Explicit per-platform identities; architecture equivalence is a user decision."""
from copy import deepcopy
from pathlib import Path
import platform
import subprocess
from dbt_bench import io

PLATFORMS={'linux-arm64':'aarch64','linux-amd64':'x86_64'}


def platform_name(settings):
    value=settings.get('platform','linux-arm64')
    if value not in PLATFORMS:raise ValueError('Unsupported platform')
    return value


def runtime_inventory(settings,root):
    return root/('release/harbor-amd64-package-files.json' if platform_name(settings)=='linux-amd64' else 'release/harbor-package-files.json')


def check_host(settings):
    expected=PLATFORMS[platform_name(settings)]
    if platform.system()!='Linux' or platform.machine()!=expected:
        raise ValueError('Configured platform differs from execution host')


def image_identity(row, settings, root):
    name=platform_name(settings)
    assets={v['id']:v for v in io.read_json(root/'release/assets.json')['assets']}
    if name=='linux-arm64':
        a=assets[row['image_asset']];d=a['distribution']
        return {'canonical_asset':a['id'],'platform':name,'asset_id':a['id'],
            'sif_sha256':a['sha256'],'uri':d['uri'],'oci_digest':d['registry_manifest_digest']}
    registry=io.read_json(root/'images/platform-images.json')
    entries=[v for v in registry['images'] if v['canonical_asset']==row['image_asset'] and v['platform']==name]
    if len(entries)!=1 or entries[0].get('status')!='published':
        raise ValueError('No published and digest-locked image for '+row['image_asset']+' / '+name)
    value=entries[0]
    if not value.get('sif_sha256') or not value.get('oci_digest'):raise ValueError('Image digest missing')
    return {k:value[k] for k in ('canonical_asset','platform','asset_id','sif_sha256','uri','oci_digest')}


def qwen_pin(name,root):
    if name=='linux-arm64':return io.read_json(root/'sources.lock.yaml')['qwen_code']['installation_bundle']
    return io.read_json(root/'runtime/qwen-code/linux-x64/bundle.lock.json')


def bind_bundle(config, settings, root):
    name=platform_name(settings)
    if name=='linux-arm64':
        from dbt_bench.prepare import bind_qwen_bundle
        return bind_qwen_bundle(root,config,settings['qwen_bundle'])
    pin=qwen_pin(name,root);path=Path(settings['qwen_bundle']).resolve();m=io.read_json(path)
    if any(m.get(k)!=pin[k] for k in ('kind','platform','qwen_version','node_version','npm_version','locks')):
        raise ValueError('Qwen platform/versions/locks differ')
    for entry in pin['locks']:
        if not io.evidence_ok(entry,root):raise ValueError('Qwen lock changed')
    for k in ('archive','inventory','installer'):
        item=m['files'][k]
        if item['sha256']!=pin['files'][k] or io.file_hash(io.contained(path.parent,item['path']))!=pin['files'][k]:
            raise ValueError('Qwen payload mismatch: '+k)
    for a in config['agents']:
        if a['name']!='qwen-coder' or a['kwargs']['version']!=m['qwen_version']:raise ValueError('Wrong native agent')
        a['kwargs'].update(installation_bundle=str(path),installation_bundle_sha256=io.file_hash(path))


def code_identity(root):
    commit=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],check=True,text=True,capture_output=True).stdout.strip()
    files=[]
    for folder in ('scripts','src','runtime/qwen-code'):
        for p in sorted((root/folder).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.json'):
                if p.name in ('worker.py','ledger_check.py'):continue
                files.append({'path':str(p.relative_to(root)),'sha256':io.file_hash(p)})
    tracked=subprocess.run(['git','-C',str(root),'diff','--name-only','HEAD','--','scripts','src','runtime/qwen-code'],check=True,text=True,capture_output=True).stdout.splitlines()
    untracked=subprocess.run(['git','-C',str(root),'ls-files','--others','--exclude-standard','--','scripts','src','runtime/qwen-code'],check=True,text=True,capture_output=True).stdout.splitlines()
    changed=set(tracked+untracked)&{v['path'] for v in files}
    return {'commit':commit,'worktree_differs_from_commit':bool(changed),'files':files,'content_id':io.digest(files)}


def verify_import_platform(identity, proof, row, root):
    name=platform_name(identity['deployment']);expected=image_identity(row,identity['deployment'],root)
    if identity['image_identity']!=expected:raise ValueError('Unapproved image/platform identity')
    pin=qwen_pin(name,root);m=io.read_json(proof/'bundle.json');q=identity['qwen_installation']
    if q['content_id']!=io.digest(q['content']) or q['manifest_sha256']!=io.file_hash(proof/'bundle.json'):
        raise ValueError('Qwen receipt differs')
    if q['manifest_sha256']!=identity['config']['agents'][0]['kwargs']['installation_bundle_sha256']:
        raise ValueError('Config does not bind verified Qwen manifest')
    content={k:m[k] for k in ('kind','platform','qwen_version','node_version','npm_version')}
    content.update(files={k:m['files'][k]['sha256'] for k in ('archive','inventory','installer')},locks=m['locks'])
    if content!=q['content'] or content['files']!=pin['files'] or content['locks']!=pin['locks']:
        raise ValueError('Qwen installation payload differs')
    for key,wanted in {'platform':pin['platform'],'kind':'dbt-bench-qwen-bundle-v1','qwen_version':'0.26.5','node_version':pin['node_version'],'npm_version':pin['npm_version']}.items():
        if m[key]!=wanted:raise ValueError('Qwen platform/version mismatch')
    for key in ('inventory','installer'):
        if io.file_hash(proof/Path(m['files'][key]['path']).name)!=pin['files'][key]:raise ValueError('Qwen inventory/installer differs')
    code=identity['code_identity']
    if io.digest(code['files'])!=code['content_id']:raise ValueError('Code identity invalid')
    expected_paths={f['path'] for f in code_identity(root)['files']}
    if len(code['files'])!=len(expected_paths) or {f['path'] for f in code['files']}!=expected_paths:raise ValueError('Incomplete code inventory')
    for f in code['files']:
        if io.file_hash(io.contained(root,f['path']))!=f['sha256']:raise ValueError('Unreviewed code version: '+f['path'])
    runtime=identity['runtime_patch']['package_inventory']
    if runtime['sha256']!=io.file_hash(runtime_inventory(identity['deployment'],root)):
        raise ValueError('Runtime package inventory mismatch')
