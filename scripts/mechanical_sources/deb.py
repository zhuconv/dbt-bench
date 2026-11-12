from copy import deepcopy


def normalize(spec):
    result = deepcopy(spec)
    result['source'] = 'deb'
    result['database_paths'] = result.get('database_paths') or [result['database_path']]
    required = result.get('required_outputs')
    if required and all(isinstance(o, dict) for o in required):
        result['outputs'] = deepcopy(required)
    # Do not silently turn reference-only helper relations into requirements.
    result['outputs'] = [o for o in result['outputs'] if o.get('role') != 'auxiliary_observed']
    seen = set()
    unique = []
    for output in result['outputs']:
        output.setdefault('database', result['database_paths'][0])
        output.setdefault('keys', output.get('key', []))
        output.setdefault('ordered', False)
        output.setdefault('tolerances', {})
        if output.get('columns') == ['*'] or output.get('columns') == '*':
            output['columns'] = None
        output.setdefault('columns', None)
        token = (output['database'], output['relation'])
        if token not in seen:
            seen.add(token)
            unique.append(output)
    result['outputs'] = unique
    result.setdefault('files', [o['model_file'] for o in result['outputs'] if o.get('model_file')])
    result.setdefault('reference_kind', 'unknown')
    result.setdefault('reference_command', 'bash /solution/solve.sh')
    result.setdefault('hidden_sources', [])
    return result
