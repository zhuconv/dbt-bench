"""Independent raw-input review of the actual Workday dbt build witness."""
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
from dbt_bench.io import evidence_ref, write_json


def recompute(inputs):
    def active(name):
        return [r for r in inputs[name] if not r.get('_fivetran_deleted', False)]
    def indexed(rows, key):
        result=defaultdict(list)
        for row in rows:
            if row[key] is not None:
                result[row[key]].append(row)
        return result
    families=indexed(active('workday_job_family_data'),'id')
    groups=indexed(active('workday_job_family_group_data'),'id')
    profile_links=indexed(active('workday_job_family_job_profile_data'),'job_profile_id')
    group_links=indexed(active('workday_job_family_job_family_group_data'),'job_family_id')
    output=[]
    matched_profile_links=0
    for profile in active('workday_job_profile_data'):
        arrays=[set(),set(),set(),set()]
        for link in profile_links.get(profile['id'],[]):
            matched_profile_links+=1
            for family in families.get(link['job_family_id'],[]):
                if family['job_family_code'] is not None:arrays[0].add(family['job_family_code'])
                if family['summary'] is not None:arrays[1].add(family['summary'])
                for group_link in group_links.get(family['id'],[]):
                    for group in groups.get(group_link['job_family_group_id'],[]):
                        if group['job_family_group_code'] is not None:arrays[2].add(group['job_family_group_code'])
                        if group['summary'] is not None:arrays[3].add(group['summary'])
        output.append({'job_profile_id':profile['id'],'source_relation':'',
                       'job_profile_code':profile['job_profile_code'],'job_title':profile['title'],
                       'private_title':profile['private_title'],'job_summary':profile['summary'],
                       'job_description':profile['description'],
                       **{name:sorted(values) or None for name,values in zip(
                           ['job_family_codes','job_family_summaries','job_family_group_codes','job_family_group_summaries'],arrays)}})
    return output,matched_profile_links


def main():
    base=ROOT/'artifacts/validation/spider-workday-build-v1'
    result=[]
    names=['workday_job_profile_data','workday_job_family_job_profile_data','workday_job_family_data',
           'workday_job_family_job_family_group_data','workday_job_family_group_data']
    previous=None
    for path in sorted((base/'jobs'/base.name).glob('*/result.json')):
        raw=json.loads(path.read_text());directory=path.parent/'agent'
        assert raw['exception_info'] is None and raw['verifier_result']['rewards']=={'reward':1.0}
        proof=json.loads((directory/'case-proof.json').read_text())
        assert proof['validity']=='built_from_inputs' and proof['gold_access'] is False and proof['all_models_run']==50
        inputs={name:json.loads((directory/(name+'.json')).read_text()) for name in names}
        expected,matched=recompute(inputs)
        actual=json.loads((directory/'workday__job_overview.json').read_text())
        key=lambda row:(row['source_relation'],row['job_profile_id'])
        assert len({key(r) for r in actual})==len(actual)==5
        assert sorted(expected,key=key)==sorted(actual,key=key)
        if previous is not None:assert sorted(actual,key=key)==previous
        previous=sorted(actual,key=key)
        result.append({'native_result':evidence_ref(path,ROOT),'proof':evidence_ref(directory/'case-proof.json',ROOT),
                       'raw_inputs':[evidence_ref(directory/(name+'.json'),ROOT) for name in names],
                       'output':evidence_ref(directory/'workday__job_overview.json',ROOT),
                       'code_and_logs':[evidence_ref(directory/name,ROOT) for name in ['submission.sql','dbt-manifest.json','dbt-run-results.json','oracle.txt']],
                       'all_models_run':50,'independent_rows_agree':5,'columns_checked':11,
                       'matching_profile_family_links':matched})
    assert len(result)==2
    summary={'task_key':'spider2__workday002','witness_kind':'built_from_inputs','gold_access':False,
             'independent_repeats':2,'all_required_output_stable':True,'reference_kind':'combined',
             'semantic_certification':False,'native_conclusion_remains':'weak',
             'scope':'All 11 output columns independently recomputed on the fixed five-profile dataset. All 50 enabled models run in both clean environments.',
             'remaining':['Original profile IDs match zero family links, so positive aggregation semantics and legal array ordering still need an extra fixture/contract review.',
                          'Deleted profile and duplicate relationship branches not exercised on original data.',
                          'No native repair or validated audit checker; earlier key/title permutation leak remains.'],
             'review_code':evidence_ref(Path(__file__),ROOT),'plan':evidence_ref(base/'manifest.json',ROOT),'trials':result}
    write_json(ROOT/'validation/evidence/spider-workday002-review/build-witness.json',summary)
    print(json.dumps({'repeats':2,'all_models_run':50,'output_rows':5,'output_columns':11,'native':'weak','certified':False}))


if __name__=='__main__':main()
