"""Read existing run artifacts, deduplicate by IDs, never trust stale absolute paths."""
from collections import defaultdict
import hashlib
import os
from pathlib import Path

from ai.checkpoint_identity import checkpoint_identifier
from ai.evaluate.records import (ROOT, canonical, environment, identity, latest_records, local_path,
                                 read_json, task_key, validate_record)
from ai.evaluate.reliability import scan_log, signals

SKIP = {".git", "private", "capstone", "pipet-physical-ai", "datasets", "node_modules", "__pycache__", ".venv", ".venv-act"}


def discover(paths):
    found = set()
    for value in paths:
        path = local_path(value)
        if not path.exists(): raise ValueError(f"Input path does not exist: {path}")
        if path.is_file(): found.add(path); continue
        for directory, children, files in os.walk(path, followlinks=False):
            parent = Path(directory)
            if (parent/'meta/info.json').is_file() or (parent/'model.safetensors').is_file():
                children[:] = []; continue
            children[:] = sorted(name for name in children if name not in SKIP and not (parent/name).is_symlink())
            for name in files:
                if (name in ('run.json','result.json','summary.json','task_result.json') or name.endswith('_task_result.json')
                        or name.endswith('events.jsonl') or name.startswith('evaluation-') and name.endswith('.json')):
                    file = parent/name
                    if not file.is_symlink(): found.add(file)
    return sorted(found)


def _task_candidates(value):
    if not isinstance(value, dict): return []
    result=[]
    if 'task_run_id' in value: result.append(value)
    if isinstance(value.get('task'),dict): result.append(value['task'])
    if isinstance(value.get('client'),dict): result += _task_candidates(value['client'])
    return result


def load_sources(paths, record_paths=()):
    issues, raw_tasks, training, logs, records = [], defaultdict(list), defaultdict(list), defaultdict(list), []
    summaries=defaultdict(list)
    launcher_contexts=defaultdict(list)
    record_ids, log_hashes = {}, {}
    corrupt_evaluations=False
    files = sorted(set(discover(paths) + (discover(record_paths) if record_paths else [])))
    def add_task(task, path):
        try:
            key=task_key(task)
            if task.get('state') not in ('IDLE','PREPARING','READY','RUNNING_ACT','STOPPING_ACT','VERIFYING_STOP','RETURNING_HOME','COMPLETED','ABORTED','FAULT'):
                raise ValueError('Missing/unknown Task state')
            execution=task.get('execution',{})
            if not isinstance(execution,dict) or not isinstance(task.get('home',{}),dict): raise ValueError('Invalid execution/home object')
            if task.get('reason') is not None and not isinstance(task['reason'],str):raise ValueError('Invalid Task reason')
            if execution.get('session_id') not in (None,key[1]) or execution.get('checkpoint_id') not in (None,key[2]):
                raise ValueError('Task/Execution identity conflict')
            raw_tasks[key].append((task,str(path)))
        except ValueError as exc: issues.append({'kind':'invalid_task','source':str(path),'reason':str(exc)})
    for path in files:
        try:
            if path.suffix=='.jsonl':
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    while block:=stream.read(1024*1024): digest.update(block)
                checksum=digest.hexdigest()
                if checksum in log_hashes:
                    issues.append({'kind':'duplicate_event_log','source':str(path),'same_as':log_hashes[checksum]}); continue
                log_hashes[checksum]=str(path)
                for log in scan_log(path,issues):
                    logs[log['session_id']].append(log)
                    if log['task']: add_task(log['task'],path)
                continue
            value=read_json(path)
            if not isinstance(value,dict): raise ValueError('Metadata must be an object')
            if (path.name=='task_result.json' or path.name.endswith('_task_result.json')) and not _task_candidates(value):
                raise ValueError('Task result is missing Task identity')
            if 'evaluation_id' in value or path.name.startswith('evaluation-'):
                validate_record(value)
                key=value['evaluation_id']
                if key in record_ids and identity(value)!=identity(record_ids[key]): raise ValueError('Conflicting duplicate evaluation ID')
                record_ids[key]=value
                continue
            for task in _task_candidates(value):
                add_task(task,path)
                if isinstance(value.get('reason'),str) and isinstance(value.get('client'),dict):
                    try:key=task_key(task)
                    except ValueError:continue
                    context={'origin':'launcher','certainty':'reported_event_not_root_cause','source':str(path),
                             'field':'reason','reason':value['reason']}
                    if context not in launcher_contexts[key]:launcher_contexts[key].append(context)
            for candidate in (value,value.get('client',{})):
                if isinstance(candidate,dict) and candidate.get('session_id') and 'actual_action_fps' in candidate:
                    allowed=('checkpoint_id','session_id','status','actions','missed_action_deadlines','actual_action_fps',
                             'inference_ms_p50','inference_ms_p95','inference_ms_max','metrics_truncated')
                    summaries[candidate['session_id']].append({'source':str(path),'values':{k:candidate[k] for k in allowed if k in candidate},
                                                             'inference_ms_p99':None,'scope':'Recorded summary only; P99 was not recorded'})
            if all(key in value for key in ('run_id','dataset_fingerprint','settings','split')):
                if (not isinstance(value['run_id'],str) or not isinstance(value['dataset_fingerprint'],dict)
                        or not isinstance(value['settings'],dict) or not isinstance(value['split'],dict)
                        or not isinstance(value.get('checkpoint_validation',{}),dict)
                        or value.get('offline_validation') is not None and not isinstance(value['offline_validation'],dict)):
                    raise ValueError('Invalid training manifest structure')
                training[value['run_id']].append((value,path))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            issues.append({'kind':'source_error','source':str(path),'reason':str(exc)})
            if path.name.startswith('evaluation-'):corrupt_evaluations=True
    records=list(record_ids.values())
    evaluations,history_issues=latest_records(records);issues.extend(history_issues)
    if corrupt_evaluations:
        issues.append({'kind':'evaluation_store_incomplete','reason':'Physical judgments withheld until corrupt/unidentifiable evaluation records are resolved'})
        evaluations={}
    runs={}
    for key,values in training.items():
        distinct={identity(value) for value,path in values}
        if len(distinct)>1:
            issues.append({'kind':'conflicting_training_run','run_id':key});continue
        value,path=values[0]
        value=dict(value)
        value['_source']=str(path)
        validation=path.parent/'dataset_validation.json'
        if validation.is_file():
            try:
                validation_value=read_json(validation)
                if not isinstance(validation_value,dict):raise ValueError('Invalid Dataset validation metadata')
                value['_dataset_validation']=validation_value
            except (ValueError,OSError) as exc:issues.append({'kind':'dataset_validation_metadata','source':str(validation),'reason':str(exc)})
        # New manifests carry the same content ID as the inference server.
        verification=value.get('checkpoint_validation',{})
        value['_checkpoint_id']=verification.get('checkpoint_id') if verification.get('valid') is True else None
        candidates=[path.parent/'training/checkpoints/last/pretrained_model']
        if isinstance(value.get('checkpoint'),str):
            original=Path(value['checkpoint'])
            if original.is_relative_to(ROOT): candidates.append(original)
        for model in candidates:
            try:model=local_path(model)
            except ValueError:continue
            if (model/'model.safetensors').is_file():
                actual=checkpoint_identifier(model)
                if value['_checkpoint_id'] and actual!=value['_checkpoint_id']:
                    issues.append({'kind':'checkpoint_content_changed','run_id':key,'source':str(model)})
                    value['_checkpoint_id']=None;value['_checkpoint_conflict']=True
                else:value['_checkpoint_id']=actual
                break
        runs[key]=value
    tasks=[]
    # Same Task run ID with different sessions/checkpoints is an identity conflict,
    # not multiple attempts to silently count.
    run_keys,session_keys=defaultdict(set),defaultdict(set)
    for key in raw_tasks:
        run_keys[key[0]].add(key);session_keys[key[1]].add(key)
    for key,values in raw_tasks.items():
        distinct={identity(task) for task,path in values}
        if len(distinct)>1 or len(run_keys[key[0]])>1 or len(session_keys[key[1]])>1:
            issues.append({'kind':'conflicting_task','task_key':list(key),'sources':[path for task,path in values]});continue
        task=dict(values[0][0]);record=evaluations.get(key)
        if record and (record['task_sha256']!=identity(task) or record['environment']!=environment(task)):
            issues.append({'kind':'evaluation_link_mismatch','task_key':list(key)});record=None
        candidates=[]
        for candidate in logs.get(key[1],[]):
            if candidate['checkpoint_ids'] and candidate['checkpoint_ids']!=[key[2]]:
                issues.append({'kind':'log_checkpoint_mismatch','task_key':list(key),'source':candidate['path']})
            elif candidate['environment'] not in ('unknown',environment(task)):
                issues.append({'kind':'log_environment_mismatch','task_key':list(key),'source':candidate['path']})
            else:candidates.append(candidate)
        log=max(candidates,key=lambda item:(item['complete_client_log'],sum(item['events'].values())),default=None)
        if len(candidates)>1:issues.append({'kind':'multiple_session_logs','task_key':list(key),'selected':log['path'],
                                        'reason':'One most complete client log selected; partial logs not concatenated'})
        links=[run for run in runs.values() if run.get('_checkpoint_id')==key[2] and not run.get('_checkpoint_conflict')]
        training_link='checkpoint_content_id' if len(links)==1 else 'unknown'
        run=links[0] if len(links)==1 else None
        if record and record.get('training_run_id'):
            named=runs.get(record['training_run_id'])
            if run and run['run_id']!=record['training_run_id']:
                issues.append({'kind':'training_link_conflict','task_key':list(key)});record=None
            elif run is None and named and not named.get('_checkpoint_conflict'):
                run=named;training_link='operator_reported_unverified_checkpoint_link'
        if record and record.get('dataset_id') and run and run['dataset_fingerprint'].get('sha256')!=record['dataset_id']:
            issues.append({'kind':'dataset_link_conflict','task_key':list(key)});record=None
        if record and record.get('related_episode_ids') and run:
            ids=[]
            for field in ('train','validation'):
                if isinstance(run['split'].get(field),list):ids.extend(run['split'][field])
            available={ep for ep in ids if type(ep) is int}
            if not set(record['related_episode_ids'])<=available:
                issues.append({'kind':'related_episode_not_in_dataset_manifest','task_key':list(key)})
        tasks.append({'task':task,'sources':sorted(set(path for item,path in values)), 'environment':environment(task),
                      'launcher_context':launcher_contexts.get(key,[]),
                      'evaluation':record,'log':log,'recorded_session_summaries':[summary for summary in summaries.get(key[1],[])
                                     if summary['values'].get('checkpoint_id')==key[2]],
                      'training_run_id':run['run_id'] if run else None,'training_link':training_link})
    for key in evaluations:
        if key not in raw_tasks:issues.append({'kind':'orphan_evaluation','task_key':list(key)})
    linked_sessions={item['task']['session_id'] for item in tasks}
    unlinked=[]
    for session,values in logs.items():
        if session in linked_sessions:continue
        log=max(values,key=lambda item:(item['complete_client_log'],sum(item['events'].values())))
        unlinked.append({key:value for key,value in log.items() if not key.startswith('_') and key!='task'})
        issues.append({'kind':'unlinked_execution_session','session_id':session,'reason':'No unambiguous Task result; execution metrics retained separately, no physical trial inferred'})
    return {'tasks':tasks,'training_runs':runs,'evaluation_history':records,'issues':issues,'input_files':len(files),'unlinked_execution_logs':unlinked}
