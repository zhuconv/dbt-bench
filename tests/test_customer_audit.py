import copy,importlib.util,json,tempfile,unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('customer_audit',ROOT/'validation/ade/ade-bench-ana-eng003/audit.py')
audit=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(audit)


def customer(key,name):
    return {'id':key,**{c:None for c in audit.ATTRIBUTES},'last_name':name,'zip_postal_code':12345}


class CustomerAuditTests(unittest.TestCase):
    def setUp(self):
        self.source=[customer(1,'A'),customer(1,'A'),customer(2,'B')]
        self.expected=[{'customer_id':1,**{k:v for k,v in self.source[0].items() if k!='id'}},
                       {'customer_id':2,**{k:v for k,v in self.source[2].items() if k!='id'}}]

    def compare(self,rows):
        return audit.compare_output(self.source,{'columns':list(rows[0]) if rows else list(audit.COLUMNS),'rows':rows},{})['equal']

    def test_deduplication_and_conflicting_source(self):
        self.assertEqual(audit.expected_rows(self.source),self.expected)
        with self.assertRaises(ValueError):audit.expected_rows(self.source+[customer(1,'Conflict')])

    def test_required_negative_data_cases(self):
        for rows in [self.expected+[self.expected[0]],self.expected[1:],
                     [{**self.expected[0],'customer_id':None},self.expected[1]],
                     [{**self.expected[0],'customer_id':2},{**self.expected[1],'customer_id':1}],
                     [{**self.expected[0],'last_name':'Wrong'},self.expected[1]],
                     [{**self.expected[0],'customer_id':Decimal('1.1')},self.expected[1]],
                     [{**self.expected[0],'customer_id':True},self.expected[1]]]:
            with self.subTest(rows=rows):self.assertFalse(self.compare(rows))

    def test_legal_order_extra_column_and_integral_numeric_equivalence(self):
        rows=[{**r,'customer_id':Decimal(r['customer_id']),'extra':'allowed'} for r in reversed(self.expected)]
        self.assertTrue(self.compare(rows))

    def test_integer_precision_and_null_are_not_coerced(self):
        source=[customer(9007199254740993,'A')]
        wrong=[{**audit.expected_rows(source)[0],'customer_id':9007199254740992}]
        output={'columns':list(wrong[0]),'rows':wrong}
        self.assertFalse(audit.compare_output(source,output,{})['equal'])
        with self.assertRaises(ValueError):audit.normalize_rows([{'customer_id':'1'}],{'customer_id':'VARCHAR'})
        self.assertEqual(audit.normalize_rows([{'customer_id':'1.000'}],{'customer_id':'DECIMAL(38,3)'})[0]['customer_id'],1)
        wrong=[{**self.expected[0],'notes':''},self.expected[1]]
        self.assertFalse(self.compare(wrong))

    def test_missing_columns_rejected(self):
        bad=copy.deepcopy(self.expected)
        for row in bad:row['id']=row.pop('customer_id')
        self.assertFalse(self.compare(bad))

    def test_snapshot_tampering_is_error_not_business_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'input.json').write_text('{}\n')
            snapshot={'phase':'before','read_only_database':True,'dbt_invoked':False,'errors':[],
                      'project_files':{},'files':{'input.json':{'sha256':audit.file_hash(root/'input.json')}}}
            (root/'snapshot.json').write_text(json.dumps(snapshot))
            audit.verified_snapshot(root,'before')
            (root/'input.json').write_text('tampered')
            with self.assertRaises(ValueError):audit.verified_snapshot(root,'before')

    def make_export(self,directory,phase,*,fresh=True,model=True):
        directory.mkdir()
        sql='select distinct id as customer_id from customer'
        node={'resource_type':'model','name':'dim_customer','original_file_path':'models/alternate/dim_customer.sql',
              'raw_code':sql,'schema':'main','alias':'dim_customer'}
        key='model.example.dim_customer'
        payloads={'input.json':{'columns':list(self.source[0]),'rows':self.source},
                  'manifest.json':{'nodes':{key:node} if model else {}},
                  'run_results.json':{'metadata':{'invocation_id':'new' if fresh and phase=='after-native-build' else 'old'},
                                      'results':[{'unique_id':key,'status':'success'}] if model else []},
                  'project/models/alternate/dim_customer.sql':sql}
        if phase=='after-native-build':payloads['output.json']={'columns':list(audit.COLUMNS),'rows':self.expected}
        snapshot={'phase':phase,'read_only_database':True,'dbt_invoked':False,'errors':[],
                  'setup_exit':0 if phase=='after-native-build' else None,'project_files':{},'files':{}}
        for name,data in payloads.items():
            path=directory/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(data if isinstance(data,str) else json.dumps(data,sort_keys=True))
            snapshot['files'][name]={'sha256':audit.file_hash(path)}
            if name.startswith('project/'):snapshot['project_files'][name[8:]]=audit.file_hash(path)
        if phase=='before':snapshot['files']['output.json']={'absent':True}
        else:snapshot['files']['output.json']['types']={}
        (directory/'snapshot.json').write_text(json.dumps(snapshot))

    def test_source_only_requires_fresh_successful_build(self):
        for fresh in [True,False]:
            with self.subTest(fresh=fresh),tempfile.TemporaryDirectory() as d:
                root=Path(d)
                self.make_export(root/'before','before')
                self.make_export(root/'after-native-build','after-native-build',fresh=fresh)
                with patch.object(audit,'INPUT_SHA',audit.file_hash(root/'before/input.json')):
                    self.assertEqual(audit.score(root)['success'],fresh)

    def test_correct_table_without_model_is_not_dbt_submission(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for phase in ['before','after-native-build']:self.make_export(root/phase,phase,model=False)
            with patch.object(audit,'INPUT_SHA',audit.file_hash(root/'before/input.json')):
                self.assertFalse(audit.score(root)['success'])
