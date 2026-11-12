"""Share immutable SIF build inputs; isolate instruction, solution and tests.

Harbor runs the already-built SIF. The environment directory is retained as
provenance and never edited by this workflow. Its identity is rechecked after
execution. Hardlinks avoid duplicating thousands of unused dependency files.
"""
import os
from pathlib import Path
import shutil


def copy_task(source, destination):
    source = Path(source).resolve()
    destination = Path(destination)

    def copy_file(src, dst):
        relative = Path(src).relative_to(source)
        if relative.parts[0] == 'environment':
            os.link(src, dst)
            return dst
        return shutil.copy2(src, dst)

    return shutil.copytree(source, destination, symlinks=True, copy_function=copy_file)
