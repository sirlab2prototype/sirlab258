"""Actual ACT + existing Scheduler/Controller + isolated Mock state feedback.

No ROS/SDK imports or transports. Recorded RGB is frozen; only Mock joint/gripper
state evolves. This is software feedback testing, not visual/physical simulation.
"""
import argparse,json,time,builtins
from pathlib import Path
from dataclasses import replace
import numpy as np
import torch
from ai.serve.lerobot_act_backend import LeRobotActBackend
from ai.execute.adapters import MockIndy7Adapter,MockMark7Transport
from ai.execute.mock_session import MockPlantSource,MockSession
from ai.execute.controller import ExecutionController
from ai.execute.authority import CommandLease
from ai.execute.safety import SafetyConfig
from ai.serve import dry_run
from lerobot.datasets.lerobot_dataset import LeRobotDataset

def main():
 p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=('model','normal','disconnect','nan'),default='model');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 original_import=builtins.__import__
 def no_hardware(name,*args,**kwargs):
  if name.startswith(('neuromeka','rclpy')):raise RuntimeError('hardware import forbidden in offline integration')
  return original_import(name,*args,**kwargs)
 builtins.__import__=no_hardware
 b=LeRobotActBackend(a.checkpoint,device='cuda');ds=LeRobotDataset('local/indy7_act',root=a.dataset,video_backend='pyav');first=ds[0];ds.finalize()
 images={k.split('.')[-1]:(first[k].numpy().transpose(1,2,0)*255).round().astype(np.uint8) for k in first if k.startswith('observation.images')}
 virtual=[1_000_000_000_000];originals={k:getattr(time,k) for k in ('monotonic_ns','monotonic','time_ns','sleep')}
 time.monotonic_ns=lambda:virtual[0];time.monotonic=lambda:virtual[0]/1e9;time.time_ns=lambda:1790000000000000000+virtual[0];time.sleep=lambda s:virtual.__setitem__(0,virtual[0]+round(s*1e9))
 logstream=(a.output/'events.jsonl').open('x');observations=[]
 def log(event,**kw):logstream.write(json.dumps({'event':event,**kw})+'\n')
 robot=MockIndy7Adapter(time.monotonic_ns);robot.joints=first['observation.state'][:6].numpy().astype(float);mark=MockMark7Transport(time.monotonic_ns,float(first['observation.state'][6]))
 class Source(MockPlantSource):
  def append(self,name,value,stamp,timing=None):
   if name in images:value=images[name]
   return super().append(name,value,stamp,timing)
 source=Source(robot,mark,None)
 # Unresolved physical values are explicit MOCK fixtures, never copied to Real profiles.
 cfg=replace(SafetyConfig.load('ai/execute/config/mock.json'),max_step_rad=[float(np.deg2rad(1.5))]*6,max_velocity_rad_s=[float(np.deg2rad(45))]*6)
 lease=CommandLease(a.output/'mock_lease.json');c=ExecutionController(robot,mark,lease,cfg,log,clock=time.monotonic_ns);c.target_update_mode='bounded_rate';session=MockSession(source,c,'offline software trial')
 model={'checkpoint_id':'offline-real-model','checkpoint':str(a.checkpoint),'chunk_size':40,'n_action_steps':10,'fps':20,'device':'cuda'}
 class RPC:
  def __init__(self,*args):self.pending=None;self.count=0
  def control(self,req):return ({**model,'session_generated_chunks':self.count},None,{})
  def submit(self,req,observation,chunk_size):
   observations.append(observation['observation.state'].copy().tolist());self.count+=1
   if a.mode in ('normal','disconnect','nan'):chunk=np.tile(observation['observation.state'],(40,1));chunk[:,:6]+=.0005
   else:
    chunk=b.predict_chunk(observation)
   if a.mode=='nan' and self.count>=2:chunk[:,:6]=np.nan
   self.pending=({**model,'session_id':req['session_id'],'request_id':req['request_id'],'observation_monotonic_ns':req['observation_monotonic_ns'],'inference_ns':1000000},chunk)
  def poll(self):
   if a.mode=='disconnect' and len(robot.commands)>=2:raise ConnectionError('controlled mock disconnect')
   item=self.pending;self.pending=None
   return None if item is None else (*item,{'client_received_monotonic_ns':time.monotonic_ns()})
  def close(self):pass
 oldrpc=dry_run.RPCClient;dry_run.RPCClient=RPC
 try:
  result=dry_run.run_session(source,'isolated in-process ACT',model,log,duration=2.,_mock_execution=session)
  result.update(transport='in-process mock RPC',action_source=('ACT checkpoint' if a.mode=='model' else 'explicit synthetic normal/fault fixture'),model_inference_calls=(len(observations) if a.mode=='model' else 0),frozen_recorded_RGB=True,physical_simulation=False,real_commands_sent=0,inference_count=len(observations),mock_commands=len(robot.commands),state_changed=bool(len(observations)>1 and not np.array_equal(observations[0],observations[-1])),observations=observations,lease_record=json.loads(lease.path.read_text()) if lease.path.exists() else None,mock_limits_not_real_approval=True)
  (a.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:result.get(k) for k in ('status','inference_count','mock_commands','state_changed','execution','lease_record')},indent=2))
 finally:
  c.close_watchdog();dry_run.RPCClient=oldrpc;logstream.close()
  for k,v in originals.items():setattr(time,k,v)
  builtins.__import__=original_import
if __name__=='__main__':main()
