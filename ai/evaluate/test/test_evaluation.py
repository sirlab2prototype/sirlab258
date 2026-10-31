"""Metadata-only tests. Real-shaped fixtures are fabricated, not robot trials."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from ai.evaluate.analysis import analyze,bin_value,distribution,load_conditions,offline_evaluation,rate
from ai.evaluate.main import DEFAULT_CONDITIONS,main
from ai.evaluate.records import (ROOT,STAGES,append_evaluation,identity,judgment_template,read_json,validate_judgment)
from ai.evaluate.sources import load_sources
from ai.evaluate.report import write_report


@pytest.fixture
def workspace():
    (ROOT/'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='act-evaluation-test-',dir=ROOT/'tmp') as directory:
        yield Path(directory)


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False))
    return path


def task(number=1,checkpoint='ckpt-a',environment='mock',state='COMPLETED',reason=None):
    return {'task_id':'indy7_basket_to_table1','task_run_id':f'task-{number}','session_id':f'session-{number}',
            'checkpoint_id':checkpoint,'checkpoint':'/historical/moved/checkpoint','state':state,'success':state=='COMPLETED',
            'started_monotonic_ns':1_000_000_000,'ended_monotonic_ns':2_000_000_000,'act_elapsed_seconds':.5,
            'act_action_count':4,'act_stop_confirmed':True,'reason':reason,'failure_stage':None,
            'act_policy_success_user_reported':state=='COMPLETED','physical_task_success_verified':False,
            'execution':{'mock':environment=='mock','session_id':f'session-{number}','checkpoint_id':checkpoint,
                         'state':'STOPPED' if state!='FAULT' else 'FAULT','safety_fault':state=='FAULT','emergency_requested':False},
            'home':{'state':'COMPLETED' if state=='COMPLETED' else 'IDLE','reason':None,
                    'started_monotonic_ns':1_600_000_000 if state=='COMPLETED' else None,
                    'ended_monotonic_ns':1_900_000_000 if state=='COMPLETED' else None}}


def put_task(workspace,value):return save(workspace/'runs'/value['task_run_id']/'task_result.json',value)


def logs(workspace,value,extra=(),corrupt=False):
    base={'session_id':value['session_id'],'checkpoint_id':value['checkpoint_id']}
    rows=[{**base,'event':'session_start'}]
    for i in range(4):
        rows.append({**base,'event':'prediction_response','accepted':True,'response':{'checkpoint_id':value['checkpoint_id'],'inference_ns':(i+1)*1_000_000},
                     'transport':{'client_sent_monotonic_ns':i*100_000_000,'client_received_monotonic_ns':i*100_000_000+10_000_000}})
        rows.append({**base,'event':'action','actual_output_monotonic_ns':1_000_000_000+i*50_000_000,
                     'schedule':{'lateness_ns':1_000_000,'missed_deadlines_total':0}})
    rows.extend({**base,**row} for row in extra)
    rows.append({**base,'event':'session_end'})
    path=workspace/'runs'/value['task_run_id']/'events.jsonl'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows)+('broken\n' if corrupt else ''))
    return path


def judgment(value,outcomes=None,**changes):
    result=judgment_template(value)
    result.update(evaluator='synthetic-fixture-operator',validity='valid')
    result['conditions'].update(region='L',yaw_deg=5,depth_cm=7,x_cm=2,y_cm=7,depth_condition='Back',training_coverage='unseen',
                                evaluation_protocol_id='protocol-a',workspace_id='workspace-a',camera_setup_id='cameras-a',object_id='basket-a')
    result['outcomes'].update(outcomes or {})
    result.update(changes)
    return result


def assess(workspace,value,outcomes=None,**changes):
    return append_evaluation(workspace/'assessments',value,judgment(value,outcomes,**changes))


def report(workspace,**kwargs):
    return analyze(load_sources([workspace/'runs'],[workspace/'assessments'] if (workspace/'assessments').exists() else []),
                   load_conditions(DEFAULT_CONDITIONS),**kwargs)


def manifest(checkpoint='ckpt-a',run_id='training-a'):
    summary={'joint_mae_rad':.2,'hold_joint_mae_rad':.1,'gripper_mae':.1,'gripper_accuracy_at_0_5':.9,
             'hold_gripper_accuracy':.8,'valid_chunk_positions':10,'beats_joint_hold_baseline':False,
             'joint_mae_per_dimension_rad':[.2]*6,'hold_joint_mae_per_dimension_rad':[.1]*6}
    return {'run_id':run_id,'status':'complete','dataset_fingerprint':{'sha256':'dataset-a'},'dataset_repo_id':'local/indy7_act',
            'settings':{'steps':2,'seed':0,'chunk_size':40,'n_action_steps':10},'completed_step':2,
            'split':{'train':[0],'validation':[1],'seed':0},'checkpoint_validation':{'valid':True,'checkpoint_id':checkpoint},
            'offline_validation':{'status':'evaluated','episodes':[1],'summary':summary,'per_episode':{'1':dict(summary)}}}


def test_completed_without_assessment_stays_unknown(workspace):
    value=task();put_task(workspace,value);logs(workspace,value)
    result=report(workspace)
    assert result['unique_attempts']==1 and result['cases'][0]['software_task_success'] is True
    assert set(result['cases'][0]['operator_physical_outcomes'].values())=={'unknown'}
    assert result['by_environment']['mock']['physical_outcomes']['task']['denominator']==0
    assert result['by_environment']['real']['attempts']==0
    assert result['recommendations'][0]['kind']=='evaluation'


def test_operator_act_success_home_failure_task_failure(workspace):
    value=task(state='FAULT',reason='home_failed: home_arrival_error_timeout');value['home']['state']='FAILED'
    put_task(workspace,value)
    assess(workspace,value,{'act':'success','home':'failure','task':'failure'})
    result=report(workspace)['by_environment']['mock']['physical_outcomes']
    assert result['act']['numerator']==1 and result['task']['numerator']==0 and result['home']['denominator']==1


def test_mock_real_separation_and_denominators(workspace):
    for i,env,state,outcome in [(1,'real','COMPLETED','success'),(2,'real','ABORTED','failure'),(3,'mock','COMPLETED','success'),(4,'real','COMPLETED','unknown')]:
        value=task(i,environment=env,state=state);put_task(workspace,value)
        assess(workspace,value,{'task':outcome})
    invalid=task(5,environment='real');put_task(workspace,invalid)
    assess(workspace,invalid,{'task':'success'},validity='invalid',invalid_reason='Test fixture invalid camera placement')
    unevaluated=task(6,environment='real');put_task(workspace,unevaluated)
    result=report(workspace)
    real=result['by_environment']['real'];metric=real['physical_outcomes']['task']
    assert real['attempts']==5 and metric['numerator']==1 and metric['denominator']==2
    assert metric['excluded']=={'unknown':1,'invalid':1,'unassessed':1}
    assert result['by_environment']['mock']['physical_outcomes']['task']['denominator']==1
    assert result['cases'][0]['physical_task_success_inferred'] is False


def test_no_assessment_validity_or_not_attempted_excluded(workspace):
    value=task();put_task(workspace,value)
    assess(workspace,value,{'task':'success'},validity='unknown')
    assert report(workspace)['by_environment']['mock']['physical_outcomes']['task']['excluded']=={'validity_unknown':1}
    value=task(2);put_task(workspace,value);assess(workspace,value,{'grasp':'not_attempted'})
    assert report(workspace)['by_environment']['mock']['physical_outcomes']['grasp']['excluded']['not_attempted']==1


def test_condition_statistics_filters_and_unseen(workspace):
    value=task();put_task(workspace,value);assess(workspace,value,{'grasp':'failure','task':'failure'})
    value=task(2);put_task(workspace,value)
    second=judgment(value,{'task':'success'});second['conditions'].update(region='R',yaw_deg=-35,depth_condition='Front',training_coverage='seen')
    append_evaluation(workspace/'assessments',value,second)
    result=report(workspace,condition_filters={'region':'L'})
    assert result['unique_attempts']==1
    case=result['cases'][0]['conditions']
    assert case['yaw_bin']=='A4' and case['depth_bin']=='unbinned' and case['training_coverage']=='unseen'
    cells=result['by_environment']['mock']['conditions']
    assert next(c for c in cells if c['axis']=='region')['physical_outcomes']['task']['denominator']==1
    assert any(r['kind']=='data_candidate' and 'task-1' in r['evidence']['failure_task_run_ids'] for r in result['recommendations'])
    assert any(r['kind']=='coverage_candidate' for r in result['recommendations'])
    assert report(workspace,condition_filters={'yaw_deg':'5.0'})['unique_attempts']==1


@pytest.mark.parametrize('yaw,expected',[(-40,'A1'),(-27,'A2'),(0,'A4'),(13,'A5'),(40,'A6'),(41,'outside_configured_bins'),(None,'unknown')])
def test_yaw_boundaries(yaw,expected):
    assert bin_value(yaw,load_conditions(DEFAULT_CONDITIONS)['yaw_bins'])==expected


def test_custom_bins_and_no_fixed_depth_cells(workspace):
    config=load_conditions(DEFAULT_CONDITIONS)
    assert config['depth_bins_cm']==[] and config['collection_goal_success_demonstrations']==108
    config['yaw_bins']=[{'id':'negative','min':-90,'max':0},{'id':'positive','min':0,'max':90}]
    config['depth_bins_cm']=[{'id':'front','min':0,'max':5},{'id':'back','min':5,'max':10}]
    path=save(workspace/'conditions.json',config)
    loaded=load_conditions(path)
    assert bin_value(10,loaded['depth_bins_cm'])=='back'
    loaded['yaw_bins'][1]['min']=-1
    save(path,loaded)
    with pytest.raises(ValueError):load_conditions(path)


def test_event_metrics_percentiles_scheduler_and_signal_evidence(workspace):
    value=task(state='FAULT',reason='act_action_failed: target_joint_limit');put_task(workspace,value)
    logs(workspace,value,[{'event':'execution_action_rejected','reason':'target_joint_limit'}])
    result=report(workspace);reliability=result['by_environment']['mock']['execution_reliability']
    assert reliability['metrics']['inference_ms']['median']==2.5
    assert reliability['metrics']['inference_ms']['p99']==pytest.approx(3.97)
    assert reliability['metrics']['action_interval_ms']['median']==50
    assert result['cases'][0]['execution_metrics']['actual_action_fps']==20
    assert reliability['logged_safety_stop_rate']['numerator']==1
    assert 'grasp_failure' not in result['cases'][0]['signals']
    assert any(r['kind']=='system' for r in result['recommendations'])


@pytest.mark.parametrize('reason,code',[('client_inference_timeout','inference_timeout'),('gripper_service_failed_state_unknown','gripper_service'),
    ('stale_joint','joint_state'),('stale_or_invalid_observation','observation_quality'),('action_timestamp_reversal','action_timing'),
    ('scheduler_watchdog_timeout','watchdog'),('stop_unconfirmed_lease_quarantined','stop_failure'),('user_emergency_stop','emergency_stop'),
    ('user_abort','user_abort'),('act_timeout','act_timeout'),('persistent_tracking_error','tracking_error')])
def test_failure_signal_mapping_does_not_invent_manipulation_causes(workspace,reason,code):
    value=task(state='ABORTED' if reason in ('user_abort','act_timeout') else 'FAULT',reason=reason);put_task(workspace,value)
    result=report(workspace)
    assert code in result['cases'][0]['signals']
    assert result['cases'][0]['operator_physical_outcomes']['task']=='unknown'


def test_fault_abort_timeout_and_safety_latch_not_guard(workspace):
    for i,state,reason in [(1,'FAULT','communication link lost'),(2,'ABORTED','user_abort'),(3,'ABORTED','act_timeout')]:
        value=task(i,state=state,reason=reason);put_task(workspace,value);logs(workspace,value)
    result=report(workspace)['by_environment']['mock']['execution_reliability']
    assert result['final_task_states']=={'FAULT':1,'ABORTED':2}
    assert result['timeout_task_run_ids']==['task-3']
    assert result['logged_safety_stop_rate']['numerator']==0 and result['logged_safety_stop_rate']['denominator']==3


def test_duplicates_are_one_attempt_and_one_event_stream(workspace):
    value=task();path=put_task(workspace,value);event=logs(workspace,value)
    save(workspace/'runs/copied_task_result.json',value)
    save(workspace/'runs/wrapper/result.json',{'task':value,'client':{'task':value}})
    shutil.copyfile(event,workspace/'runs/copied_events.jsonl')
    result=report(workspace)
    assert result['unique_attempts']==1
    assert result['by_environment']['mock']['execution_reliability']['metrics']['inference_ms']['observed_samples']==4
    assert any(issue['kind']=='duplicate_event_log' for issue in result['issues'])


def test_conflicting_duplicate_task_is_withheld(workspace):
    value=task();put_task(workspace,value)
    different=copy.deepcopy(value);different['state']='FAULT'
    save(workspace/'runs/conflict_task_result.json',different)
    result=report(workspace)
    assert result['unique_attempts']==0 and any(i['kind']=='conflicting_task' for i in result['issues'])


def test_corrupt_json_jsonl_does_not_silently_count_as_negative(workspace):
    value=task();put_task(workspace,value);logs(workspace,value,corrupt=True)
    (workspace/'runs/bad_task_result.json').write_text('{broken')
    result=report(workspace)
    assert result['unique_attempts']==1 and len(result['issues'])>=2
    assert result['by_environment']['mock']['execution_reliability']['logged_safety_stop_rate']['denominator']==0
    assert result['cases'][0]['execution_metrics']['complete_client_log'] is False


def test_missing_logs_and_unknown_cause(workspace):
    value=task(state='FAULT',reason='unclassified controller termination');put_task(workspace,value)
    result=report(workspace)
    assert result['cases'][0]['execution_metrics'] is None
    assert 'unknown_cause' in result['cases'][0]['signals']
    assert result['by_environment']['mock']['execution_reliability']['logged_safety_stop_rate']['denominator']==0


def test_immutable_revision_history_and_ambiguous_branch(workspace):
    value=task();put_task(workspace,value)
    original=assess(workspace,value,{'task':'failure'});before=original.read_bytes();old=read_json(original)
    changed=judgment(value,{'task':'success'},change_reason='Review of saved video corrected initial rating')
    revision=append_evaluation(workspace/'assessments',value,changed,supersedes=old['evaluation_id'])
    assert original.read_bytes()==before and read_json(revision)['revision']==2
    assert report(workspace)['by_environment']['mock']['physical_outcomes']['task']['numerator']==1
    branch=read_json(revision);branch['evaluation_id']='branch';branch['outcomes']['task']='failure'
    save(workspace/'assessments/evaluation-branch.json',branch)
    result=report(workspace)
    assert result['by_environment']['mock']['physical_outcomes']['task']['denominator']==0
    assert any(i['kind']=='evaluation_history' for i in result['issues'])


def test_revisions_need_current_parent_reason_and_original_task(workspace):
    value=task();put_task(workspace,value);original=assess(workspace,value);old=read_json(original)
    with pytest.raises(ValueError):assess(workspace,value)
    with pytest.raises(ValueError):append_evaluation(workspace/'assessments',value,judgment(value),supersedes=old['evaluation_id'])
    modified=copy.deepcopy(value);modified['reason']='changed original'
    with pytest.raises(ValueError):append_evaluation(workspace/'assessments',modified,judgment(modified,change_reason='revision'),supersedes=old['evaluation_id'])


def test_mock_evaluation_cannot_be_relabelled_real(workspace):
    value=task();put_task(workspace,value)
    with pytest.raises(ValueError,match='relabel'):assess(workspace,value,environment='real')


def test_suspected_causes_stay_suspected_and_recovery_is_separate(workspace):
    value=task();put_task(workspace,value)
    assess(workspace,value,{'grasp':'failure','task':'failure'},causes=[{'code':'grasp_failure','certainty':'suspected','evidence':'Operator suspects alignment; camera view is incomplete'}])
    result=report(workspace)
    cause=result['cases'][0]['signals']['grasp_failure'][0]
    assert cause['certainty']=='suspected' and cause['origin']=='operator'
    assert any(r['kind']=='recovery_experiment' for r in result['recommendations'])
    assert all(r['causal_claim'] is False for r in result['recommendations'])


@pytest.mark.parametrize('damaged',['{','{}'])
def test_corrupt_evaluation_record_withholds_judgments(workspace,damaged):
    value=task();put_task(workspace,value);assess(workspace,value,{'task':'success'})
    (workspace/'assessments/evaluation-broken.json').write_text(damaged)
    result=report(workspace)
    assert result['by_environment']['mock']['physical_outcomes']['task']['denominator']==0
    assert any(i['kind']=='evaluation_store_incomplete' for i in result['issues'])


def test_wilson_interval_and_empty_denominator():
    assert rate(0,0)['rate'] is None
    interval=rate(1,1)['wilson_95'];assert interval[0]==pytest.approx(.2065493144) and interval[1]==pytest.approx(1)
    assert distribution([1,2,3,100])['p99']==pytest.approx(97.09)


def test_episode_link_offline_split_and_checkpoint_comparison(workspace):
    for i,checkpoint in [(1,'ckpt-a'),(2,'ckpt-b')]:
        value=task(i,checkpoint=checkpoint);put_task(workspace,value);logs(workspace,value)
        assess(workspace,value,{'task':'success'},training_run_id=f'train-{i}',dataset_id='dataset-a',related_episode_ids=[1])
        save(workspace/'runs'/f'train-{i}'/'run.json',manifest(checkpoint,f'train-{i}'))
        save(workspace/'runs'/f'train-{i}'/'dataset_validation.json',{'success_episodes':2})
    result=report(workspace)
    assert len(result['checkpoint_comparison']['checkpoints'])==2
    assert result['offline_training_runs']['train-1']['evaluation']['status']=='evaluated'
    assert result['cases'][0]['training_link']=='checkpoint_content_id'
    assert result['checkpoint_comparison']['checkpoints'][0]['successful_demonstrations']==2
    assert result['checkpoint_comparison']['ranking_performed'] is False
    assert any(r['kind']=='training_candidate' for r in result['recommendations'])
    assert report(workspace,checkpoint_ids=['ckpt-a'])['unique_attempts']==1


@pytest.mark.parametrize('change',['overlap','wrong_episode','wrong_per_episode','negative_mae','invalid_shape','pilot'])
def test_offline_metrics_not_mixed_with_training(change):
    value=manifest()
    if change=='overlap':value['split']['train']=[0,1]
    elif change=='wrong_episode':value['offline_validation']['episodes']=[0]
    elif change=='wrong_per_episode':value['offline_validation']['per_episode']={'0':value['offline_validation']['summary']}
    elif change=='negative_mae':value['offline_validation']['summary']['joint_mae_rad']=-1
    elif change=='invalid_shape':value['offline_validation']['summary']['joint_mae_per_dimension_rad']=[1]*10
    else:value['offline_validation']={'status':'not_available','reason':'Pilot run has no held-out episodes'};value['split']['validation']=[]
    result=offline_evaluation(value)
    assert result['status']==('not_available' if change=='pilot' else 'blocked')
    assert result['generalization_validated'] is False


def test_unmatched_checkpoint_conditions_never_rank(workspace):
    for i,checkpoint in [(1,'ckpt-a'),(2,'ckpt-b')]:
        value=task(i,checkpoint=checkpoint,environment='real');put_task(workspace,value)
        evaluation=judgment(value,{'task':'success' if i==1 else 'failure'})
        evaluation['conditions']['yaw_deg']=i*10
        append_evaluation(workspace/'assessments',value,evaluation)
    result=report(workspace)
    assert any('distributions differ' in limit for limit in result['checkpoint_comparison']['limitations'])
    assert result['checkpoint_comparison']['ranking_performed'] is False


def test_moved_records_link_by_ids_and_hash_not_absolute_path(workspace):
    value=task();put_task(workspace,value);assess(workspace,value,{'task':'success'})
    shutil.copytree(workspace/'runs',workspace/'relocated')
    shutil.rmtree(workspace/'runs')
    result=analyze(load_sources([workspace/'relocated'],[workspace/'assessments']),load_conditions(DEFAULT_CONDITIONS))
    assert result['by_environment']['mock']['physical_outcomes']['task']['numerator']==1
    assert all('relocated' in source for source in result['cases'][0]['sources'])


def test_report_cli_template_record_and_input_preservation(workspace):
    value=task();source=put_task(workspace,value);logs(workspace,value)
    before=source.read_bytes()
    template=workspace/'judgment.json'
    assert main(['template','--task-result',str(source),'--output',str(template)])==0
    edited=read_json(template);edited.update(evaluator='operator',validity='valid');edited['outcomes']['task']='success';save(template,edited)
    assert main(['record','--task-result',str(source),'--judgment',str(template),'--store',str(workspace/'assessments')])==0
    assert main(['--runs',str(workspace/'runs'),'--records',str(workspace/'assessments'),'--report-dir',str(workspace/'report')])==0
    assert source.read_bytes()==before
    generated=read_json(workspace/'report/report.json')
    assert generated['by_environment']['mock']['physical_outcomes']['task']['numerator']==1
    assert (workspace/'report/cases.csv').exists() and 'Wilson 95%' in (workspace/'report/report.md').read_text()
    with pytest.raises(ValueError):write_report(generated,workspace/'report')
    with pytest.raises(ValueError):write_report(generated,source.parent/'report')


def test_existing_actual_synthetic_mock_e2e_to_report(workspace):
    artifacts=ROOT/'outputs/act_task_verification_c3740dc278'
    assert artifacts.is_dir(),'Expected retained prior Synthetic ACT/Mock Task verification artifacts'
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts.glob('*.json*')}
    result=analyze(load_sources([artifacts]),load_conditions(DEFAULT_CONDITIONS))
    assert result['unique_attempts']==2
    assert all(case['state']=='COMPLETED' and case['environment']=='mock' for case in result['cases'])
    assert result['by_environment']['real']['physical_outcomes']['task']['denominator']==0
    assert result['by_environment']['mock']['physical_outcomes']['task']['denominator']==0
    assert result['by_environment']['mock']['execution_reliability']['metrics']['inference_ms']['p99'] is not None
    write_report(result,workspace/'existing-e2e-report')
    original=load_sources([artifacts])['tasks'][0]['task']
    unknown_assessment=judgment(original,notes='Offline test record only; no physical outcome was observed')
    append_evaluation(workspace/'existing-assessments',original,unknown_assessment)
    linked=analyze(load_sources([artifacts],[workspace/'existing-assessments']),load_conditions(DEFAULT_CONDITIONS))
    assert linked['by_environment']['mock']['evaluation_records']==1
    assert linked['by_environment']['mock']['physical_outcomes']['task']['denominator']==0
    write_report(linked,workspace/'existing-e2e-linked-report')
    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts.glob('*.json*')}


def test_lightweight_cli_and_shared_portable_checkpoint_identity(workspace):
    model=workspace/'model';model.mkdir();(model/'model.safetensors').write_bytes(b'fake bytes; never loaded')
    save(model/'config.json',{'type':'metadata hash fixture'})
    from ai.checkpoint_identity import checkpoint_identifier
    from ai.serve.zmq_act_server import checkpoint_identifier as server_identifier
    original=checkpoint_identifier(model)
    shutil.copytree(model,workspace/'moved-model')
    assert server_identifier(model)==original==checkpoint_identifier(workspace/'moved-model')
    command="import sys;import ai.evaluate.main;assert not {'torch','rclpy','zmq'} & set(sys.modules)"
    result=subprocess.run([str(ROOT/'.venv-act/bin/python'),'-c',command],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_generic_action_rejection_is_not_a_guard_limit_claim(workspace):
    value=task(state='FAULT',reason='client_inference_timeout');put_task(workspace,value)
    logs(workspace,value,[{'event':'execution_action_rejected','reason':'execution_stopped_during_poll'}])
    result=report(workspace)
    assert 'action_rejected' in result['cases'][0]['signals'] and 'safety_guard' not in result['cases'][0]['signals']
    assert result['by_environment']['mock']['execution_reliability']['logged_safety_stop_rate']['numerator']==0
    from ai.evaluate.reliability import signals
    assert 'communication' not in signals('controller_command_timeout')
    assert 'controller_timing' in signals('controller_command_timeout')
    assert 'observation_stale' not in signals('stale_or_invalid_observation')


def test_home_only_failure_does_not_recommend_pose_or_grasp_data(workspace):
    value=task(state='FAULT',reason='home_failed: home_command_failed');put_task(workspace,value)
    assess(workspace,value,{'act':'success','home':'failure','task':'failure'})
    result=report(workspace)
    assert not any(item['kind'] in ('coverage_candidate','data_candidate') for item in result['recommendations'])


def test_prefix_quantiles_explicitly_report_memory_cap(workspace,monkeypatch):
    from ai.evaluate import reliability
    monkeypatch.setattr(reliability,'SAMPLE_LIMIT',3)
    value=task();put_task(workspace,value);logs(workspace,value)
    result=report(workspace)
    metric=result['cases'][0]['execution_metrics']['metrics']['inference_ms']
    assert metric['retained_samples']==3 and metric['observed_samples']==4 and metric['truncated']
    assert 'prefix' in metric['quantile_scope']


def test_partial_logs_are_not_concatenated_into_fake_continuity(workspace):
    value=task();put_task(workspace,value);path=logs(workspace,value)
    rows=path.read_text().splitlines()
    path.write_text('\n'.join(rows[:5])+'\n')
    copied=path.with_name('tail_events.jsonl');copied.write_text('\n'.join(rows[5:])+'\n')
    result=report(workspace)
    assert result['cases'][0]['execution_metrics']['complete_client_log'] is False
    assert result['by_environment']['mock']['execution_reliability']['logged_safety_stop_rate']['denominator']==0
    assert any(issue['kind']=='multiple_session_logs' for issue in result['issues'])


def test_conflicting_duplicate_evaluation_id_withholds_scores(workspace):
    value=task();put_task(workspace,value);original=assess(workspace,value,{'task':'success'})
    conflict=read_json(original);conflict['outcomes']['task']='failure'
    save(workspace/'other/evaluation-conflict.json',conflict)
    bundle=load_sources([workspace/'runs'],[workspace/'assessments',workspace/'other'])
    result=analyze(bundle,load_conditions(DEFAULT_CONDITIONS))
    assert result['by_environment']['mock']['physical_outcomes']['task']['denominator']==0


def test_bad_task_identity_and_log_checkpoint_do_not_mix(workspace):
    value=task();put_task(workspace,value)
    other=copy.deepcopy(value);other['checkpoint_id']='different'
    logs(workspace,other)
    save(workspace/'runs/missing_task_result.json',{'state':'COMPLETED'})
    result=report(workspace)
    assert result['cases'][0]['execution_metrics'] is None
    assert any(i['kind']=='log_checkpoint_mismatch' for i in result['issues'])
    assert any(i['kind']=='source_error' for i in result['issues'])


def test_checkpoint_content_change_is_detected_without_model_loading(workspace):
    from ai.checkpoint_identity import checkpoint_identifier
    model=workspace/'runs/train/training/checkpoints/last/pretrained_model';model.mkdir(parents=True)
    (model/'model.safetensors').write_bytes(b'not a model; content identity fixture')
    original=checkpoint_identifier(model)
    run=manifest(original);save(workspace/'runs/train/run.json',run)
    value=task(checkpoint=original);put_task(workspace,value)
    (model/'model.safetensors').write_bytes(b'changed fake weights')
    result=report(workspace)
    assert any(i['kind']=='checkpoint_content_changed' for i in result['issues'])
    assert result['cases'][0]['training_run_id'] is None


def test_checkpoint_filter_limits_offline_runs_too(workspace):
    save(workspace/'runs/a/run.json',manifest('ckpt-a','train-a'))
    save(workspace/'runs/b/run.json',manifest('ckpt-b','train-b'))
    result=report(workspace,checkpoint_ids=['ckpt-a'])
    assert set(result['offline_training_runs'])=={'train-a'}
    assert [item['checkpoint_id'] for item in result['checkpoint_comparison']['checkpoints']]==['ckpt-a']


def test_summary_only_p99_is_not_invented(workspace):
    value=task();put_task(workspace,value)
    save(workspace/'runs/task-1/summary.json',{'session_id':value['session_id'],'checkpoint_id':value['checkpoint_id'],
        'actual_action_fps':19.9,'inference_ms_p50':4,'inference_ms_p95':6,'task':value})
    result=report(workspace)
    summaries=result['cases'][0]['recorded_session_summaries']
    assert summaries[0]['inference_ms_p99'] is None
    assert result['by_environment']['mock']['execution_reliability']['metrics']=={}


def test_empty_and_missing_inputs_are_explicit(workspace):
    empty=workspace/'runs';empty.mkdir()
    result=report(workspace)
    assert result['unique_attempts']==0 and result['by_environment']['real']['physical_outcomes']['task']['rate'] is None
    with pytest.raises(ValueError,match='does not exist'):load_sources([workspace/'missing'])


def test_nonfinite_or_duplicate_keys_are_rejected(workspace):
    for text in ('{"x":NaN}','{"x":1e999}','{"x":1,"x":2}'):
        path=workspace/'bad.json';path.write_text(text)
        with pytest.raises(ValueError):read_json(path)


def test_oversized_jsonl_is_drained_without_losing_next_event(workspace):
    value=task();put_task(workspace,value);path=logs(workspace,value)
    with path.open('a') as stream:
        stream.write('x'*(4*1024*1024+2)+'\n')
        stream.write(json.dumps({'event':'error','session_id':value['session_id'],'reason':'client_inference_timeout'})+'\n')
    result=report(workspace)
    assert 'inference_timeout' in result['cases'][0]['signals']
    assert result['cases'][0]['execution_metrics']['corrupt_lines']==1


def test_offline_accuracy_has_numerator_and_chunk_position_denominator():
    result=offline_evaluation(manifest())
    counts=result['accuracy_counts']['summary']
    assert counts['gripper_accuracy_at_0_5']=={'numerator':9,'denominator':10,'rate':.9}
    assert counts['hold_gripper_accuracy']=={'numerator':8,'denominator':10,'rate':.8}
    inconsistent=manifest();inconsistent['offline_validation']['summary']['gripper_accuracy_at_0_5']=.93
    assert offline_evaluation(inconsistent)['status']=='blocked'


def test_private_vendor_ros_environment_output_paths_are_blocked():
    from ai.evaluate.records import local_path,output_path
    with pytest.raises(ValueError):local_path(ROOT/'private/not-read')
    for path in (ROOT/'ros2_ws/new-report',ROOT/'ai/lerobot_source/new-report',ROOT/'.venv-act/new-report'):
        with pytest.raises(ValueError):output_path(path)


def test_launcher_failure_context_is_separate_from_original_task(workspace):
    value=task(state='FAULT',reason='inference_cannot_keep_up');put_task(workspace,value)
    original=identity(value)
    save(workspace/'runs/task-1/result.json',{'status':'failed','reason':'RuntimeError: Inference server disconnected',
                                          'client':{'task':value},'task':value})
    result=report(workspace)
    assert result['unique_attempts']==1 and 'communication' in result['cases'][0]['signals']
    evidence=result['cases'][0]['signals']['communication']
    assert len(evidence)==1 and evidence[0]['origin']=='launcher'
    assert identity(read_json(workspace/'runs/task-1/task_result.json'))==original
