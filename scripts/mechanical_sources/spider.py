from copy import deepcopy


def normalize(spec):
    result = deepcopy(spec)
    assert result['source'] == 'spider2' and result['outputs']
    for output in result['outputs']:
        # Capture the whole declared relation when selected column names are not
        # yet known. Keep native projection separately; it is not a full contract.
        output['native_projection_indices'] = output.get('column_indices')
        output['native_projection_columns'] = output.get('columns')
        output['columns'] = output.get('all_columns_observed') or None
        output['capture_scope'] = 'All columns of the explicitly configured output relation; native projection retained separately.'
    result['reference_scope'] = 'Native copy-gold fixture unless the task review supplies a separate real build witness.'
    return result
