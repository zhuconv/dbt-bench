"""Keep published review evidence stable while working review files evolve."""
from pathlib import Path
from dbt_bench import io


def archive_review_refs(value, root, archive, *, preserve_audit=False):
    """Archive mutable refs; native-only publication leaves audit branches exact."""
    root, archive = Path(root), Path(archive)
    archive.mkdir(parents=True, exist_ok=True)
    if isinstance(value, list):
        return [archive_review_refs(item, root, archive, preserve_audit=preserve_audit) for item in value]
    if not isinstance(value, dict):
        return value
    if isinstance(value.get('path'), str) and value.get('sha256'):
        source = root / value['path']
        destination = archive / value['sha256']
        mutable = any(marker in str(source.absolute()) for marker in (
            '/phase1-review-v1/', '/scripts/', '/validation/ade/', '/validation/deb/', '/validation/spider2/'))
        if mutable:
            if destination.exists():
                assert io.file_hash(destination) == value['sha256']
            else:
                assert io.evidence_ok(value, root), 'Changed evidence has no retained original: ' + str(source)
                destination.write_bytes(source.read_bytes())
                assert io.file_hash(destination) == value['sha256']
            return {**value, **io.evidence_ref(destination, root)}
    return {key: item if preserve_audit and key == 'audit' else
            archive_review_refs(item, root, archive, preserve_audit=preserve_audit)
            for key, item in value.items()}
