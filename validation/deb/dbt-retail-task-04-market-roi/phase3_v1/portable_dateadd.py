"""Accept dbt's pinned portable DATEADD call, not hand-written business intervals."""
from pathlib import Path
import hashlib
import json
import re
from jinja2 import Environment, nodes


def portable_dateadd(source, manifest):
    tree = Environment(extensions=['jinja2.ext.do']).parse(source)
    calls = [n for n in tree.find_all(nodes.Call)
             if isinstance(n.node, nodes.Getattr) and n.node.attr == 'dateadd'
             and isinstance(n.node.node, nodes.Name) and n.node.node.name == 'dbt']
    if not calls:
        return False
    for call in calls:
        if (call.kwargs or call.dyn_args or call.dyn_kwargs or len(call.args) != 3
                or not all(isinstance(a, nodes.Const) for a in call.args)
                or call.args[0].value != 'day' or type(call.args[1].value) is not int
                or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?', str(call.args[2].value))):
            raise ValueError('Portable DATEADD expression requires separate review')
    literals = '\n'.join(n.data for n in tree.find_all(nodes.TemplateData))
    literals = re.sub(r'/\*.*?\*/|--[^\n]*', '', literals, flags=re.S)
    if re.search(r'\binterval\s+(?:[0-9]|\')', literals, re.I):
        return False
    policy = json.loads(Path(__file__).with_name('policy.json').read_text())
    for name, expected_hash in policy['portable_dateadd_macros'].items():
        matches = [m for m in manifest['macros'].values() if m['name'] == name]
        if len(matches) != 1 or hashlib.sha256(matches[0]['macro_sql'].encode()).hexdigest() != expected_hash:
            raise ValueError('Portable DATEADD implementation differs from frozen dbt adapter')
    return True
