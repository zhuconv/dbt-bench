"""Place Harbor's transient sanitized copy on node-local storage."""
from contextlib import contextmanager
from pathlib import Path
import tempfile


@contextmanager
def local_artifact_alias(artifacts):
    original = Path(artifacts).resolve(strict=True)
    if not original.is_dir(): raise ValueError('Expected retained artifact directory')
    with tempfile.TemporaryDirectory(prefix='session-replay-transfer-', dir='/tmp') as temporary:
        alias = Path(temporary) / 'artifacts'
        alias.symlink_to(original, target_is_directory=True)
        # Harbor reads children through this alias and stages beside it. Its
        # original tar data filter and upload implementation remain in use.
        yield alias
