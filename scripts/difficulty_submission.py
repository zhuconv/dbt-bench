"""Verify and unpack actual native pre-verifier captures for isolated audits."""
from datetime import datetime,timezone
import hashlib
from pathlib import Path,PurePosixPath
import tarfile
from dbt_bench import io


def verify_capture(trial,task_key):
    trial=Path(trial);folder=trial/'artifacts/difficulty-submission'
    native=io.read_json(trial/'artifacts/manifest.json')
    entries=[x for x in native if x['source']=='/tmp/dbt-bench-difficulty-submission']
    if (len(entries)!=1 or entries[0]['status']!='ok'
            or entries[0]['destination']!='artifacts/difficulty-submission'
            or entries[0]['type']!='directory'):
        raise ValueError('Native submission capture absent, duplicated or failed')
    capture=io.read_json(folder/'capture.json')
    if (capture['task_key']!=task_key or capture['stage']!='agent-submission-before-native'
            or not capture['complete'] or capture['errors']):
        raise ValueError('Submission capture incomplete or bound to another task')
    for entry in capture['databases']:
        if entry['exists']:
            if (not entry['unchanged'] or entry['source_before']!=entry['sha256']
                    or entry['source_after']!=entry['sha256']
                    or io.file_hash(io.contained(folder,entry['file']))!=entry['sha256']):
                raise ValueError('Captured database changed')
    archive=io.contained(folder,capture['project_archive']['file'])
    if io.file_hash(archive)!=capture['project_archive']['sha256']:
        raise ValueError('Project archive changed')
    files=io.unique_index(capture['project_files'],'path')
    if any(not x['unchanged'] for x in files.values()):raise ValueError('Source mutated while collecting')
    result=io.read_json(trial/'result.json')
    def stamp(value):
        x=datetime.fromisoformat(value.replace('Z','+00:00'))
        return x.replace(tzinfo=timezone.utc).timestamp() if x.tzinfo is None else x.timestamp()
    agent=result.get('agent_execution') or {};verifier=result.get('verifier') or {}
    if not agent.get('finished_at') or not verifier.get('started_at'):
        raise ValueError('Missing actual agent/verifier chronology')
    if not (stamp(agent['finished_at'])<=stamp(capture['started_at'])
            <=stamp(capture['finished_at'])<=stamp(verifier['started_at'])):
        raise ValueError('Capture did not occur between actual agent and verifier')
    return capture,archive,files


def extract_source(trial,task_key,output):
    output=Path(output)
    if output.exists():raise ValueError('Preserve extracted submissions; select a new path')
    capture,archive,files=verify_capture(trial,task_key)
    payload=[]
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers()
        if len({x.name for x in members})!=len(members) or {x.name for x in members}!=set(files):
            raise ValueError('Archive/source inventory mismatch or duplicate')
        for member in members:
            name=PurePosixPath(member.name)
            if (not member.isfile() or name.is_absolute() or '..' in name.parts
                    or str(name)!=member.name or member.size!=files[member.name]['size']):
                raise ValueError('Unsafe or substituted source member')
            data=tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest()!=files[member.name]['sha256']:
                raise ValueError('Source bytes differ from captured manifest')
            payload.append((name,data))
    output.mkdir(parents=True)
    for name,data in payload:
        destination=output/str(name);destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data)
    return {'task_key':task_key,'project_root':capture['project_root'],
            'source_dir':str(output),'capture':io.evidence_ref(Path(trial)/'artifacts/difficulty-submission/capture.json',Path.cwd()),
            'source_files':[{**row,'captured_source':io.evidence_ref(output/name,Path.cwd())} for name,row in sorted(files.items())],
            'training_prohibited':True,'model_requests':0,
            'scope':'Exact pre-native model submission source; candidate-generated target artifacts remain untrusted and must be discarded in any fresh audit build.'}
