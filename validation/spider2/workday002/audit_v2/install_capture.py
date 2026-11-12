"""Instrument an isolated task only; keep every previous verifier byte."""
import hashlib
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent


def install(task):
    task = Path(task)
    tests = task / 'tests'
    original = tests / 'audit-original-test.sh'
    if original.exists():raise ValueError('Capture wrapper already exists')
    inventory = {p.relative_to(tests).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in tests.rglob('*') if p.is_file()}
    (tests / 'test.sh').rename(original)
    shutil.copyfile(HERE / 'collect.py', tests / 'workday-audit-collect.py')
    (tests / 'test.sh').write_text('''#!/bin/bash
set +e
cd /workspace
python3 /tests/workday-audit-collect.py before-native
bash /tests/audit-original-test.sh
native_exit=$?
python3 /tests/workday-audit-collect.py after-native
exit "$native_exit"
''')
    for name, expected in inventory.items():
        p = original if name == 'test.sh' else tests / name
        if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError('Original verifier material changed: ' + name)
    return {'prior_verifier_files': inventory, 'collector_sha256': hashlib.sha256((HERE / 'collect.py').read_bytes()).hexdigest(),
            'scope': 'Read-only capture around the unchanged prior test script. Original score generation and exit code retained.'}
