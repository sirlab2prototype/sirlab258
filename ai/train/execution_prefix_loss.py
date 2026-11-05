"""Optional execution-prefix weighting; official ACT architecture/targets unchanged."""
import torch

def weighted_l1(prediction,target,padding,prefix,weight):
    valid=(~padding).unsqueeze(-1).to(prediction.dtype)
    weights=torch.ones_like(valid);weights[:,:prefix]*=weight
    denom=(valid*weights).sum()*prediction.shape[-1]
    if denom<=0:raise ValueError('No valid action targets')
    return ((prediction-target).abs()*valid*weights).sum()/denom

def install(policy,weight):
    if weight==1:return policy
    original=policy.forward
    def forward(batch,*args,**kwargs):
        captured=[]
        hook=policy.model.action_head.register_forward_hook(lambda module,args,out:captured.append(out))
        try:loss,parts=original(batch,*args,**kwargs)
        finally:hook.remove()
        pred=captured[-1];valid=(~batch['action_is_pad']).unsqueeze(-1)
        old=((pred-batch['action']).abs()*valid).mean()
        new=weighted_l1(pred,batch['action'],batch['action_is_pad'],policy.config.n_action_steps,weight)
        return loss-old+new,{**parts,'execution_prefix_l1':float(new.detach())}
    policy.forward=forward
    return policy
