"""Independent raw-rule proof gate before invoking native phase4 verifiers."""
from pathlib import Path
from collections import defaultdict,Counter
from decimal import Decimal,ROUND_HALF_UP
from datetime import date
import argparse,hashlib,json,subprocess,time
import duckdb
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def bag(rows):return sorted(json.dumps(r,default=str,sort_keys=True) for r in rows)
def records(c,relation):
 q=c.execute('SELECT * FROM '+relation);cols=[x[0].lower() for x in q.description];return [dict(zip(cols,r)) for r in q.fetchall()]
def proof(c,kind,observed):
 if kind=='ltv_status':
  raw=records(c,'main.orders');counts=Counter()
  for r in raw:
   if r['status'] not in ('CANCELLED','RETURNED'):counts[r['customer_id']]+=1
  mismatches=[r['customer_id'] for r in observed if r['total_orders']!=counts[r['customer_id']]]
  return {'mismatch_count':len(mismatches),'examples':mismatches[:12],'raw_rows':len(raw),'rule':'Patched status-only exclusion, distinct non-NULL order IDs (raw uniqueness verified below)','eligible_unpaid_rows':sum(r['status'] not in ('CANCELLED','RETURNED') and r['payment_status']=='UNPAID' for r in raw),'raw_order_ids_unique_nonnull':len({r['order_id'] for r in raw})==len(raw) and all(r['order_id'] is not None for r in raw)}
 if kind=='cltv_dates':
  raw=records(c,'main.int_sales__orders_enriched');wanted={r['customer_id'] for r in raw if r['customer_id'] is not None and r['ordered_at'] is not None and r['is_cancelled'] is not True};got={r['customer_id'] for r in observed};difference=wanted^got
  return {'mismatch_count':len(difference),'examples':sorted(difference)[:12],'expected_customers':len(wanted),'actual_customers':len(got),'raw_rows':len(raw),'rule':'Patched NULL timestamp orders contribute no metric and create no customer row'}
 if kind=='repeat_null_id':
  raw=records(c,'main.int_sales__orders_enriched');want=sum((Decimal(r['grand_total']) for r in raw if r['ordered_at'] is not None and r['grand_total'] is not None),Decimal(0));got=sum((Decimal(r['revenue']) for r in observed),Decimal(0))
  return {'mismatch_count':int(want!=got),'expected_revenue':str(want),'actual_revenue':str(got),'dated_null_id_rows':sum(r['order_id'] is None and r['ordered_at'] is not None for r in raw),'rule':'Patched NULL order IDs retain each dated row and every source revenue amount; excluded only from order counts'}
 if kind=='interval_quartile':
  raw=records(c,'main.orders');groups=defaultdict(lambda:Decimal(0))
  for r in raw:
   if r['ordered_at'] is not None and date(2024,1,1)<=r['ordered_at'].date()<=date(2024,12,31) and r['status'] is not None and r['status'].strip() not in ('CANCELLED','RETURNED','FAILED'):groups[r['customer_id'].strip()]+=Decimal(r['grand_total'])
  values=sorted((v.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),k) for k,v in groups.items());n=len(values);q,rem=divmod(n,4);buckets=[b for b in range(4) for _ in range(q+(b<rem))];expected={cid:('Bronze','Silver','Gold','Platinum')[b] for (_,cid),b in zip(values,buckets)};bad=[r['customer_id'] for r in observed if r['customer_tier']!=expected[r['customer_id']]]
  return {'mismatch_count':len(bad),'examples':bad[:12],'raw_rows':len(raw),'expected_tier_counts':dict(Counter(expected.values())),'revenue_ties':n-len({v for v,_ in values}),'rule':'Patched ascending NTILE(4), lower-value groups receive surplus rows; input-only exact revenue grouping'}
 raise ValueError(kind)
def main(spec,out):
 s=json.loads(spec.read_text());p=Path(s['project_root'])/s['model_path'];original=p.read_text();source_sha=digest(p)
 with duckdb.connect(s['database'],read_only=True) as c:before=records(c,s['relation']);before_proof=proof(c,s['rule'],before)
 if before_proof['mismatch_count']!=0:raise ValueError('Actual author output did not satisfy independently checked rule')
 if s['kind']=='equivalent':new=original+'\n-- phase4 equivalent: comment-only SQL source presentation.\n'
 else:
  new=original
  for before_text,after_text in s['replacements']:
   if new.count(before_text)!=1:raise ValueError('Mutation source context differs')
   new=new.replace(before_text,after_text)
 p.write_text(new);start=time.time();cmd=['dbt','run','--select',Path(s['model_path']).stem,'--target-path','phase4_case_target_v1','--log-path','phase4_case_logs_v1'];done=subprocess.run(cmd,cwd=s['project_root'],text=True,capture_output=True);log=out.with_name('case-build.log');log.write_text(done.stdout+done.stderr)
 if done.returncode:raise ValueError('Case SQL build failed; not semantic rejection')
 with duckdb.connect(s['database'],read_only=True) as c:after=records(c,s['relation']);after_proof=proof(c,s['rule'],after)
 valid='legal_equivalent' if s['kind']=='equivalent' and bag(before)==bag(after) else 'business_error' if s['kind']=='negative' and after_proof['mismatch_count']>0 else 'invalid_mutation'
 result={'schema_version':1,'task_key':s['task_key'],'case_id':s['case_id'],'validity':valid,'tested_checker_invoked_to_define_validity':False,'before':before_proof,'after':after_proof,'changed_row_bag':bag(before)!=bag(after),'original_source_sha256':source_sha,'case_source_sha256':digest(p),'actual_command':cmd,'returncode':done.returncode,'elapsed_seconds':time.time()-start,'build_log_sha256':digest(log),'model_requests':0,'scope':'Raw-input Python arithmetic establishes violation before any native/audit checker invocation; source and built relation mutated together.'};out.write_text(json.dumps(result,indent=2,default=str)+'\n')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--spec',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();main(a.spec,a.output)
