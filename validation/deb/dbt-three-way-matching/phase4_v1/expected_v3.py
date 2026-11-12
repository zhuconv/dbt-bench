"""Independent earliest/latest invoice-date alternatives from fixed source rows."""
import expected_v2 as earliest
import expected_max_v1 as latest


def derive(raw):
    first = earliest.derive(raw)
    last = latest.derive(raw)
    first['legal_invoice_date_alternatives'] = {'latest': last}
    first['invoice_date_policy'] = ('Multiple invoice headers are aggregated per PO line; '
        'the instruction does not require MIN rather than MAX invoice date. '
        'Both source-derived alternatives are permitted, with every dependent '
        'timing/risk/status/action field required to follow the same choice.')
    return first
