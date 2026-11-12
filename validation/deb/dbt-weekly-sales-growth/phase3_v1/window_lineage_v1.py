"""Limited scoped CTE projection lineage; unsupported SQL stays unknown."""


class Unrecognized(ValueError):
    pass


def output_windows(parsed):
    statements = parsed.get('statements', [])
    if parsed.get('error') or len(statements) != 1:
        raise Unrecognized('Expected one parsed SELECT')

    def query(node, inherited, active):
        if node.get('type') != 'SELECT_NODE':
            raise Unrecognized('Only SELECT projections are supported')
        ctes = dict(inherited)
        for item in node.get('cte_map', {}).get('map', []):
            if item['value'].get('aliases'):
                raise Unrecognized('CTE column renaming needs explicit mapping')
            ctes[item['key'].lower()] = item['value']['query']['node']
        source = node.get('from_table', {})
        source_names = {}
        if source.get('type') == 'BASE_TABLE':
            name = source['table_name'].lower()
            if not source.get('schema_name') and not source.get('catalog_name') and name in ctes:
                if name in active:
                    raise Unrecognized('Recursive CTE')
                source_names = query(ctes[name], ctes, active | {name})
            if source.get('column_name_alias'):
                raise Unrecognized('FROM column aliases not resolved')
        elif source.get('type') != 'EMPTY':
            raise Unrecognized('Joins/subqueries outside limited resolver')
        allowed_qualifiers = {source.get('alias', '').lower(), source.get('table_name', '').lower()}

        def expression(value):
            if isinstance(value, list):
                return set().union(*(expression(x) for x in value)) if value else set()
            if not isinstance(value, dict):
                return set()
            if value.get('class') == 'COLUMN_REF':
                names = value.get('column_names', [])
                if not names or len(names) > 2 or (len(names) == 2 and names[0].lower() not in allowed_qualifiers):
                    raise Unrecognized('Column qualifier does not identify the single FROM source')
                return set(source_names.get(names[-1].lower(), ()))
            if value.get('class') in {'SUBQUERY', 'STAR'}:
                raise Unrecognized('Unsupported nested expression source')
            found = set()
            if value.get('class') == 'WINDOW':
                found.add(value.get('function_name', '').lower())
            for child in value.values():
                found |= expression(child)
            return found

        output = {}
        for expr in node.get('select_list', []):
            if expr.get('class') == 'STAR':
                if any(expr.get(field) for field in ('exclude_list', 'qualified_exclude_list', 'replace_list', 'rename_list', 'columns', 'expr', 'unpacked')):
                    raise Unrecognized('Nontrivial star expansion')
                if expr.get('relation_name') and expr['relation_name'].lower() not in allowed_qualifiers:
                    raise Unrecognized('Star qualifier mismatch')
                for key, value in source_names.items():
                    if key in output:
                        raise Unrecognized('Duplicate projected name')
                    output[key] = value
                continue
            name = expr.get('alias') or (expr.get('column_names', [''])[-1] if expr.get('class') == 'COLUMN_REF' else '')
            if not name:
                raise Unrecognized('Unnamed expression output')
            name = name.lower()
            if name in output:
                raise Unrecognized('Duplicate projected name')
            output[name] = sorted(expression(expr))
        return output

    return query(statements[0]['node'], {}, set())
