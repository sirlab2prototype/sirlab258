"""Optional execution-prefix weighting; official ACT architecture/targets unchanged."""
import torch

def weighted_l1(prediction,target,padding,prefix,weight):
    valid=(~padding).unsqueeze(-1).to(prediction.dtype)
    weights=torch.ones_like(valid);weights[:,:prefix]*=weight
    denom=(valid*weights).sum()*prediction.shape[-1]
    if denom<=0:raise ValueError('No valid action targets')
    return ((prediction-target).abs()*valid*weights).sum()/denom

def physical_joint_weights(q10, q90):
    """QUANTILE10: physical delta = normalized delta * (q90-q10)/2."""
    spans=torch.as_tensor(q90,dtype=torch.float64)-torch.as_tensor(q10,dtype=torch.float64)
    if spans.shape!=(7,) or not torch.isfinite(spans).all() or not torch.all(spans[:6]>0):
        raise ValueError('Finite positive J1..J6 quantile spans are required')
    joints=spans[:6]/spans[:6].mean()
    return torch.cat((joints,torch.ones(1,dtype=joints.dtype))).float()

def balanced_weighted_l1(prediction,target,padding,prefix,weight,joint_weights):
    valid=(~padding).unsqueeze(-1).to(prediction.dtype)
    weights=torch.ones_like(valid);weights[:,:prefix]*=weight
    dimensions=joint_weights.to(device=prediction.device,dtype=prediction.dtype).view(1,1,-1)
    if dimensions.shape[-1]!=prediction.shape[-1]:raise ValueError('Action dimension/weight mismatch')
    denom=(valid*weights).sum()*dimensions.sum()
    if denom<=0:raise ValueError('No valid action targets')
    return ((prediction-target).abs()*valid*weights*dimensions).sum()/denom

def install(policy,weight,joint_weights=None):
    if weight==1 and joint_weights is None:return policy
    original=policy.forward
    def forward(batch,*args,**kwargs):
        captured=[]
        hook=policy.model.action_head.register_forward_hook(lambda module,args,out:captured.append(out))
        try:loss,parts=original(batch,*args,**kwargs)
        finally:hook.remove()
        pred=captured[-1];valid=(~batch['action_is_pad']).unsqueeze(-1)
        old=((pred-batch['action']).abs()*valid).mean()
        new=(weighted_l1(pred,batch['action'],batch['action_is_pad'],policy.config.n_action_steps,weight)
             if joint_weights is None else balanced_weighted_l1(pred,batch['action'],batch['action_is_pad'],
                                                                  policy.config.n_action_steps,weight,joint_weights))
        return loss-old+new,{**parts,'execution_prefix_l1':float(new.detach())}
    policy.forward=forward
    return policy
