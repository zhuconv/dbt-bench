"""Resolve only literal env_var expressions using actual captured environment."""
import ast
import re


def resolve(value, environment):
    if isinstance(value, dict):
        return {k: resolve(v, environment) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, environment) for v in value]
    if not isinstance(value, str) or not any(x in value for x in ('{{', '{%')):
        return value
    match = re.fullmatch(r'\s*\{\{\s*env_var\((.*)\)\s*\}\}\s*', value, re.S)
    if not match:
        raise ValueError('Uncaptured profile expression')
    node = ast.parse('env_var(' + match[1] + ')', mode='eval').body
    if (not isinstance(node, ast.Call) or node.keywords or len(node.args) not in (1, 2)
            or any(not isinstance(a, ast.Constant) or not isinstance(a.value, str) for a in node.args)):
        raise ValueError('Unsupported env_var arguments')
    name = node.args[0].value
    if name not in environment:
        raise ValueError('Environment variable was not captured')
    if environment[name] is not None:
        return environment[name]
    if len(node.args) == 2:
        return node.args[1].value
    raise ValueError('Required environment variable absent')
