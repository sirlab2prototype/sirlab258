"""Human-readable and structured reports, only into new separate directories."""
import csv
from datetime import datetime, timezone
import json

from ai.evaluate.records import STAGES, output_path


def cell(value):return ('unknown' if value is None else str(value)).replace('|','\\|').replace('\n',' ')


def rate_text(value):
    if value['rate'] is None:return f"unknown ({value['numerator']}/{value['denominator']})"
    lo,hi=value['wilson_95']
    return f"{value['rate']:.1%} ({value['numerator']}/{value['denominator']}; Wilson 95% {lo:.1%}–{hi:.1%})"


def markdown(result):
    lines=['# ACT Evaluation & Failure Analysis', '',f"Generated: {result['generated_at']}",
           f"Unique loaded Task attempts: {result['unique_attempts']}. Input files: {result['input_files']}.",'',
           '## Interpretation','',
           'Offline Action error, logged execution reliability, and operator-assessed physical outcomes are separate. '
           'COMPLETED, command ACK, Open and user completion are not physical-task labels. Unknown results remain unknown.', '',
           '108 is the successful Demonstration collection goal; the earlier ~60 unseen-pose evaluations are a separate candidate plan. '
           'Configured bins are planning candidates, not verified robot workspace constraints.', '',
           '## A. Offline policy error','',
           '| Training run | Status / role | Dataset | Train / Validation episodes | Joint MAE rad | Hold MAE rad | Gripper MAE / Accuracy / Hold Accuracy |',
           '|---|---|---|---|---:|---:|---|']
    for run_id,run in result['offline_training_runs'].items():
        evaluation=run['evaluation'];summary=(evaluation.get('metrics') or {}).get('summary',{})
        counts=(evaluation.get('accuracy_counts') or {}).get('summary',{})
        def accuracy(key):
            value=counts.get(key)
            return f"{value['rate']} ({value['numerator']}/{value['denominator']})" if value else 'unknown'
        lines.append('| '+' | '.join(cell(value) for value in (run_id,evaluation['status']+' / '+evaluation['role'],run['dataset_id'],run['split'],
            summary.get('joint_mae_rad','unknown'),summary.get('hold_joint_mae_rad','unknown'),
            f"{summary.get('gripper_mae','unknown')} / {accuracy('gripper_accuracy_at_0_5')} / {accuracy('hold_gripper_accuracy')}"))+' |')
        if evaluation.get('issues'):lines.append(f"\nBlocked/limited: {cell(evaluation['issues'])}\n")
    if not result['offline_training_runs']:lines+=['','No training manifest/held-out metrics available; no offline errors are inferred.']
    lines+=['','## B. Execution reliability and C. operator-assessed outcomes','']
    for environment,group in result['by_environment'].items():
        reliability=group['execution_reliability']
        lines += [f'### {environment}','',f"Attempts: {group['attempts']}; assessment validity: {group['validity_counts']}.",
                  f"Recorded Task states: {reliability['final_task_states']}. Timeouts: {len(reliability['timeout_task_run_ids'])} attempts.",
                  f"Logged safety-stop rate: {rate_text(reliability['logged_safety_stop_rate'])}. Unknown coverage: {len(reliability['logged_safety_stop_rate']['unknown_task_run_ids'])}.",'',
                  '| Operator outcome | Success rate | Excluded attempts |','|---|---|---|']
        for stage,metric in group['physical_outcomes'].items():lines.append(f"| {stage} | {rate_text(metric)} | {cell(metric['excluded'])} |")
        lines+=['','| Logged metric | Samples | Median | P95 | P99 | Scope |','|---|---:|---:|---:|---:|---|']
        for name,metric in reliability['metrics'].items():
            lines.append('| '+' | '.join(cell(v) for v in (name,metric['observed_samples'],metric['median'],metric['p95'],metric['p99'],metric['quantile_scope']))+' |')
        lines+=['',f"Observed signal counts (unique attempts, multiple labels allowed): {cell(reliability['signal_trial_counts'])}",
                f"Task duration seconds, all states: {cell(group['task_duration_seconds'])}",'',
                '| Condition axis | Condition | Attempts | Task | ACT | Home |','|---|---|---:|---|---|---|']
        for condition in group['conditions']:
            lines.append('| '+' | '.join(cell(value) for value in (condition['axis'],condition['value'],condition['attempts'],
                        *(rate_text(condition['physical_outcomes'][key]) for key in ('task','act','home'))))+' |')
    lines+=['','## Checkpoint comparison','',
            '| Checkpoint | Training run | Dataset | Successful demonstrations | Completed steps | ACT settings |','|---|---|---|---:|---:|---|']
    for item in result['checkpoint_comparison']['checkpoints']:
        lines.append('| '+' | '.join(cell(item[key]) for key in ('checkpoint_id','training_run_id','dataset_id','successful_demonstrations','completed_step','act_settings'))+' |')
        lines.append(f"\nTraining links: {cell(item['training_link_methods'])}. Per-environment rates, timing and conditions are in JSON.\n")
    lines+=['','| Checkpoint | Environment | Attempts | Task | ACT | Home | Logged safety stop | Inference ms median/P95/P99 |',
            '|---|---|---:|---|---|---|---|---|']
    for item in result['checkpoint_comparison']['checkpoints']:
        for env,group in item['by_environment'].items():
            reliability=group['execution_reliability'];timing=reliability['metrics'].get('inference_ms',{})
            values=(item['checkpoint_id'],env,group['attempts'],*(rate_text(group['physical_outcomes'][stage]) for stage in ('task','act','home')),
                    rate_text(reliability['logged_safety_stop_rate']),'/'.join(str(timing.get(key,'unknown')) for key in ('median','p95','p99')))
            lines.append('| '+' | '.join(cell(value) for value in values)+' |')
    lines+=['','No automatic ranking is performed.']
    for limitation in result['checkpoint_comparison']['limitations']:lines.append('- '+limitation)
    lines+=['','## Failure cases and provenance','', '| Task run | Environment | State | Failure stage (execution / operator) | Assessment | Signals | Sources |','|---|---|---|---|---|---|---|']
    for case in result['cases']:
        lines.append('| '+' | '.join(cell(value) for value in (case['task_run_id'],case['environment'],case['state'],
                            f"{case['execution_failure_stage']} / {case['operator_failure_stage']}",case['evaluation_id'] or 'unknown',
                            list(case['signals']),case['sources']))+' |')
    lines+=['','## Improvement candidates (hypotheses)','']
    if not result['recommendations']:lines.append('Insufficient evidence for a specific change; collect controlled assessments before choosing a cause.')
    for candidate in result['recommendations']:
        lines += [f"- **{candidate['kind']} / {candidate.get('environment','offline')}**: {candidate['candidate']}",
                  f"  Evidence: `{cell(json.dumps(candidate['evidence'],ensure_ascii=False))}`"]
        if candidate.get('scope'):lines.append('  '+candidate['scope'])
    lines+=['','## Integrity, denominators and uncertainty','']
    for key,value in result['policies'].items():lines.append(f'- {key}: {value}')
    if result['issues']:
        lines += ['','Input issues are retained, never silently repaired:']
        for issue in result['issues']:lines.append('- '+cell(json.dumps(issue,ensure_ascii=False)))
    lines+=['','Per-episode errors, binomial numerator/denominator and included Task IDs, revision history, per-case log evidence, '
            'and checkpoint/environment statistics are preserved in report.json. Original input files are unchanged.','']
    return '\n'.join(lines)


def write_report(result,destination):
    destination=output_path(destination)
    if destination.exists():raise ValueError('Report directory must be new; no original report is overwritten')
    destination.mkdir(parents=True,exist_ok=False)
    result={**result,'generated_at':datetime.now(timezone.utc).isoformat()}
    (destination/'report.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    (destination/'report.md').write_text(markdown(result),encoding='utf-8')
    fields=['task_run_id','session_id','checkpoint_id','environment','state','evaluation_id','validity',
            'region','yaw_deg','yaw_bin','depth_cm','depth_bin','depth_condition','training_coverage',*STAGES,'signals']
    with (destination/'cases.csv').open('x',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for case in result['cases']:
            row={key:case.get(key) for key in fields[:6]}
            row.update(case['conditions']);row.update(case['operator_physical_outcomes'])
            row['validity']=(case['evaluation'] or {}).get('validity','unassessed');row['signals']=','.join(case['signals'])
            writer.writerow({key:("'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value)
                             for key in fields if (value:=row.get(key)) is not None})
    return result
