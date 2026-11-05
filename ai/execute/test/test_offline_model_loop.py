"""Offline diagnostic CLI contract with synthetic data and no model or devices."""
import json
import sys

import numpy as np
import torch

from ai.execute import offline_model_loop as loop


def test_normal_fixture_uses_recorded_rgb_contract_and_mock_only(tmp_path, monkeypatch):
    camera_names=('camera_317222074298','camera_844212071939')

    class Dataset:
        def __init__(self, *args, **kwargs):pass
        def __getitem__(self, index):
            assert index==0
            return {'observation.state':torch.zeros(7),
                    **{f'observation.images.{name}':torch.ones((3,2,2)) for name in camera_names}}
        def finalize(self):pass

    class Backend:
        def __init__(self, path, device):
            assert device=='cuda'

    actual_run_session=loop.dry_run.run_session
    def run_session(source, endpoint, model, log, *, duration, _mock_execution):
        assert duration==2 and _mock_execution.is_mock
        for name in camera_names:
            source.append(name,np.zeros((2,2,3),dtype=np.uint8),None)
        _,history=source.snapshot()
        for name in camera_names:
            assert np.array_equal(history['items'][name][-1][0],np.full((2,2,3),255,dtype=np.uint8))
        return actual_run_session(source,endpoint,model,log,duration=duration,
                                  _mock_execution=_mock_execution)

    monkeypatch.setattr(loop,'LeRobotDataset',Dataset)
    monkeypatch.setattr(loop,'LeRobotActBackend',Backend)
    monkeypatch.setattr(loop.dry_run,'run_session',run_session)
    output=tmp_path/'offline'
    monkeypatch.setattr(sys,'argv',['offline_model_loop','--checkpoint',str(tmp_path/'fake'),
                                    '--dataset',str(tmp_path/'fake_dataset'),'--output',str(output),
                                    '--mode','normal'])
    loop.main()
    result=json.loads((output/'result.json').read_text())
    assert result['real_commands_sent']==0 and result['frozen_recorded_RGB'] is True
    assert result['physical_simulation'] is False and result['transport']=='in-process mock RPC'
    assert result['status']=='complete' and result['mock_commands']>0
