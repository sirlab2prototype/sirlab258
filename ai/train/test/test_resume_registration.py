"""Standalone resume must register ACT before config decoding; no training/devices."""
import subprocess,sys
from pathlib import Path

def test_resume_registers_act_in_fresh_process():
 root=Path(__file__).resolve().parents[3]
 code='''
from ai.train import run_train
from lerobot.configs.policies import PreTrainedConfig
class ReachedFiles(Exception):pass
def stop(path):raise ReachedFiles()
run_train.check_files=stop
try:run_train.resume_plan(None,None,None)
except ReachedFiles:pass
assert PreTrainedConfig.get_choice_class('act').__name__=='ACTConfig'
'''
 r=subprocess.run([sys.executable,'-B','-c',code],cwd=root,text=True,capture_output=True,timeout=45)
 assert r.returncode==0,r.stderr
