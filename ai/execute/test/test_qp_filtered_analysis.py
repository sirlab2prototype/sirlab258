import importlib.util,json,sys
from pathlib import Path
import pytest

def test_filtered_measurement_handles_gaps_and_fresh_commands(tmp_path,monkeypatch):
 root=Path(__file__).resolve().parents[3];monkeypatch.syspath_prepend(str(root/'scripts'))
 spec=importlib.util.spec_from_file_location('filtered_qp',root/'scripts/analyze_qp_measurement.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 rows=[{'topic':'/capstone/action_context','received_ns':1000000000,'data':{'data':json.dumps({'phase':'QP_TELEOP'})}}]
 for t in [2,2.05,2.1,2.15,2.65,2.7,2.75,2.8]:
  for topic,delta in [('/capstone/teleop_action',-.001),('/joint_states',0)]:
   ts=t+delta;ns=int(ts*1e9)
   rows.append({'topic':topic,'received_ns':ns,'data':{'name':['joint'+str(j) for j in range(6)],'position':[ts*.02]*6,'velocity':[.02]*6,'header':{'stamp':{'sec':ns//10**9,'nanosec':ns%10**9}}}})
 rows.append({'kind':'end','drops':0,'write_error':None});p=tmp_path/'raw.jsonl';p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
 r=m.details(p)
 assert r['sampling']['long_over75ms']==1
 assert r['tracking']['moving_0.005_age_0.05']['n']<8
 assert max(r['acceleration']['position_second_difference_clean_moving_QP']['quantiles']['100'])<1e-6
 assert r['base']['valid_complete_log']


def test_filtered_analysis_rejects_incomplete_input(tmp_path,monkeypatch):
 root=Path(__file__).resolve().parents[3];monkeypatch.syspath_prepend(str(root/'scripts'))
 spec=importlib.util.spec_from_file_location('filtered_qp_short',root/'scripts/analyze_qp_measurement.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 path=tmp_path/'empty.jsonl';path.write_text('')
 with pytest.raises(ValueError,match='at least three joint states'):
  m.details(path)
 rows=[]
 for i in range(3):
  rows.append({'topic':'/joint_states','received_ns':(i+1)*50_000_000,
               'data':{'name':['joint'+str(j) for j in range(6)],
                       'position':[0.]*6,'velocity':[0.]*6}})
  if i<2:rows.append({**rows[-1],'topic':'/capstone/teleop_action'})
 path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
 with pytest.raises(ValueError,match='complete log'):
  m.details(path)
