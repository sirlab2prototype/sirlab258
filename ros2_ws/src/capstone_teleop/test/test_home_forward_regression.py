"""Pure offline regression: no ROS/SDK import or connection."""
import ast,math,time
from pathlib import Path
from types import SimpleNamespace as M
import numpy as np
from capstone_teleop.input_profile import MotionProfile

def profile():return MotionProfile(30,5,.3,.18,2,.08,2,600,1.5)

def test_only_forward_input_never_generates_Z_or_rotation():
 p=profile()
 for _ in range(100):
  xyz,rot=p.update([0,1,1,0,0,1,0,0],5)
  assert xyz[0]>0 and np.array_equal(xyz[1:],[0,0])
  assert np.array_equal(rot,[0,0,0])

def test_home_rearm_rebases_on_fresh_joint_and_resets_profile():
 path=Path(__file__).resolve().parents[1]/'capstone_teleop/qp_teleop.py'
 cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='QPTeleop')
 method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='joint_callback')
 scope={'math':math,'np':np,'time':time}
 exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Fake',bases=[],keywords=[],body=[method],decorator_list=[])],type_ignores=[])),str(path),'exec'),scope)
 n=scope['Fake']();n.arming=True;n.waiting_for_fresh_joint=True;n.relative_joint=np.ones(6)*10;n.motion_profile=profile()
 n.motion_profile.xyz[:]=[1,2,3];n.recording_phase=lambda *a:None;n.get_logger=lambda:M(warning=lambda *a:None)
 q=[1,40,-130,90,2,90];n.joint_callback(M(name=['joint'+str(i) for i in range(6)],position=np.deg2rad(q)))
 assert np.allclose(n.origin_joint,q) and np.array_equal(n.relative_joint,np.zeros(6))
 assert n.armed and not n.arming and not n.waiting_for_fresh_joint
 assert np.array_equal(n.motion_profile.xyz,np.zeros(3))
