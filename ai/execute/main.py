"""Explicit guarded Mock/Real execution. Hardware is disabled without reviewed profiles."""
import argparse
from pathlib import Path
from ai.serve.infer import main as inference_main
from ai.execute.safety import SafetyConfig

PROJECT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "real"))
    parser.add_argument("--checkpoint")
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config/mock.json")
    parser.add_argument("--approve-mock", help="Explicit acknowledgement/operator label for MOCK arming and start")
    parser.add_argument("--mock-initial-joints",type=float,nargs=6,metavar="RAD",
                        help="Explicit six-joint software fixture (rad); default zeros. Never moves hardware.")
    parser.add_argument("--hardware-profile",type=Path)
    parser.add_argument("--preflight-only",action="store_true",help="Validate Real profiles offline; no SDK, ROS or model loading")
    parser.add_argument("--approve-real", help="Onsite operator: controlled clear area, physical E-stop access, all other writers/Conty off; direct-link mode additionally requires physically isolated PC-to-controller cable. Live checks remain mandatory")
    parser.add_argument("--stop-run",type=Path)
    parser.add_argument("--status-run",type=Path)
    args, forwarded = parser.parse_known_args(argv)
    if args.preflight_only:
        if not args.hardware_profile or args.mode != 'real' or forwarded or args.stop_run or args.status_run:
            parser.error('--preflight-only requires --mode real --config PATH --hardware-profile PATH; no forwarded options')
        import json
        from ai.execute.preflight import check
        result=check(args.config,args.hardware_profile,args.checkpoint)
        print(json.dumps(result,indent=2))
        return 0 if result['profile_valid'] else 2
    if args.stop_run or args.status_run:
        if args.stop_run and args.status_run:parser.error("Choose stop or status")
        path=(args.stop_run or args.status_run).expanduser().resolve()/"client"
        if not path.is_relative_to(PROJECT):parser.error("Run must be inside this project")
        if not (path/"real_activation.json").is_file():parser.error("Not a Real run directory")
        if args.stop_run:
            (path/"STOP").touch(exist_ok=True)
            print("Stop requested; check execution_status.json / summary.json for feedback-confirmed stop.")
        else:
            status=path/"execution_status.json"
            print(status.read_text() if status.is_file() else "Real session not armed yet; inspect client.log")
        return 0
    if not args.mode or not args.checkpoint:parser.error("--mode and --checkpoint are required")
    if args.mode == "real":
        if args.approve_mock or args.mock_initial_joints is not None:parser.error("Mock flags are not allowed in Real mode")
        from ai.execute.real_profile import HardwareProfile
        if not args.approve_real or not args.approve_real.strip() or not args.hardware_profile:
            parser.error("Real execution locked: --approve-real and --hardware-profile required")
        if any(x.split("=",1)[0] in ("--dry-run","--source","--operator-initial-gripper","--ros-observation-recovery") for x in forwarded):
            parser.error("Real source/approval cannot be overridden with Dry Run options")
        try:
            config=SafetyConfig.load(args.config)
            profile=HardwareProfile.load(args.hardware_profile,config)
        except (ValueError,RuntimeError,OSError,TypeError,KeyError) as exc:
            parser.error("Real execution locked: "+str(exc))
        if profile.values.get('authority_mode')=='supervised_direct_link':
            trial=argparse.ArgumentParser(add_help=False)
            trial.add_argument('--duration',type=float,default=5.)
            limit,remaining=trial.parse_known_args(forwarded)
            if not 0<limit.duration<=5:parser.error('Supervised direct-link trial is limited to 0 < duration <= 5 seconds')
            forwarded=[*remaining,'--duration',str(limit.duration)]
        return inference_main(["--checkpoint",args.checkpoint,"--source","ros","--dry-run",*forwarded],
            _real_options={"config":args.config.resolve(),"hardware":args.hardware_profile.resolve(),
                           "approval":args.approve_real,"stop_timeout_s":config.stop_timeout_ms/1000})
    if not args.approve_mock or not args.approve_mock.strip(): parser.error("--approve-mock is required; startup never auto-arms")
    config=SafetyConfig.load(args.config)
    if args.mock_initial_joints is not None:
        from ai.execute.mock_session import validate_mock_initial_joints
        validate_mock_initial_joints(args.mock_initial_joints,config)
    return inference_main(["--checkpoint", args.checkpoint, "--source", "synthetic", "--dry-run", *forwarded],
                          _mock_options={"config": args.config.expanduser().resolve(), "approval": args.approve_mock,
                                         "mock_initial_joints":args.mock_initial_joints})


if __name__ == "__main__":
    raise SystemExit(main())
