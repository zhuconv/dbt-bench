"""Compare diagnostic findings without depending on process-specific set order."""
from collections import Counter
import json


def same_differences(left, right):
    def counts(values):
        return Counter(json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values)
    return counts(left) == counts(right)
