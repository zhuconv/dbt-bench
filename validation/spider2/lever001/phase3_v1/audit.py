"""Phase3 Lever entrypoint: exact immutable raw/workflow implementation reused."""
from pathlib import Path
import sys
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).parents[1]/'audit_phase2_v1'))
from check import run,main
if __name__=='__main__':main()
