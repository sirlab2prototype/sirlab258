"""Two-camera Indy7 ACT adapter using the shared official policy/processors."""
from ai import dataset_contract as contract
from ai.serve.lerobot_act_backend import LeRobotActBackend
from ai.serve.protocol import validate_observation, validate_chunk
from ai.train.checkpoint import assert_policy_contract


class RGBActBackend(LeRobotActBackend):
    def __init__(self, pretrained_model_dir, dataset_repo_id=None, dataset_root=None, device="cuda", task=""):
        super().__init__(pretrained_model_dir, dataset_repo_id, dataset_root, device, task)
        assert_policy_contract(self.policy.config)

    def predict_chunk(self, observation=None, *, state=None, images=None):
        if observation is None:
            observation = {"observation.state": state, **(images or {})}
        validate_observation(observation)
        chunk = super().predict_chunk(observation)
        return validate_chunk(chunk, self.policy.config.chunk_size)
