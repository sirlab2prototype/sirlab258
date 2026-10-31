"""Offline ACT report CLI. No training, model inference or device initialization."""
import argparse
import json
from pathlib import Path
import sys

from ai.evaluate.analysis import analyze,load_conditions
from ai.evaluate.records import append_evaluation,judgment_template,output_path,read_json
from ai.evaluate.report import write_report
from ai.evaluate.sources import _task_candidates,load_sources

DEFAULT_CONDITIONS=Path(__file__).with_name('conditions.json')


def one_task(path):
    tasks=_task_candidates(read_json(path))
    if len(tasks)!=1:raise ValueError('Task input must contain exactly one Task result')
    return tasks[0]


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    action=argv.pop(0) if argv and argv[0] in ('template','record') else 'report'
    parser=argparse.ArgumentParser(description=__doc__)
    if action in ('template','record'):
        parser.add_argument('--task-result',type=Path,required=True)
        if action=='template':parser.add_argument('--output',type=Path)
        else:
            parser.add_argument('--judgment',type=Path,required=True)
            parser.add_argument('--store',type=Path,required=True)
            parser.add_argument('--supersedes')
        args=parser.parse_args(argv)
        task=one_task(args.task_result)
        if action=='template':
            text=json.dumps(judgment_template(task),indent=2,ensure_ascii=False)+'\n'
            if args.output:
                path=output_path(args.output)
                path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('x',encoding='utf-8') as stream:stream.write(text)
            print(text)
        else:print(append_evaluation(args.store,task,read_json(args.judgment),supersedes=args.supersedes,source_path=args.task_result))
        return 0
    parser.add_argument('--runs',nargs='+',type=Path,required=True)
    parser.add_argument('--records',nargs='*',type=Path,default=[])
    parser.add_argument('--report-dir',type=Path,required=True)
    parser.add_argument('--conditions',type=Path,default=DEFAULT_CONDITIONS)
    parser.add_argument('--checkpoint',action='append',default=[])
    parser.add_argument('--condition',action='append',default=[],help='Exact key=value filter on actual/binned conditions')
    args=parser.parse_args(argv)
    filters={}
    allowed={'region','yaw_deg','depth_cm','x_cm','y_cm','yaw_bin','depth_bin','depth_condition','training_coverage','evaluation_protocol_id','workspace_id','camera_setup_id','object_id'}
    for value in args.condition:
        if '=' not in value:parser.error('Condition must be key=value')
        key,value=value.split('=',1)
        if key not in allowed or key in filters:parser.error('Unknown or duplicate condition filter')
        filters[key]=value
    bundle=load_sources(args.runs,args.records)
    result=analyze(bundle,load_conditions(args.conditions),checkpoint_ids=args.checkpoint,condition_filters=filters)
    result=write_report(result,args.report_dir)
    print(f"Evaluation report: {args.report_dir}\nUnique attempts: {result['unique_attempts']}\nInput issues: {len(result['issues'])}")
    for env,group in result['by_environment'].items():
        metric=group['physical_outcomes']['task']
        print(f"{env}: operator Task success {metric['numerator']}/{metric['denominator']} (unknown when denominator=0)")
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError,KeyError,TypeError) as exc:raise SystemExit(f'Evaluation failed: {exc}')
