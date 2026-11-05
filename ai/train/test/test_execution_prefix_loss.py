import torch
from ai.train.execution_prefix_loss import weighted_l1

def test_weights_change_loss_not_targets_and_mask_padding():
 p=torch.tensor([[[2.],[1.],[100.]]],requires_grad=True);t=torch.zeros_like(p);mask=torch.tensor([[False,False,True]])
 before=t.clone();loss=weighted_l1(p,t,mask,1,4)
 assert torch.isclose(loss,torch.tensor(1.8))
 loss.backward();assert p.grad[0,0]>p.grad[0,1] and p.grad[0,2]==0
 assert torch.equal(t,before)


def test_wrapper_preserves_kl_and_does_not_rewrite_targets():
    from types import SimpleNamespace as M
    from ai.train.execution_prefix_loss import install
    class FakePolicy(torch.nn.Module):
        def __init__(self):
            super().__init__();self.model=torch.nn.Module();self.model.action_head=torch.nn.Identity();self.config=M(n_action_steps=1)
        def forward(self,batch):
            pred=self.model.action_head(batch['pred']);l1=((pred-batch['action']).abs()*(~batch['action_is_pad']).unsqueeze(-1)).mean()
            return l1+2,{'l1_loss':float(l1.detach()),'kld_loss':2}
    policy=install(FakePolicy(),4);batch={'pred':torch.tensor([[[2.],[1.],[100.]]],requires_grad=True),'action':torch.zeros((1,3,1)),'action_is_pad':torch.tensor([[False,False,True]])}
    loss,parts=policy(batch);assert torch.isclose(loss,torch.tensor(3.8));assert parts['kld_loss']==2
    loss.backward();assert batch['pred'].grad[0,2]==0 and torch.equal(batch['action'],torch.zeros_like(batch['action']))


def test_new_training_options_keep_default_and_resume_strict():
    import pytest
    from ai.train.run_train import parse_args,resolve_settings
    assert resolve_settings(parse_args([]))['execution_prefix_weight']==1
    previous=resolve_settings(parse_args([]));previous.pop('execution_prefix_weight')
    assert resolve_settings(parse_args([]),previous)['execution_prefix_weight']==1
    with pytest.raises(ValueError):resolve_settings(parse_args(['--execution-prefix-weight','4']),previous)
    with pytest.raises(ValueError):resolve_settings(parse_args(['--execution-prefix-weight','nan']))


def test_default_weight_leaves_policy_identity_and_forward_unchanged():
    from ai.train.execution_prefix_loss import install
    policy=torch.nn.Linear(1,1);forward=policy.forward
    assert install(policy,1) is policy and policy.forward==forward


def test_resume_and_weights_only_initialization_are_mutually_exclusive():
    import pytest
    from ai.train.run_train import main
    with pytest.raises(ValueError,match='Choose resume'):
        main(['--resume','old','--init-checkpoint','old'])


def test_default_training_command_keeps_original_policy_path(tmp_path,monkeypatch):
    from ai.train import run_train
    args=run_train.parse_args(['--dataset-dir',str(tmp_path),'--dataset-repo-id','local/fake'])
    settings=run_train.resolve_settings(args)
    cmd=run_train.command(args,settings,{'train':[0]},tmp_path/'training')
    assert '--policy.pretrained_path' not in cmd
    args.init_checkpoint=tmp_path/'source'
    monkeypatch.setattr(run_train,'check_files',lambda path:path)
    initialized=run_train.command(args,settings,{'train':[0]},tmp_path/'training')
    assert initialized[initialized.index('--policy.pretrained_path')+1]==str(args.init_checkpoint)
