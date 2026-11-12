"""Shared actual Qwen actions; source delivery does not imply a prior final build."""
import posixpath,re,shlex
from difficulty_deb_events import shell_result
from difficulty_deb_workflow_v3 import declared_action_gate

def explicit_profile_export(command, cwd, expected):
    """Recognize an actual successful explicit export, without requiring a run."""
    if not isinstance(command, str) or any(x in command for x in ('\n', ';', '|', '`', '$(', '>', '<')):
        return False
    try:
        stages = [shlex.split(s.strip()) for s in command.split('&&')]
    except ValueError:
        return False
    location = cwd if isinstance(cwd, str) and cwd.startswith('/') else None
    found = False
    for argv in stages:
        if not argv or any('&' in x for x in argv): return False
        if argv[0] == 'cd' and len(argv) == 2:
            value = argv[1]
            if '$' in value or '~' in value: return False
            location = value if value.startswith('/') else posixpath.normpath(posixpath.join(location, value)) if location else None
            continue
        if len(argv) == 2 and argv[0] == 'export' and argv[1].startswith('DBT_PROFILES_DIR='):
            value = argv[1].split('=', 1)[1]
            if not value or '$' in value or '~' in value: return False
            value = value if value.startswith('/') else posixpath.normpath(posixpath.join(location, value)) if location else None
            found = value == expected
            continue
        # An opaque script or printed command is not evidence of setting env.
        return False
    return found


def source_action_gate(key, events, project):
    gate = declared_action_gate(key, events, project)
    if gate['reward'] in (0, 1): return gate
    calls = []
    for row in events.get('pairs', []):
        args = row.get('args', {})
        response = shell_result(row.get('response', {}))
        if row.get('name') == 'run_shell_command' and response and response[0] == 0 and explicit_profile_export(
                args.get('command'), args.get('directory', args.get('cwd')), project):
            calls.append(row['call_id'])
    if events.get('echo_before_first_write') is True and calls:
        return {'reward': 1, 'status': 'pass', 'reason':
            'Actual paired backend echo precedes source writes and an explicit successful local DBT_PROFILES_DIR export was observed. This source contract does not require a model-issued dbt run.',
            'profile_export_calls': calls, 'source_logs': events.get('source_logs', [])}
    return gate | {'reason': 'Original echo/local-profile environment evidence is incomplete; source delivery does not add a dbt-run obligation.'}


def reference_build_events(events, projects, models):
    """Recognize actual successful named upstream builds in simple shell chains."""
    found=[]
    for row in events.get('pairs',[]):
        if row.get('name')!='run_shell_command':continue
        result=shell_result(row.get('response',{}))
        if not result or result[0]!=0:continue
        args=row.get('args',{});command=args.get('command');cwd=args.get('directory',args.get('cwd'))
        if not isinstance(command,str)or any(x in command for x in ('\n',';','|','`','$(','>','<')):continue
        try:stages=[shlex.split(x.strip())for x in command.split('&&')]
        except ValueError:continue
        calls=[];safe=True
        for argv in stages:
            if not argv or any('&'in x for x in argv):safe=False;break
            if argv[0]=='cd'and len(argv)==2:
                if any(x in argv[1]for x in ('$','~')):safe=False;break
                cwd=argv[1]if argv[1].startswith('/')else posixpath.normpath(posixpath.join(cwd,argv[1]))if cwd else None;continue
            if argv[0]=='export'and len(argv)==2 and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*=.*',argv[1]):continue
            while argv and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*=.*',argv[0]):argv=argv[1:]
            if len(argv)<2 or argv[0].rsplit('/',1)[-1]!='dbt'or argv[1]not in ('run','build'):safe=False;break
            project=argv[argv.index('--project-dir')+1]if '--project-dir'in argv and argv.index('--project-dir')+1<len(argv)else cwd
            selected=[];take=False
            for token in argv[2:]:
                if token in ('--select','-s'):take=True;continue
                if token.startswith('-'):take=False
                elif take:selected.extend(token.split())
            if project in projects and set(models)<={x.strip('+')for x in selected}:calls.append(row['call_id'])
        if safe:found.extend(calls)
    return sorted(set(found))
