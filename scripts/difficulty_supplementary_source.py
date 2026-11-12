"""Verify supplementary source bytes; this never selects a grading project."""
from datetime import datetime
import hashlib
from pathlib import Path, PurePosixPath
import tarfile
from dbt_bench import io
from difficulty_submission_v2 import verify_capture, verify_absences


def verify(trial, key):
    trial = Path(trial); capture, _, _ = verify_capture(trial, key)
    folder = trial/'artifacts/difficulty-submission'
    rows = []
    for declared in capture.get('additional_projects', []):
        path = io.contained(folder, declared['capture_file'])
        if io.file_hash(path) != declared['capture_sha256']:
            raise ValueError('Supplementary capture changed')
        child = io.read_json(path)
        if (child['task_key'] != key or child['project_root'] != declared['path']
                or child['complete'] is not True or child['errors'] or child['databases']
                or declared['approved_as_equivalent_delivery'] is not False):
            raise ValueError('Supplementary project identity or declared scope changed')
        if not (datetime.fromisoformat(capture['started_at']) <= datetime.fromisoformat(child['started_at'])
                <= datetime.fromisoformat(child['finished_at']) <= datetime.fromisoformat(capture['finished_at'])):
            raise ValueError('Supplementary capture is outside the original pre-native window')
        verify_absences(child)
        archive = io.contained(path.parent, child['project_archive']['file'])
        if io.file_hash(archive) != child['project_archive']['sha256']:
            raise ValueError('Supplementary source archive changed')
        files = io.unique_index(child['project_files'], 'path')
        with tarfile.open(archive, 'r:gz') as stream:
            members = stream.getmembers()
            if len(members) != len(files) or {m.name for m in members} != set(files):
                raise ValueError('Supplementary archive inventory differs')
            for m in members:
                name = PurePosixPath(m.name); f = files[m.name]
                if (not m.isfile() or name.is_absolute() or '..' in name.parts or str(name) != m.name
                        or not f['unchanged'] or m.size != f['size']):
                    raise ValueError('Unsafe or changed supplementary member')
                if hashlib.sha256(stream.extractfile(m).read()).hexdigest() != f['sha256']:
                    raise ValueError('Supplementary source bytes changed')
        rows.append({'project_root': child['project_root'], 'files': files,
                     'capture': io.evidence_ref(path, Path.cwd()), 'archive': io.evidence_ref(archive, Path.cwd()),
                     'approved_as_equivalent_delivery': False})
    return rows
