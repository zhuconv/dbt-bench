"""Resolve only literal env_var calls against the trusted build's captured env.

This supplementary version never rewrites the captured profile. It resolves only
the selected target: unused Snowflake credentials are neither needed nor read.
"""
import ast
from copy import deepcopy
import yaml


class UnresolvedProfile(ValueError):
    pass


def scalar(value, environment, environment_complete=False):
    if not isinstance(value, str) or not any(x in value for x in ('{{', '{%', '{#')):
        return value
    value = value.strip()
    if not value.startswith('{{') or not value.endswith('}}'):
        raise UnresolvedProfile('Only a whole scalar env_var expression is supported')
    try:
        node = ast.parse(value[2:-2].strip(), mode='eval').body
    except SyntaxError as exc:
        raise UnresolvedProfile('Unsupported template syntax') from exc
    if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name)
            or node.func.id != 'env_var' or node.keywords or len(node.args) not in (1, 2)
            or any(not isinstance(v, ast.Constant) or not isinstance(v.value, str) for v in node.args)):
        raise UnresolvedProfile('Only env_var with literal string arguments is supported')
    name = node.args[0].value
    if name in environment:
        result = environment[name]
        if not isinstance(result, str):
            raise UnresolvedProfile('Captured environment is not string-valued')
        return result
    if len(node.args) == 2 and environment_complete:
        return node.args[1].value
    raise UnresolvedProfile('Required variable was not captured: ' + name)


def has_model_schema(value):
    return isinstance(value, dict) and any(k in ('schema', '+schema') or has_model_schema(v) for k, v in value.items())


def evaluate(facts, kind):
    try:
        if not facts.get('trusted_process_complete'):
            raise UnresolvedProfile('Trusted actual process environment unavailable')
        process = facts['process']; environment = process['environment']; files = facts['files']
        cfg = yaml.safe_load(files['dbt_project.yml'])
        profiles = yaml.safe_load(files['profiles.yml'])
        profile = profiles['dbt_project']
        target_name = scalar(profile['target'], environment)
        target = {k: scalar(v, environment) for k, v in profile['outputs'][target_name].items()}
        expected_schema = 'risk_analytics' if kind == 'risk' else 'daily_analytics'
        command = process.get('command', [])
        explicit = any(v == '--profiles-dir' and i + 1 < len(command) and command[i + 1] == '/app/dbt_project' for i, v in enumerate(command))
        profile_location = environment.get('DBT_PROFILES_DIR') == '/app/dbt_project'
        if kind == 'risk':
            profile_location = profile_location or explicit
        ok = (scalar(cfg.get('profile'), environment) == 'dbt_project'
              and target.get('type') == 'duckdb' and target.get('path') == facts['database']
              and target.get('schema') == expected_schema and profile_location
              and process.get('exit_code') == 0 and process.get('artifacts_bound_to_process') is True)
        if kind == 'daily':
            ok = ok and not has_model_schema(cfg.get('models', {})) and 'schema' not in cfg
        return {'status': 'pass' if ok else 'fail',
                'reason': 'Selected profile target resolved from original YAML and captured actual process environment; declared profile, database, schema and invocation checked',
                'selected_target': target_name, 'resolved_nonsecret_fields': {k: target.get(k) for k in ('type', 'path', 'schema')}}
    except UnresolvedProfile as exc:
        return {'status': 'pending', 'reason': str(exc)}
    except (KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        return {'status': 'fail', 'reason': 'Captured selected profile configuration is invalid: ' + str(exc)}


def extend(legacy, facts, kind):
    result = deepcopy(legacy)
    if 'checks' not in result:
        return result
    profile = evaluate(facts, kind)
    if kind == 'risk':
        workflow = result['checks'].get('workflow')
        if not workflow or 'details' not in workflow:
            return result
        workflow['details']['profile'] = profile
        states = [v['status'] for v in workflow['details'].values()]
        workflow['status'] = 'fail' if 'fail' in states else 'pending' if 'pending' in states else 'pass'
    else:
        result['checks']['profile_configuration'] = profile
    states = [v['status'] for v in result['checks'].values()]
    result['reward'] = 0.0 if 'fail' in states else None if any(v != 'pass' for v in states) else 1.0
    result['status'] = 'unknown' if result['reward'] is None else 'scored'
    result['legacy_checker_outcome'] = legacy
    result['checker_version'] = 'phase4-runtime-profile-v1'
    result['scope'] = 'Supplementary audit version; original audit retained. Only profile interpretation is extended using captured process environment.'
    return result
