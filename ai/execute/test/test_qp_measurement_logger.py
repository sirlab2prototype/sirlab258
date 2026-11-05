import importlib.util,json
from pathlib import Path

def test_offline_measurement_analysis(tmp_path):
    script=Path(__file__).resolve().parents[3]/'scripts/log_qp_readonly.py'
    spec=importlib.util.spec_from_file_location('qp_log',script);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    rows=[]
    for i in range(6):
        t=(i+1)*50000000
        data={'name':['joint'+str(j) for j in range(6)],'position':[i*.001]*6,'velocity':[.02]*6}
        rows.extend([{'topic':'/capstone/teleop_action','received_ns':t-1000,'data':data},{'topic':'/joint_states','received_ns':t,'data':data}])
    rows.append({'kind':'end','drops':0,'write_error':None})
    path=tmp_path/'raw.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    result=m.analyze(path)
    assert result['valid_complete_log'] and result['states']==6
    assert abs(result['velocity_abs_max_rad_s'][0]-.02)<1e-10
    assert result['stop_latency_s'] is None


def test_short_or_duplicate_timestamp_log_keeps_available_metrics(tmp_path):
    script=Path(__file__).resolve().parents[3]/'scripts/log_qp_readonly.py'
    spec=importlib.util.spec_from_file_location('qp_log_short',script);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    rows=[]
    for stamp in (1_000_000_000,1_000_000_000,1_050_000_000,1_050_000_000):
        rows.append({'topic':'/joint_states','received_ns':stamp,
                     'data':{'name':['joint'+str(j) for j in range(6)],
                             'position':[stamp*1e-12]*6,'velocity':[.02]*6}})
    rows.append({'kind':'end','drops':0,'write_error':None})
    path=tmp_path/'short.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    result=m.analyze(path)
    assert result['valid_complete_log']
    assert len(result['velocity_abs_max_rad_s'])==6
    assert 'acceleration_abs_quantiles_rad_s2' not in result
