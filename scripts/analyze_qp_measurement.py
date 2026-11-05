"""Offline extension of log_qp_readonly.analyze; no ROS/SDK imports."""
import json,hashlib
from pathlib import Path
import numpy as np
from log_qp_readonly import analyze

def details(path):
 rows=[json.loads(x) for x in path.read_text().splitlines()]
 states=[r for r in rows if r.get('topic')=='/joint_states'];cmds=[r for r in rows if r.get('topic')=='/capstone/teleop_action']
 if len(states)<3 or len(cmds)<2:raise ValueError('QP analysis requires at least three joint states and two action samples')
 base=analyze(path)
 if not base['valid_complete_log']:raise ValueError('QP analysis requires a complete log without drops or write errors')
 contexts=[]
 for r in rows:
  if r.get('topic')=='/capstone/action_context':
   try:contexts.append((r['received_ns']*1e-9,json.loads(r['data']['data'])['phase']))
   except (KeyError,ValueError):pass
 def phase(t):
  if not contexts:return np.zeros(len(t),bool)
  ts=np.array([x[0] for x in contexts]);k=np.searchsorted(ts,t,side='right')-1
  return np.array([i>=0 and contexts[i][1]=='QP_TELEOP' for i in k])
 def vectors(rs,key):return np.array([[r['data'][key][r['data']['name'].index('joint'+str(j))] for j in range(6)] for r in rs])
 def summary(a,mask):
  a=abs(a[mask]);return {'n':len(a),'quantiles':{str(p):np.percentile(a,p,axis=0).tolist() for p in (50,95,99,99.9,100)}} if len(a) else {'n':0}
 t=np.array([r['received_ns']*1e-9 for r in states]);t-=t[0];origin=states[0]['received_ns']*1e-9
 q=vectors(states,'position');v=vectors(states,'velocity');ct=np.array([r['received_ns']*1e-9-origin for r in cmds]);c=vectors(cmds,'position');dt=np.diff(t);qp=phase(t+origin);cqp=phase(ct+origin)
 good=(dt>=.025)&(dt<=.075);fd=np.diff(q,axis=0)/dt[:,None];av=np.diff(v,axis=0)/dt[:,None];tv=(t[1:]+t[:-1])/2;ap=np.diff(fd,axis=0)/np.diff(tv)[:,None]
 moving=np.max(abs(v),axis=1)>.005
 moving[1:]|=np.max(abs(fd),axis=1)>.005
 masks=good&qp[1:]&qp[:-1]& (moving[1:]|moving[:-1]);pc=good[1:]&good[:-1]&qp[2:]&qp[1:-1]&qp[:-2]&(moving[2:]|moving[1:-1]|moving[:-2])
 out={'base':base,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'sampling':{'dt_ms_quantiles':np.percentile(dt*1000,[0,50,95,99,100]).tolist(),'long_over75ms':int(sum(dt>.075)),'short_under25ms':int(sum(dt<.025)),'long_intervals':[[float(t[i]),float(t[i+1]),float(dt[i])] for i in np.where(dt>.075)[0]]},'units':'rad, rad/s, rad/s^2','acceleration':{'qdot_difference_all':summary(av,np.ones(len(av),bool)),'qdot_difference_clean_moving_QP':summary(av,masks),'position_second_difference_all':summary(ap,np.ones(len(ap),bool)),'position_second_difference_clean_moving_QP':summary(ap,pc)},'tracking':{}}
 for threshold in (.002,.005,.01):
  mm=(np.max(abs(v),axis=1)>threshold);mm[1:]|=np.max(abs(fd),axis=1)>threshold
  k=np.searchsorted(ct,t,side='right')-1;safe=np.maximum(k,0);age=t-ct[safe];err=q-c[safe]
  for cap in (.025,.05,.075,.1):
   mask=(k>=0)&(age>=0)&(age<=cap)&qp&cqp[safe]&mm
   # Reject feedback on either side of a large gap.
   mask[1:]&=good;mask[:-1]&=good
   out['tracking'][f'moving_{threshold}_age_{cap}']=summary(err,mask)
 out['sample_counts']={'feedback':len(t),'commands':len(ct),'moving_QP':int(sum(moving&qp)),'clean_acceleration_intervals':int(sum(masks))}
 # Position-derived velocities at ~50ms are less noisy than raw qdot differentiation;
 # command-target differences are a separate control quantity.
 cd=np.diff(ct);cv=np.diff(c,axis=0)/cd[:,None];ca=np.diff(cv,axis=0)/((cd[1:]+cd[:-1])/2)[:,None]
 cg=(cd>.015)&(cd<.075)&cqp[1:]&cqp[:-1]
 out['command_acceleration_QP']=summary(ca,cg[1:]&cg[:-1])
 grid=np.arange(ct[0],ct[-1],.05);gk=np.searchsorted(ct,grid,side='right')-1;gk=np.maximum(gk,0)
 gc=c[gk];gvalid=(grid-ct[gk]<=.05)&cqp[gk]&phase(grid+origin)
 ga=np.diff(gc,n=2,axis=0)/.05**2
 out['QP_targets_subsampled_20Hz_not_ACT']=summary(ga,gvalid[2:]&gvalid[1:-1]&gvalid[:-2])
 out['command_interval_ms_quantiles']=np.percentile(cd*1000,[0,50,95,99,100]).tolist()
 out['candidate_limits_not_approved']={'acceleration_rad_s2':[4,3,3,5,3,5],
  'acceleration_basis':'ceil of larger clean moving qdot/position-derived max to next integer: replay-envelope diagnostic only, not safety margin proof',
  'tracking_error_rad':[.05,.05,.05,.08,.05,.08],
  'tracking_basis':'clean moving QP with command age<=50ms p99 rounded up to 0.01rad; not full-tail or safety coverage'}
 k=np.searchsorted(ct,t,side='right')-1;safe=np.maximum(k,0);age=t-ct[safe]
 valid=(k>=0)&(age>=0)&(age<=.05)&qp&cqp[safe]&moving;valid[1:]&=good;valid[:-1]&=good
 exceed=abs(q-c[safe])>np.array(out['candidate_limits_not_approved']['tracking_error_rad'])
 maxruns=[]
 for j in range(6):
  start=None;longest=0.;runs=0
  for i in range(len(t)):
   if valid[i] and exceed[i,j]:
    if start is None:start=t[i];runs+=1
    longest=max(longest,t[i]-start)
   else:start=None
  maxruns.append({'axis':j+1,'samples_exceeding':int(sum(exceed[:,j]&valid)), 'runs':runs,'max_observed_consecutive_duration_ms':longest*1000})
 out['candidate_tracking_exceedances']=maxruns
 out['timing_limitations']=['receipt time is not SDK send or sensor timestamp','action header is after SDK return; already executing commands may not yet have arrived','QP_TELEOP motion mask uses max |qdot| or interval |dq/dt|; Home/non-QP excluded','25..75ms filter is diagnostic, not a safety limit','finite differences attenuate missing peaks and amplify short-interval/encoder noise','no ACT predicted trajectory, no ACT physical replay; QP30Hz targets differ from ACT20Hz']
 # Compare header intervals and transport receipt jitter without pretending sensor time.
 def header(r):
  h=r['data']['header']['stamp'];return h['sec']+h['nanosec']*1e-9
 ht=np.array([header(r) for r in states]);ch=np.array([header(r) for r in cmds]);hd=np.diff(ht)
 out['header_dt_ms_quantiles']=np.percentile(hd*1000,[0,50,95,99,100]).tolist()
 k=np.searchsorted(ch,ht,side='right')-1;safe=np.maximum(k,0);age=ht-ch[safe]
 for cap in (.05,.075,.1):
  mask=(k>=0)&(age>=0)&(age<=cap)&qp&cqp[safe]&moving
  mask[1:]&=good;mask[:-1]&=good
  out['tracking'][f'header_aligned_age_{cap}']=summary(q-c[safe],mask)
 return out

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 result=details(a.input)
 with a.output.open('x') as f:json.dump(result,f,indent=2)
 print(json.dumps({k:result[k] for k in ('sampling','sample_counts','acceleration','tracking','command_acceleration_QP')},indent=2))
