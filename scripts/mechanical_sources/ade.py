from copy import deepcopy


def normalize(spec):
    result = deepcopy(spec)
    assert result['source'] == 'ade' and result['database_paths'] and result['outputs']
    # Diagnosis-answer relations and model tasks use their actual declarations.
    # No inference of models from the ADE source name or SQL test keywords.
    return result
