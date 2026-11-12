"""Wrap only an isolated verifier, preserving original bytes and exit status."""
import hashlib
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
WRAPPER = '''#!/bin/bash
set +e
tests_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "$tests_dir/session-audit-collect.py" before-native
bash "$tests_dir/audit-original-test.sh"
native_exit=$?
python3 "$tests_dir/session-audit-collect.py" after-native
exit "$native_exit"
'''


def install(task):
    tests = Path(task) / 'tests'
    original = tests / 'audit-original-test.sh'
    if original.exists() or (tests / 'session-audit-collect.py').exists(): raise ValueError('Capture already installed')
    inventory = {p.relative_to(tests).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in tests.rglob('*') if p.is_file()}
    (tests / 'test.sh').rename(original)
    shutil.copyfile(HERE / 'collect.py', tests / 'session-audit-collect.py')
    (tests / 'test.sh').write_text(WRAPPER)
    for name, expected in inventory.items():
        p = original if name == 'test.sh' else tests / name
        if hashlib.sha256(p.read_bytes()).hexdigest() != expected: raise ValueError('Prior verifier bytes changed')
    return {'prior_verifier_files': inventory, 'collector_sha256': hashlib.sha256((HERE / 'collect.py').read_bytes()).hexdigest(),
            'scope': 'Read-only exports around the unchanged prior verifier. Native reward and exit status retained, including collector failure.'}
