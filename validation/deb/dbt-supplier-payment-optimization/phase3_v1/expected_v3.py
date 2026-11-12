"""Declared fixed-input interpretation; arithmetic remains independent Python."""
from expected_v2 import derive as retained_derive


def derive(data):
    result = retained_derive(data)
    accepted = ('raw_concentration', 'rounded_concentration')
    result['policies'] = {k: result['policies'][k] for k in accepted}
    result['exact_values'] = {k: result['exact_values'][k] for k in accepted}
    result['reviewed_fixed_input_policy'] = True
    result['interpretations']['future_invoice_age'] = (
        'Blind author keeps status-selected invoices and floors negative age '
        'contribution at0, consistent with declared0-100range; current-data scope.')
    return result
