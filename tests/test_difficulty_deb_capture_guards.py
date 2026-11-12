"""No original DB/WAL capture may silently fall back to an image database."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
try:
 import yaml
 from difficulty_deb_score_v8 import observe_submission
except ImportError:yaml=None

@unittest.skipIf(yaml is None,'Requires PyYAML from the locked Harbor runtime')
class OriginalDatabaseGuards(unittest.TestCase):
 def call(self,entries):
  with tempfile.TemporaryDirectory()as temp:
   context={'fresh':{},'binding':{'task_key':'deb__fixture'},'original_capture':{'databases':entries},'request':{'observation':{'database_paths':['/app/database/retail.duckdb']}}}
   with patch('difficulty_deb_score_v8.subprocess.run')as run:
    with self.assertRaises(ValueError):observe_submission(context,Path(temp)/'new')
    run.assert_not_called()
 def test_uncaptured_database_never_runs_probe(self):self.call([])
 def test_absent_database_never_runs_probe(self):self.call([{'path':'/app/database/retail.duckdb','exists':False}])
 def test_missing_wal_inventory_never_runs_probe(self):self.call([{'path':'/app/database/retail.duckdb','exists':True,'sha256':'db'}])
 def test_unknown_wal_state_never_runs_probe(self):self.call([{'path':'/app/database/retail.duckdb','exists':True,'sha256':'db'},{'path':'/app/database/retail.duckdb.wal','exists':None}])

if __name__=='__main__':unittest.main()
