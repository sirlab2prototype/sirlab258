"""Fail-closed CLI tests with fake commands; never contact hardware."""
from pathlib import Path
import os,subprocess
import pytest
ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('scenario,code,text',[('ready',3,'SAFETY LOCK'),('ssh_failure',1,'remote state unknown'),('busy',1,'existing robot control'),('no_camera',1,'D405 driver not confirmed'),('container_down',1,'container is unavailable')])
def test_start_never_bypasses_unverified_stop(tmp_path,scenario,code,text):
    fake=tmp_path/'bin';fake.mkdir();log=tmp_path/'calls'
    for name in ('docker','ssh','python3'):
        p=fake/name
        p.write_text('''#!/usr/bin/env bash
printf '%s\n' "$0 $*" >> "$CALL_LOG"
case "${0##*/}:$1" in
 docker:inspect) [[ "$SCENARIO" == container_down ]] && echo false || echo true ;;
 docker:top) [[ "$SCENARIO" == no_camera ]] || echo '335122271033 /realsense2_camera_node' ;;
 ssh:*) [[ "$SCENARIO" == ssh_failure ]] && exit 255; [[ "$SCENARIO" == busy ]] && echo '/controller_manager/ros2_control_node'; exit 0 ;;
esac
exit 0
''');p.chmod(0o755)
    env=os.environ.copy();env.update(PATH=str(fake)+':'+env['PATH'],CALL_LOG=str(log),SCENARIO=scenario)
    result=subprocess.run([str(ROOT/'scripts/bin/omy')],env=env,text=True,capture_output=True)
    assert result.returncode==code,result.stderr
    assert text in result.stderr
    calls=log.read_text()
    assert 'ros2 launch open_manipulator_bringup' not in calls
    assert not any(value in calls for value in ('docker exec','docker stop','docker restart','pkill','killpg'))
    assert 'return: can only' not in result.stderr
