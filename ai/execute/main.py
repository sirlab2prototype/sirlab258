"""Explicit Mock execution command. Real hardware activation is not exposed."""
import argparse
from pathlib import Path
from ai.serve.infer import main as inference_main
from ai.execute.safety import SafetyConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "real"), required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config/mock.json")
    parser.add_argument("--approve-mock", help="Explicit acknowledgement/operator label for MOCK arming and start")
    parser.add_argument("--mock-initial-joints",type=float,nargs=6,metavar="RAD",
                        help="Explicit six-joint software fixture (rad); default zeros. Never moves hardware.")
    args, forwarded = parser.parse_known_args(argv)
    if args.mode != "mock":
        parser.error("Real execution CLI is locked: independent mode/servo/exclusive-control evidence and hardware validation are not available")
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
