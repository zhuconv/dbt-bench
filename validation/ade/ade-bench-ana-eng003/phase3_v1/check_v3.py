from pathlib import Path
import argparse,hashlib,importlib.util,json
HERE=Path(__file__).parent
def run(request,root):
 p=json.loads((HERE/'policy-v3.json').read_text());r=p['engine'];f=Path(root)/r['path'];assert hashlib.sha256(f.read_bytes()).hexdigest()==r['sha256'];s=importlib.util.spec_from_file_location('fulfillment_audit',f);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m.run(request,root,HERE)
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:r=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:r={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 if a.output.exists():raise ValueError('Refuse to overwrite result')
 a.output.write_text(json.dumps(r,indent=2)+'\n')
if __name__=='__main__':main()
