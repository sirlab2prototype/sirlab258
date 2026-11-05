"""Private Real client. Only the explicit execute --mode real launcher selects it."""
import argparse
from functools import partial
from ai.execute.real_session import create_real_session
from ai.serve.dry_run import main as client_main


def main():
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--execution-config',required=True)
    parser.add_argument('--hardware-profile',required=True)
    parser.add_argument('--approve-real',required=True)
    args,forwarded=parser.parse_known_args()
    factory=partial(create_real_session,config_path=args.execution_config,hardware_path=args.hardware_profile,
                    approval_text=args.approve_real)
    return client_main(forwarded,_real_factory=factory)


if __name__=='__main__':raise SystemExit(main())
