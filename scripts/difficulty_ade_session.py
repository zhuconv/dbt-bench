"""Bind original/read-only dbt sessions to a bounded clock-safe source build."""
from pathlib import Path
import re
from dbt_bench import io
from difficulty_ade_clock import join_clocks


def source_session_guard(manifest, sources):
    """Reject unobserved session-changing mechanisms rather than guessing.

    This is a conservative supported syntax boundary, not an author SQL hash
    whitelist. Any ordinary SELECT/CTE implementation can vary freely inside it.
    """
    for node in manifest['nodes'].values():
        if node.get('resource_type')=='operation':
            raise ValueError('Project run hooks require separate dbt-session evidence')
        if node.get('resource_type')!='model':continue
        config=node.get('config',{})
        if config.get('pre-hook')or config.get('post-hook')or config.get('sql_header'):
            raise ValueError('Model hook/header can alter an unobserved SQL session')
        if node.get('language','sql')!='sql' or config.get('materialized')not in ('table','view','ephemeral'):
            raise ValueError('Unobserved model session/materialization implementation')
        sql=sources.get(node.get('original_file_path'))
        if sql is None:raise ValueError('Model source missing from complete delivered inventory')
        if '{%'in sql or re.search(r'\b(?:run_query|statement|adapter|modules|load_result|store_result|set_config|query|query_table)\b',sql,re.I):
            raise ValueError('Executable Jinja/SQL session mechanism requires additional evidence')
        for expression in re.findall(r'\{\{(.*?)\}\}',sql,re.S):
            if not re.fullmatch(r'\s*(?:config|ref|source)\s*\(.*\)\s*',expression,re.S):
                raise ValueError('Unreviewed Jinja expression may alter SQL session')
        compiled=node.get('compiled_code')or re.sub(r'\{\{.*?\}\}','',sql,flags=re.S)
        if re.search(r'\b(?:SET|RESET|PRAGMA|CALL|ATTACH|DETACH|EXPORT|IMPORT|COPY|INSTALL|LOAD)\b',compiled,re.I):
            raise ValueError('SQL session-changing statement is outside supported clock scope')
    project=manifest['metadata']['project_name']
    for macro in manifest['macros'].values():
        if macro.get('package_name')==project:
            raise ValueError('Project macros require their own actual session evidence')
        if macro['name'].startswith('materialization_')and macro['package_name']not in ('dbt','dbt_duckdb'):
            raise ValueError('Custom package materialization can change SQL session')
    return {'supported':True,'model_count':sum(x.get('resource_type')=='model'for x in manifest['nodes'].values()),
            'scope':'Static selected credentials; standard SQL table/view/ephemeral models, no hooks/custom materialization/executable Jinja. Unsupported cases remain unknown.'}


def review(original, before, after, workflow, native, capture_ref, root):
    root=Path(root)
    try:
        for value in (original,before,after):
            if value.get('producer')!='actual_dbt_connection_manager_readonly' or value.get('error'):
                raise ValueError('Actual dbt-session clock observation absent/failed')
            if value['observer_only_override']!={'read_only':True,'session_settings_modified':False}:
                raise ValueError('Unexpected observer session override')
            if not value['connection_open_calls']or any(not x['actual_read_only']for x in value['connection_open_calls']):
                raise ValueError('Observer did not use a read-only dbt session')
        for field in ('adapter_sources','dbt_core_version','dbt_duckdb_version','duckdb_version'):
            if original[field]!=before[field]or original[field]!=after[field]:
                raise ValueError('Original/rebuild dbt runtime differs: '+field)
        for field in ('profile_name','target','selected','resolved_database'):
            if original['session'][field]!=before['session'][field]or original['session'][field]!=after['session'][field]:
                raise ValueError('Original/rebuild selected dbt credentials differ: '+field)
        def read(reference):
            if not io.evidence_ok(reference,root):raise ValueError('Session source evidence changed')
            return root/reference['path']
        manifest=io.read_json(read(workflow['manifest']))
        sources={r['original_file_path']:read(r['file']).read_text()for r in workflow['sources']}
        guard=source_session_guard(manifest,sources)
        joined=join_clocks(original,before,after,native,original_capture_sha256=capture_ref['sha256'],
                           verified_session_timezone=original['timezone'])
        return joined|{'source_session_guard':guard,'runtime_and_selected_credentials_match':True}
    except (KeyError,ValueError,TypeError)as exc:
        return {'status':'unknown','same_sql_date':False,'reason':str(exc)}
