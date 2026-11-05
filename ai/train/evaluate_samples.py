"""Recorded-observation ACT diagnostics; no ROS/SDK; reusable across checkpoints."""
import argparse,json,time
from pathlib import Path
import numpy as np,torch
from torch.utils.data import DataLoader,Subset
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from ai.serve.lerobot_act_backend import LeRobotActBackend

def error_stats(x):
 x=np.abs(x)
 return {'mean_abs_deg':np.rad2deg(x.mean(0)).tolist(),'p95_abs_deg':np.rad2deg(np.percentile(x,95,axis=0)).tolist(),'max_abs_deg':np.rad2deg(x.max(0)).tolist(),'pooled_mean_abs_deg':float(np.rad2deg(x.mean())),'pooled_p95_abs_deg':float(np.rad2deg(np.percentile(x,95))),'pooled_max_abs_deg':float(np.rad2deg(x.max())),'observations_over1_5deg':int(np.any(x>np.deg2rad(1.5),axis=1).sum())}

def main():
 p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True);p.add_argument('--indices-from',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 torch.set_num_threads(4);ref=np.load(a.indices_from);indices=ref['frame_indices'];b=LeRobotActBackend(a.checkpoint,device='cuda');ds=LeRobotDataset('local/indy7_act',root=a.dataset,delta_timestamps={'action':[i/20 for i in range(40)]},video_backend='pyav')
 starts=np.array(ds.hf_dataset['frame_index'])[indices]==0;pred=[];truth=[];state=[];pad=[];images={};times=[]
 try:
  for physical in DataLoader(Subset(ds,indices.tolist()),batch_size=8,num_workers=0):
   truth.append(physical['action'].clone().cpu().numpy());state.append(physical['observation.state'].clone().cpu().numpy());pad.append(physical['action_is_pad'].cpu().numpy())
   if not images:images={k:{'shape':list(v.shape),'min':float(v.min()),'max':float(v.max())} for k,v in physical.items() if k.startswith('observation.images')}
   batch=b.preprocessor(physical);torch.cuda.synchronize();begin=time.perf_counter()
   with torch.inference_mode(),torch.autocast('cuda',enabled=b.policy.config.use_amp):x=b.policy.predict_action_chunk(batch)
   x=b.postprocessor(x);torch.cuda.synchronize();times.append(time.perf_counter()-begin);assert torch.isfinite(x).all();pred.append(x.float().cpu().numpy())
 finally:ds.finalize()
 pred=np.concatenate(pred);truth=np.concatenate(truth);state=np.concatenate(state);pad=np.concatenate(pad);assert np.array_equal(truth,ref['target']) and np.array_equal(state,ref['observation_state'])
 chunkerr=abs(pred[:,:,:6]-truth[:,:,:6]);valid=~pad;delta=np.diff(pred[:,:,:6],axis=1)
 report={'checkpoint':str(a.checkpoint.resolve()),'sample_count':len(indices),'image_contract':images,'first_prediction_vs_label':error_stats(pred[:,0,:6]-truth[:,0,:6]),'first_prediction_vs_state':error_stats(pred[:,0,:6]-state[:,:6]),'first_label_vs_state':error_stats(truth[:,0,:6]-state[:,:6]),'starts10_prediction_vs_label':error_stats(pred[starts,0,:6]-truth[starts,0,:6]),'starts10_prediction_vs_state':error_stats(pred[starts,0,:6]-state[starts,:6]),'horizons':{str(h):{'n':int(valid[:,h].sum()),**error_stats((pred[:,h,:6]-truth[:,h,:6])[valid[:,h]])} for h in (0,1,9,19,39)},'chunk_joint_mae_rad':float(chunkerr[valid].mean()),'gripper_mae':float(abs(pred[:,:,6]-truth[:,:,6])[valid].mean()),'finite':True,'shape':list(pred.shape),'processors':b.processor_verification,'max_internal_step_rad':float(abs(delta).max()),'max_nominal_velocity_rad_s':float(abs(delta).max()*20),'max_nominal_acceleration_rad_s2':float(abs(np.diff(pred[:,:,:6],n=2,axis=1)).max()*400),'mean_batch8_inference_s':float(np.mean(times)),'in_sample_not_generalization':True,'real_commands_sent':0}
 np.savez_compressed(a.output/'predictions.npz',action=pred,frame_indices=indices,observation_state=state,target=truth,action_is_pad=pad)
 (a.output/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
