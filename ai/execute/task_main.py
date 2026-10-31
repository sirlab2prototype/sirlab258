"""Explicit Task 1 launcher. Only Mock execution is exposed."""
import argparse
from pathlib import Path
from ai.execute.safety import SafetyConfig
from ai.execute.task_config import TaskConfig, TASK_NAME
from ai.serve.infer import main as inference_main


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=(TASK_NAME,), required=True)
    parser.add_argument("--mode", choices=("mock", "real"), required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config/mock.json")
    parser.add_argument("--task-config", type=Path, default=Path(__file__).parent / "config/task1.json")
    parser.add_argument("--approve-mock", help="Operator acknowledgement authorizing Mock ACT start")
    parser.add_argument("--approve-home-mock", action="store_true", help="Explicitly authorize subsequent Mock Home")
    parser.add_argument("--confirm-home-path-mock", help="Mock path safety evidence; not hardware validation")
    parser.add_argument("--mock-complete-after-actions", type=int, help="MOCK TEST ONLY: emulate user completion after N actions")
    args, forwarded = parser.parse_known_args(argv)
    if args.mode != "mock": parser.error("Real Task/Home execution is locked; official driver lacks a verified project-Home interface")
    if not args.approve_mock or not args.approve_mock.strip(): parser.error("--approve-mock is required")
    if not args.approve_home_mock or not args.confirm_home_path_mock or not args.confirm_home_path_mock.strip():
        parser.error("--approve-home-mock and --confirm-home-path-mock are required")
    if args.mock_complete_after_actions is not None and args.mock_complete_after_actions < 1: parser.error("Completion count must be positive")
    SafetyConfig.load(args.config)
    config = TaskConfig.load(args.task_config)
    options = {"config": args.config.expanduser().resolve(), "approval": args.approve_mock,
               "task_config": args.task_config.expanduser().resolve(), "path_evidence": args.confirm_home_path_mock,
               "complete_after_actions": args.mock_complete_after_actions}
    return inference_main(["--checkpoint", args.checkpoint, "--source", "synthetic", "--dry-run",
                           "--duration", str(config.max_act_seconds + config.prepare_timeout_seconds + 1), *forwarded],
                          _mock_options=options)


if __name__ == "__main__":
    raise SystemExit(main())
