from pathlib import Path
import shutil
import tempfile
import unittest
from seal_difficulty_preflight import observation_only


class DifficultyPreflightTest(unittest.TestCase):
    def test_only_collect_change_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);base=p/'base';base.mkdir();(base/'tests').mkdir()
            (base/'tests/test.sh').write_text('original checker')
            (base/'task.toml').write_text('[agent]\ntimeout_sec=600\n[verifier]\ntimeout_sec=300\n')
            actual=p/'actual';shutil.copytree(base,actual)
            (actual/'task.toml').write_text((base/'task.toml').read_text()+
                '\n[[verifier.collect]]\ncommand="readonly"\ntimeout_sec=600\n')
            self.assertTrue(observation_only(actual,base))
            (actual/'tests/test.sh').write_text('changed checker')
            with self.assertRaises(ValueError):observation_only(actual,base)

    def test_budget_change_is_rejected_even_with_valid_collect(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);base=p/'base';base.mkdir();actual=p/'actual';actual.mkdir()
            (base/'task.toml').write_text('[agent]\ntimeout_sec=600\n[verifier]\ntimeout_sec=300\n')
            (actual/'task.toml').write_text('[agent]\ntimeout_sec=60\n[verifier]\ntimeout_sec=300\n'
                '[[verifier.collect]]\ncommand="readonly"\n')
            with self.assertRaises(ValueError):observation_only(actual,base)
