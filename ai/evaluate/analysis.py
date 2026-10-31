"""Separate offline error, logged reliability, and operator-assessed task outcomes."""
from collections import Counter, defaultdict
import math
from statistics import NormalDist

from ai.evaluate.records import CAUSES, STAGES, canonical, read_json
from ai.evaluate.reliability import SAMPLE_LIMIT, distribution, signals


def load_conditions(path):
    config=read_json(path)
    if config.get('schema_version')!=1: raise ValueError('Unknown conditions version')
    if not isinstance(config.get('regions'),list) or len(set(config['regions']))!=len(config['regions']): raise ValueError('Invalid regions')
    for field in ('yaw_bins','depth_bins_cm'):
        previous=None;names=set()
        for band in config.get(field,[]):
            if (not isinstance(band.get('id'),str) or band['id'] in names
                    or any(type(band.get(k)) not in (int,float) or not math.isfinite(band[k]) for k in ('min','max'))
                    or band['min']>=band['max'] or previous is not None and band['min']<previous):
                raise ValueError(f'Invalid/overlapping {field}')
            previous=band['max'];names.add(band['id'])
    return config


def bin_value(number,bands):
    if number is None:return 'unknown'
    for i,band in enumerate(bands):
        if band['min']<=number<band['max'] or i==len(bands)-1 and number==band['max']:return band['id']
    return 'unbinned' if not bands else 'outside_configured_bins'


def conditions_for(record,config):
    raw=record.get('conditions',{}) if record else {}
    region=raw.get('region')
    return {**raw,'region':region if region in config['regions'] else 'unknown',
            'yaw_bin':bin_value(raw.get('yaw_deg'),config.get('yaw_bins',[])),
            'depth_bin':bin_value(raw.get('depth_cm'),config.get('depth_bins_cm',[])),
            'depth_condition':raw.get('depth_condition') or 'unknown',
            'training_coverage':raw.get('training_coverage','unknown')}


def rate(numerator,denominator):
    if not 0<=numerator<=denominator: raise ValueError('Invalid binomial counts')
    if not denominator:return {'numerator':0,'denominator':0,'rate':None,'wilson_95':None}
    z=NormalDist().inv_cdf(.975);n=denominator;p=numerator/n
    center=(p+z*z/(2*n))/(1+z*z/n)
    margin=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return {'numerator':numerator,'denominator':denominator,'rate':p,'wilson_95':[max(0,center-margin),min(1,center+margin)]}


def physical_rates(tasks):
    result={}
    for stage in STAGES:
        included=[];excluded=Counter();successes=0
        for item in tasks:
            record=item['evaluation']
            if record is None:excluded['unassessed']+=1;continue
            if record['validity']!='valid':excluded['invalid' if record['validity']=='invalid' else 'validity_unknown']+=1;continue
            outcome=record['outcomes'][stage]
            if outcome not in ('success','failure'):excluded[outcome]+=1;continue
            included.append(item['task']['task_run_id']);successes+=outcome=='success'
        result[stage]={**rate(successes,len(included)), 'included_task_run_ids':included,'excluded':dict(excluded)}
    return result


def case_evidence(item):
    task,log,record=item['task'],item['log'],item['evaluation']
    result=defaultdict(list)
    for context in item.get('launcher_context',[]):
        for code in signals(context['reason']):result[code].append(context)
    if log:
        for code,entries in log['observed_codes'].items():result[code].extend(entries)
    if not log or not log['complete_client_log']:
        result['incomplete_record'].append({'origin':'availability','reason':'Missing/partial/corrupt client event coverage'})
    for field,value in [('reason',task.get('reason')),('act_stop_reason',task.get('act_stop_reason')),
                        ('home.reason',task.get('home',{}).get('reason')),
                        ('execution.reason',task.get('execution',{}).get('reason')),
                        ('execution.state_reason',task.get('execution',{}).get('state_reason'))]:
        for code in signals(value):
            result[code].append({'origin':'task_field','certainty':'reported_event_not_root_cause','source':item['sources'][0],
                                 'field':field,'reason':value})
    if task.get('emergency_requested') is True or task.get('execution',{}).get('emergency_requested') is True:
        result['emergency_stop'].append({'origin':'task_field','field':'emergency_requested','certainty':'reported_event_not_root_cause'})
    if task.get('user_aborted') is True:
        result['user_abort'].append({'origin':'task_field','field':'user_aborted','certainty':'reported_event_not_root_cause'})
    if task.get('home',{}).get('state')=='FAILED':
        result['home_failure'].append({'origin':'task_field','field':'home.state','reason':'FAILED','certainty':'reported_event_not_root_cause'})
    if record:
        for cause in record.get('causes',[]):result[cause['code']].append({'origin':'operator','certainty':cause['certainty'],
                        'evaluation_id':record['evaluation_id'],'reason':cause['evidence']})
        if record['validity']=='unknown' or record['outcomes']['task']=='unknown':
            result['unassessed'].append({'origin':'assessment_availability','evaluation_id':record['evaluation_id'],
                                         'reason':'Record exists but Task outcome or experiment validity remains unknown'})
    else:result['unassessed'].append({'origin':'assessment_availability','reason':'No compatible unambiguous operator assessment'})
    if task.get('state') in ('FAULT','ABORTED') and not any(code not in ('unassessed','incomplete_record') for code in result):
        result['unknown_cause'].append({'origin':'availability','reason':'Recorded termination does not identify root cause'})
    return dict(result)


def reliability(tasks):
    evidence={item['task']['task_run_id']:case_evidence(item) for item in tasks}
    logged_safety=[];unknown=[];pooled=defaultdict(list);observed=Counter()
    state_counts=Counter();signals_by_trial=Counter();timeouts=[];stop_failure=[];stop_known=[];stop_confirmed=[];home_attempts=[];home_arrived=[]
    for item in tasks:
        task,log=item['task'],item['log'];key=task['task_run_id'];codes=evidence[key]
        state_counts[task['state']]+=1
        for code in codes:signals_by_trial[code]+=1
        if any(code in codes for code in ('safety_guard','emergency_stop','safety_conditions')):logged_safety.append(key)
        elif not log or not log['complete_client_log']:unknown.append(key)
        if 'timeout' in str(task.get('reason','')).lower() or 'inference_timeout' in codes:timeouts.append(key)
        stop_attempted=(type(task.get('act_action_count')) is int and task['act_action_count']>0) or (log and log['events'].get('execution_stop_requested',0)>0)
        if stop_attempted and type(task.get('act_stop_confirmed')) is bool:
            stop_known.append(key)
            if task['act_stop_confirmed']:stop_confirmed.append(key)
            else:stop_failure.append(key)
        home=task.get('home',{})
        if home.get('started_monotonic_ns') is not None and home.get('state') in ('COMPLETED','FAILED'):
            home_attempts.append(key)
            if home['state']=='COMPLETED':home_arrived.append(key)
        if log:
            for name,values in log['_samples'].items():
                observed[name]+=log['_sample_counts'].get(name,0)
                pooled[name].extend(values[:max(0,SAMPLE_LIMIT-len(pooled[name]))])
    return {'attempts':len(tasks),'final_task_states':dict(state_counts), 'timeout_task_run_ids':timeouts,
            'stop_unconfirmed_task_run_ids':stop_failure,'signal_trial_counts':dict(signals_by_trial),
            'act_stop_confirmation_rate':{**rate(len(stop_confirmed),len(stop_known)),'included_task_run_ids':stop_known},
            'home_software_arrival_rate':{**rate(len(home_arrived),len(home_attempts)),'included_task_run_ids':home_attempts},
            'deadline_misses_observed':sum(item['log']['deadline_misses_observed'] for item in tasks if item['log']),
            'logged_safety_stop_rate':{**rate(len(logged_safety),len(tasks)-len(unknown)),
                'included_task_run_ids':[i['task']['task_run_id'] for i in tasks if i['task']['task_run_id'] not in unknown],
                'positive_task_run_ids':logged_safety,'unknown_task_run_ids':unknown},
            'metrics':{name:distribution(values,observed=observed[name]) for name,values in pooled.items()},
            'metrics_scope':'Pooled valid samples from selected client logs; summary-only values are not pooled',
            'source_note':'Signal occurrence counts are not inferred physical root causes; safety_fault is a generic fault latch, not a Guard classification.'}


def offline_evaluation(run):
    evaluation=run.get('offline_validation')
    split=run.get('split',{});train=split.get('train',[]);validation=split.get('validation',[])
    issues=[]
    if not isinstance(train,list) or not isinstance(validation,list):issues.append('Invalid split lists')
    else:
        if any(type(ep) is not int or ep<0 for ep in train+validation):issues.append('Invalid Episode IDs')
        elif len(train)!=len(set(train)) or len(validation)!=len(set(validation)) or set(train)&set(validation):issues.append('Episode split overlap/invalid IDs')
    if run.get('status')!='complete':issues.append('Training run is not complete')
    if issues:return {'status':'blocked','role':'held_out_validation','issues':issues,'generalization_validated':False}
    if not evaluation or evaluation.get('status')!='evaluated':
        return {'status':'not_available','role':'held_out_validation','reason':evaluation.get('reason') if evaluation else 'No offline validation artifact',
                'issues':issues,'generalization_validated':False}
    if (not isinstance(evaluation.get('episodes'),list) or any(type(ep) is not int for ep in evaluation['episodes'])
            or not isinstance(evaluation.get('per_episode'),dict)):
        return {'status':'blocked','role':'held_out_validation','issues':['Malformed offline episodes/metrics'],'generalization_validated':False}
    if not validation or set(evaluation['episodes'])!=set(validation) or len(evaluation['episodes'])!=len(set(evaluation['episodes'])):
        issues.append('Evaluated episodes do not equal unique held-out split')
    if set(evaluation['per_episode'])!={str(ep) for ep in validation}:issues.append('Per-episode metrics mismatch')
    accuracy_counts={}
    for label,summary in [('summary',evaluation.get('summary',{})),*evaluation.get('per_episode',{}).items()]:
        if not isinstance(summary,dict):issues.append(f'Invalid metric object {label}');continue
        for key in ('joint_mae_rad','hold_joint_mae_rad','gripper_mae','gripper_accuracy_at_0_5','hold_gripper_accuracy','valid_chunk_positions'):
            value=summary.get(key)
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:issues.append(f'Invalid metric {label}/{key}')
            elif ('accuracy' in key and value>1) or key=='valid_chunk_positions' and value<=0:issues.append(f'Invalid metric range {label}/{key}')
        for key in ('joint_mae_per_dimension_rad','hold_joint_mae_per_dimension_rad'):
            if key in summary and (not isinstance(summary[key],list) or len(summary[key])!=6
                    or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in summary[key])):issues.append(f'Invalid six-joint metric {label}/{key}')
        n=summary.get('valid_chunk_positions')
        if type(n) is not int or n<=0:issues.append(f'Invalid chunk position count {label}')
        else:
            accuracy_counts[label]={}
            for key in ('gripper_accuracy_at_0_5','hold_gripper_accuracy'):
                accuracy=summary.get(key)
                if type(accuracy) in (int,float) and math.isfinite(accuracy) and 0<=accuracy<=1:
                    correct=round(accuracy*n)
                    if not math.isclose(accuracy*n,correct,abs_tol=1e-6,rel_tol=1e-9):issues.append(f'Inconsistent accuracy count {label}/{key}')
                    else:accuracy_counts[label][key]={'numerator':correct,'denominator':n,'rate':accuracy}
    counts=[value.get('valid_chunk_positions') for value in evaluation['per_episode'].values() if isinstance(value,dict)]
    if all(type(n) is int for n in counts) and isinstance(evaluation.get('summary'),dict) and sum(counts)!=evaluation['summary'].get('valid_chunk_positions'):
        issues.append('Offline summary/per-Episode chunk counts mismatch')
    return {'status':'blocked' if issues else 'evaluated','role':'held_out_validation','issues':issues,
            'split':split,'metrics':evaluation if not issues else None,'generalization_validated':False,
            'accuracy_counts':accuracy_counts if not issues else None,
            'accuracy_count_note':'Counts use valid_chunk_positions from the existing evaluator. Overlapping chunk targets are not independent robot trials; no binomial trial CI is claimed for these positions.',
            'interpretation':'Unnormalized masked action-chunk error, not robot task success; no automatic train-data evaluation.'}


def duration(item):
    task=item['task'];start,end=task.get('started_monotonic_ns'),task.get('ended_monotonic_ns')
    return (end-start)/1e9 if type(start) is int and type(end) is int and end>=start else None


def group(tasks,config):
    conditions=defaultdict(list)
    for item in tasks:
        values=conditions_for(item['evaluation'],config)
        for axis in ('region','yaw_bin','depth_bin','depth_condition','training_coverage'):
            conditions[(axis,values[axis])].append(item)
        joint=canonical({axis:values[axis] for axis in ('region','yaw_bin','depth_bin','depth_condition','training_coverage')})
        conditions[('combined',joint)].append(item)
    evaluated=[item for item in tasks if item['evaluation']]
    by_code=defaultdict(lambda:defaultdict(list));categories=defaultdict(set)
    for item in tasks:
        key=item['task']['task_run_id']
        for code,entries in case_evidence(item).items():
            category=CAUSES.get(code,'unknown');categories[category].add(key)
            by_code[code]['task_run_ids'].append(key)
            for field,selected in (
                    ('log_signal_task_run_ids',any(entry['origin'] in ('log_signal','task_field','launcher') for entry in entries)),
                    ('operator_observed_task_run_ids',any(entry['origin']=='operator' and entry['certainty']=='observed' for entry in entries)),
                    ('operator_suspected_task_run_ids',any(entry['origin']=='operator' and entry['certainty']=='suspected' for entry in entries))):
                if selected:by_code[code][field].append(key)
    return {'attempts':len(tasks),'evaluation_records':len(evaluated),
            'validity_counts':dict(Counter(item['evaluation']['validity'] if item['evaluation'] else 'unassessed' for item in tasks)),
            'physical_outcomes':physical_rates(tasks),'execution_reliability':reliability(tasks),
            'failure_analysis':{'by_code':{code:{'category':CAUSES.get(code,'unknown'),'attempts':len(value['task_run_ids']),**value}
                                           for code,value in sorted(by_code.items())},
                                'by_category':{category:{'attempts':len(ids),'task_run_ids':sorted(ids)} for category,ids in categories.items()},
                                'note':'Multiple labels per attempt. Unknown/unassessed are availability states, not proven task failures.'},
            'task_duration_seconds':distribution([value for item in tasks if (value:=duration(item)) is not None]),
            'completed_task_duration_seconds':distribution([value for item in tasks if item['task']['state']=='COMPLETED' and (value:=duration(item)) is not None]),
            'act_duration_seconds':distribution([item['task']['act_elapsed_seconds'] for item in tasks
                    if type(item['task'].get('act_elapsed_seconds')) in (int,float) and item['task']['act_elapsed_seconds']>=0]),
            'conditions':[{'axis':axis,'value':value,'attempts':len(items),'physical_outcomes':physical_rates(items)}
                          for (axis,value),items in sorted(conditions.items())]}


def recommendations(tasks,runs,config):
    result=[]
    for env in sorted({item['environment'] for item in tasks}):
        items=[item for item in tasks if item['environment']==env]
        unassessed=[item['task']['task_run_id'] for item in items if not item['evaluation']
                    or item['evaluation']['validity']!='valid' or item['evaluation']['outcomes']['task']=='unknown']
        if unassessed:result.append({'kind':'evaluation','environment':env,'candidate':'Complete valid operator stage assessments and actual pose; unknown/excluded results do not establish physical success',
                                    'evidence':{'task_run_ids':unassessed},'causal_claim':False})
        codes=defaultdict(list)
        for item in items:
            for code in case_evidence(item):codes[code].append(item['task']['task_run_id'])
        for family,candidate in [({'camera_observation','joint_state','action_timing','observation_stale','observation_quality','observation_timing','controller_timing','inference_timeout','communication','watchdog'},'Investigate observation/clock/transport quality before attributing failures to policy data'),
                                 ({'safety_guard','joint_limit','tracking_error','safety_conditions'},'Review validated limits, target semantics and tracking with control diagnostics'),
                                 ({'gripper_service'},'Investigate gripper service reliability; command success does not prove physical grasp')]:
            matched=family & codes.keys()
            if matched:result.append({'kind':'system','environment':env,'candidate':candidate,
                         'evidence':{'signals':{code:codes[code] for code in sorted(matched)}},'causal_claim':False})
        for stage,candidate in [('grasp','Collect additional successful grasp demonstrations under the failing conditions'),
                                ('placement','Investigate placement examples and placement criteria'),('retreat','Collect safe retreat demonstrations')]:
            failures=[item['task']['task_run_id'] for item in items if item['evaluation'] and item['evaluation']['validity']=='valid'
                      and item['evaluation']['outcomes'][stage]=='failure']
            if failures:result.append({'kind':'data_candidate','environment':env,'candidate':candidate,
                      'evidence':{'stage':stage,'rate':physical_rates(items)[stage],'failure_task_run_ids':failures},
                      'scope':'Real collection hypothesis' if env=='real' else 'Mock/Synthetic analysis only; not evidence for real data collection',
                      'causal_claim':False})
        for cell in group(items,config)['conditions']:
            if cell['axis'] not in ('region','yaw_bin','depth_condition','depth_bin'):continue
            stage=next((stage for stage in STAGES[:6] if cell['physical_outcomes'][stage]['denominator']>cell['physical_outcomes'][stage]['numerator']),None)
            if stage is None:continue  # Home/control failure alone is not coverage evidence.
            metric=cell['physical_outcomes'][stage];failures=metric['denominator']-metric['numerator']
            if failures and cell['value'] not in ('unknown','unbinned'):
                ids=[item['task']['task_run_id'] for item in items if item['evaluation'] and item['evaluation']['validity']=='valid'
                     and item['evaluation']['outcomes'][stage]=='failure' and conditions_for(item['evaluation'],config)[cell['axis']]==cell['value']]
                result.append({'kind':'coverage_candidate','environment':env,'candidate':f"Additional controlled evaluation and successful demonstrations for {cell['axis']}={cell['value']}",
                               'evidence':{'stage':stage,'metric':metric,'failure_task_run_ids':ids},'causal_claim':False,
                               'scope':'Candidate, not a confirmed cause; validate sample size and pose feasibility'})
        recovery=[item['task']['task_run_id'] for item in items if item['evaluation'] and item['evaluation']['validity']=='valid'
                  and any(cause['code'] in ('object_drop','grasp_failure') for cause in item['evaluation'].get('causes',[]))]
        if recovery:result.append({'kind':'recovery_experiment','environment':env,'candidate':'Design a separate corrective/recovery experiment with confirmed failure states',
                                  'evidence':{'task_run_ids':recovery},'causal_claim':False})
    for run_id,run in runs.items():
        offline=offline_evaluation(run)
        if offline['status']=='evaluated' and offline['metrics']['summary']['joint_mae_rad']>=offline['metrics']['summary']['hold_joint_mae_rad']:
            result.append({'kind':'training_candidate','candidate':'Compare training steps and ACT chunk settings while holding Dataset, split and seed fixed',
                    'evidence':{'training_run_id':run_id,'offline_summary':offline['metrics']['summary']},'causal_claim':False,
                    'note':'Offline error alone cannot establish a manipulation cause or robot success'})
    return result


def comparison(tasks,runs,checkpoint_groups):
    items=[]
    for ckpt,summary in checkpoint_groups.items():
        cases=[item for item in tasks if item['task']['checkpoint_id']==ckpt]
        linked={item['training_run_id'] for item in cases if item['training_run_id']}
        run=runs[next(iter(linked))] if len(linked)==1 else next((r for r in runs.values() if r.get('_checkpoint_id')==ckpt),None)
        items.append({'checkpoint_id':ckpt,'training_run_id':run.get('run_id') if run else None,
                      'dataset_id':run.get('dataset_fingerprint') if run else None,
                      'successful_demonstrations':run.get('_dataset_validation',{}).get('success_episodes') if run else None,
                      'split':run.get('split') if run else None,'act_settings':run.get('settings') if run else None,
                      'completed_step':run.get('completed_step') if run else None,
                      'offline':offline_evaluation(run) if run else {'status':'unknown','reason':'No verified or explicitly reported training link'},
                      'by_environment':summary,'training_link_methods':sorted({item['training_link'] for item in cases}),
                      'direct_ranking':False})
    limits=[]
    if any(any(method in ('unknown','operator_reported_unverified_checkpoint_link') for method in item['training_link_methods']) for item in items):
        limits.append('Some Checkpoint-to-training links are missing or operator-reported rather than content-verified')
    if len(items)>1:
        datasets={canonical(item['dataset_id']) for item in items};splits={canonical(item['split']) for item in items}
        if len(datasets)>1 or any(item['dataset_id'] is None for item in items):limits.append('Dataset versions differ or are unknown')
        if len(splits)>1 or any(item['split'] is None for item in items):limits.append('Train/Validation splits differ or are unknown')
        for env in sorted({item['environment'] for item in tasks}):
            signatures=[];missing=False
            for ckpt in checkpoint_groups:
                counter=Counter()
                for item in tasks:
                    if item['environment']!=env or item['task']['checkpoint_id']!=ckpt:continue
                    record=item['evaluation']
                    if not record or record['validity']!='valid':continue
                    conditions=record.get('conditions',{})
                    if any(conditions.get(key) in (None,'unknown','') for key in ('region','yaw_deg','depth_cm','x_cm','y_cm','training_coverage','evaluation_protocol_id','workspace_id','camera_setup_id','object_id')):missing=True
                    counter[canonical(conditions)]+=1
                if not counter:missing=True
                signatures.append(counter)
            if missing or any(value!=signatures[0] for value in signatures[1:]):limits.append(f'{env}: exact pose/context distributions differ, are unbalanced, or lack information')
    return {'checkpoints':items,'limitations':limits,'ranking_performed':False,
            'note':'Mock/Synthetic/Real are separate; no claim of checkpoint superiority from unmatched or unverified evaluation conditions'}


def analyze(bundle,config,*,checkpoint_ids=(),condition_filters=None):
    filters=condition_filters or {}
    numeric={'yaw_deg','depth_cm','x_cm','y_cm'}
    for key,value in filters.items():
        if key in numeric and not math.isfinite(float(value)):raise ValueError('Numeric condition filter must be finite')
    def matches(record):
        values=conditions_for(record,config)
        return all((values.get(key) is not None and values[key]==float(value)) if key in numeric else str(values.get(key))==str(value)
                   for key,value in filters.items())
    tasks=[item for item in bundle['tasks'] if (not checkpoint_ids or item['task']['checkpoint_id'] in checkpoint_ids)
           and matches(item['evaluation'])]
    env_groups={env:group([item for item in tasks if item['environment']==env],config) for env in sorted({item['environment'] for item in tasks}|{'real'})}
    linked_ids={item['training_run_id'] for item in tasks if item['training_run_id']}
    runs={key:value for key,value in bundle['training_runs'].items() if not checkpoint_ids
          or value.get('_checkpoint_id') in checkpoint_ids or key in linked_ids}
    all_checkpoint_ids={item['task']['checkpoint_id'] for item in tasks}|{run['_checkpoint_id'] for run in runs.values() if run.get('_checkpoint_id')}
    checkpoints={ckpt:{env:group([item for item in tasks if item['task']['checkpoint_id']==ckpt and item['environment']==env],config)
                       for env in sorted({item['environment'] for item in tasks if item['task']['checkpoint_id']==ckpt} or {'real'})}
                 for ckpt in sorted(all_checkpoint_ids)}
    cases=[]
    for item in tasks:
        log=item['log'];task=item['task']
        cases.append({'task_run_id':task['task_run_id'],'session_id':task['session_id'],'checkpoint_id':task['checkpoint_id'],
                      'task_id':task.get('task_id'),'environment':item['environment'],'state':task['state'],
                      'conditions':conditions_for(item['evaluation'],config),'evaluation_id':item['evaluation']['evaluation_id'] if item['evaluation'] else None,
                      'evaluation':item['evaluation'],'software_task_success':task.get('success'),
                      'execution_failure_stage':task.get('failure_stage'),
                      'operator_failure_stage':item['evaluation'].get('failure_stage') if item['evaluation'] else None,
                      'recorded_stop_reason':task.get('act_stop_reason'),'home_state':task.get('home',{}).get('state'),
                      'recorded_session_summaries':item.get('recorded_session_summaries',[]),
                      'operator_physical_outcomes':item['evaluation']['outcomes'] if item['evaluation'] else {stage:'unknown' for stage in STAGES},
                      'signals':case_evidence(item),'sources':item['sources'],'training_run_id':item['training_run_id'],'training_link':item['training_link'],
                      'execution_metrics':{key:value for key,value in log.items() if not key.startswith('_') and key!='task'} if log else None,
                      'physical_task_success_inferred':False})
    return {'schema_version':1,'evaluation_types':['offline_policy_error','execution_reliability','operator_task_success'],
            'filters':{'checkpoint_ids':list(checkpoint_ids),'conditions':filters},'input_files':bundle['input_files'],
            'unique_attempts':len(tasks),'issues':bundle['issues'],'by_environment':env_groups,'by_checkpoint':checkpoints,
            'unlinked_execution_logs':bundle.get('unlinked_execution_logs',[]),
            'offline_training_runs':{key:{'source':run['_source'],'dataset_id':run.get('dataset_fingerprint'),'settings':run.get('settings'),
                    'split':run.get('split'),'evaluation':offline_evaluation(run)} for key,run in runs.items()},
            'checkpoint_comparison':comparison(tasks,runs,checkpoints),
            'recommendations':recommendations(tasks,runs,config),'cases':cases,
            'evaluation_history':bundle['evaluation_history'],'condition_configuration':config,
            'policies':{'physical_denominator':'Latest compatible unambiguous operator record; validity=valid and outcome=success/failure for that stage. Abort/fault counts only when explicitly assessed. Invalid, unknown and not_attempted excluded and counted.',
                        'execution_denominator':'Unique loaded attempts in each environment; invalid trials remain visible as reliability attempts. Logged safety-stop denominator excludes unknown log coverage.',
                        'uncertainty':'Wilson two-sided 95% interval; n=0 gives unknown; small samples require further evaluation.',
                        'causality':'Logged error strings identify reported events only. Operator suspected causes remain suspected. Recommendations are hypotheses.',
                        'collection_vs_evaluation':'108 is a successful-demonstration collection goal, not evaluation trial count or minimum sample size.'}}
