"""Bounded fixed-batch ACT diagnostic using existing loss/processors, never hardware."""
import argparse,json,time,shutil
from pathlib import Path
import numpy as np
import torch
from torch.utils.data import DataLoader,Subset
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from ai.serve.lerobot_act_backend import LeRobotActBackend

def main():
 p=argparse.ArgumentParser();p.add_argument('--checkpoint',required=True);p.add_argument('--dataset',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--steps',type=int,default=400);p.add_argument('--samples',type=int,choices=(1,8),default=8);p.add_argument('--execution-prefix-weight',type=float,default=1.);a=p.parse_args()
 a.output.mkdir(exist_ok=False,parents=True);torch.set_num_threads(4);torch.manual_seed(0);np.random.seed(0)
 (a.output/'diagnostic_only.json').write_text(json.dumps({'purpose':'fixed-batch fit diagnostic, not full-Dataset training or a Real approval','source':a.checkpoint,'samples':a.samples}))
 b=LeRobotActBackend(Path(a.checkpoint),device='cuda');ds=LeRobotDataset('local/indy7_act',root=Path(a.dataset),delta_timestamps={'action':[i/20 for i in range(40)]},video_backend='pyav')
 ep=np.array(ds.hf_dataset['episode_index']);frame=np.array(ds.hf_dataset['frame_index']);idx=[int(np.where((ep==i)&(frame==0))[0][0]) for i in range(4)]+[int(np.where(ep==i)[0][len(np.where(ep==i)[0])//2]) for i in range(4)]
 idx=idx[:a.samples]
 physical=next(iter(DataLoader(Subset(ds,idx),batch_size=8,num_workers=0)));target=physical['action'].clone().cpu();batch=b.preprocessor(physical);ds.finalize()
 groups={'backbone':'model.backbone.conv1.weight','encoder':'model.encoder.layers.0.self_attn.in_proj_weight','head':'model.action_head.weight'}
 from ai.train.execution_prefix_loss import install
 install(b.policy,a.execution_prefix_weight)
 params=dict(b.policy.named_parameters());snap={k:params[n].detach().clone() for k,n in groups.items()};opt=torch.optim.AdamW(b.policy.parameters(),lr=1e-4,weight_decay=1e-4);log=[]
 def metric():
  with torch.inference_mode():
   pred=b.postprocessor(b.policy.predict_action_chunk(batch));err=abs(pred[:,:,:6]-target[:,:,:6]);return {'first_mae_deg':float(torch.rad2deg(err[:,0]).mean()),'chunk_mae_deg':float(torch.rad2deg(err).mean()),'start4_max_first_deg':float(torch.rad2deg(err[:4,0]).max())}
 log.append({'step':0,**metric()});start=time.monotonic()
 for step in range(1,a.steps+1):
  b.policy.train();opt.zero_grad(set_to_none=True);loss,parts=b.policy(batch);loss.backward()
  if step==1:
   grad={k:float(params[n].grad.norm()) if params[n].grad is not None else None for k,n in groups.items()}
  torch.nn.utils.clip_grad_norm_(b.policy.parameters(),10);opt.step()
  if step%50==0:
   row={'step':step,'loss':float(loss.detach()),**parts,**metric()};log.append(row);print(json.dumps(row),flush=True)
   (a.output/'progress.json').write_text(json.dumps(log,indent=2))
 b.policy.save_pretrained(a.output/'pretrained_model')
 for f in b.pretrained_model_dir.iterdir():
  if f.name not in ('model.safetensors','config.json') and f.is_file():shutil.copy2(f,a.output/'pretrained_model'/f.name)
 result={'indices':idx,'checkpoint':a.checkpoint,'steps':a.steps,'lr':1e-4,'execution_prefix_weight':a.execution_prefix_weight,'amp':False,'gradient_norm_first_step':grad,'parameter_max_abs_change':{k:float(abs(params[n].detach()-snap[k]).max()) for k,n in groups.items()},'history':log,'elapsed_s':time.monotonic()-start}
 (a.output/'pretrained_model'/'diagnostic_only.json').write_text((a.output/'diagnostic_only.json').read_text())
 (a.output/'result.json').write_text(json.dumps(result,indent=2))
if __name__=='__main__':main()
