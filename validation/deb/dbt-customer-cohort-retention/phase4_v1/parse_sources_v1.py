"""Parse captured compiled SQL and replay bounded Jinja with inert metadata adapters."""
from pathlib import Path
from types import SimpleNamespace
import argparse,hashlib,json,re
import duckdb
from jinja2.sandbox import SandboxedEnvironment
from jinja2 import StrictUndefined
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def scrub(s):return re.sub(r'\s+','',re.sub(r'--[^\n]*|/\*.*?\*/','',s,flags=re.S)).replace('"','')
class Adapter:
 def __init__(self,metadata):self.metadata=metadata;self.calls=[]
 def get_relation(self,database,schema,identifier):
  self.calls.append({'method':'get_relation','schema':schema,'identifier':identifier});return f'"{database}"."{schema}"."{identifier}"' if identifier in self.metadata else None
 def get_columns_in_relation(self,relation):
  name=relation.split('.')[-1].strip('"');self.calls.append({'method':'get_columns_in_relation','identifier':name});return [SimpleNamespace(name=c) for c in self.metadata[name]]
def fail(message):raise ValueError(message)
def main(source,dest):
 request=json.loads(source.read_text());out={'input_sha256':sha(source),'duckdb_version':duckdb.__version__,'model_requests':0,'scope':'Only json_serialize_sql in an empty memory database. Jinja Sandbox replay uses inert captured metadata methods, never connects to data or changes files; exact compiled-text equivalence binds source-control-flow evidence to actual dbt compilation.','tasks':{}}
 with duckdb.connect(':memory:') as db:
  for task,entry in request['tasks'].items():
   models={}
   for name,node in entry['models'].items():
    compiled=node['compiled_code'];value={'compiled_sha256':hashlib.sha256(compiled.encode()).hexdigest(),'ast':json.loads(db.execute('SELECT json_serialize_sql(?)',[compiled]).fetchone()[0])}
    if name in ('rpt_email_attribution_fixed','rpt_multi_touch_attribution_fixed'):
     adapter=Adapter(entry['metadata']);env=SandboxedEnvironment(undefined=StrictUndefined,extensions=['jinja2.ext.do']);context={'adapter':adapter,'execute':True,'target':SimpleNamespace(type='duckdb',database='retail'),'var':lambda name,default=None:default,'env_var':lambda name,default=None:entry['environment'].get(name,default),'config':lambda **kw:'','log':lambda *args,**kw:'','exceptions':SimpleNamespace(raise_compiler_error=fail)}
     try:
      rendered=env.from_string(node['raw_code']).render(**context);value['template']={'status':'pass' if scrub(rendered)==scrub(compiled) else 'gap','actual_compiled_text_matches_replay':scrub(rendered)==scrub(compiled),'adapter_calls':adapter.calls,'rendered_sha256':hashlib.sha256(rendered.encode()).hexdigest()}
     except Exception as e:value['template']={'status':'gap','error_type':type(e).__name__,'reason':str(e),'adapter_calls':adapter.calls}
    models[name]=value
   out['tasks'][task]={'models':models,'trial':entry['trial'],'source_refs':entry['source_refs']}
 if dest.exists():raise ValueError('Immutable parser result exists')
 dest.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'tasks':len(out['tasks']),'output':str(dest),'sha256':sha(dest)}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();main(a.input,a.output)
