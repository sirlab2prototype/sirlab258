"""Private Mock-only client entry. Existing infer --dry-run never selects this."""
import argparse
from functools import partial
from ai.execute.mock_session import create_mock_session
from ai.serve.dry_run import main as client_main


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--execution-config", required=True)
    parser.add_argument("--approve-mock", required=True)
    parser.add_argument("--task-config")
    parser.add_argument("--home-path-evidence")
    parser.add_argument("--mock-complete-after-actions", type=int)
    parser.add_argument("--mock-initial-joints",type=float,nargs=6)
    args, client_args = parser.parse_known_args()
    if not args.approve_mock.strip(): parser.error("Explicit Mock arm approval required")
    factory = partial(create_mock_session, config_path=args.execution_config, approval_text=args.approve_mock,
                      mock_initial_joints=args.mock_initial_joints)
    if args.task_config:
        if args.mock_initial_joints is not None:parser.error("Task Mock initial pose is the configured project Home")
        from ai.execute.task_session import create_task_session
        if not args.home_path_evidence or not args.home_path_evidence.strip(): parser.error("Mock Home path evidence required")
        factory = partial(create_task_session, config_path=args.execution_config, approval_text=args.approve_mock,
                          task_config_path=args.task_config, path_evidence=args.home_path_evidence,
                          complete_after_actions=args.mock_complete_after_actions)
    return client_main(client_args, _mock_factory=factory)


if __name__ == "__main__":
    raise SystemExit(main())
