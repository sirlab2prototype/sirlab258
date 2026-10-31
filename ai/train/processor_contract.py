"""Require real saved normalization state; empty processors silently pass through."""
import json
from pathlib import Path
import torch
from lerobot.processor import NormalizerProcessorStep, UnnormalizerProcessorStep


def check_processor_files(directory):
    root = Path(directory).resolve()
    for filename, registry in (("policy_preprocessor.json", "normalizer_processor"),
                               ("policy_postprocessor.json", "unnormalizer_processor")):
        document = json.loads((root / filename).read_text())
        steps = [step for step in document["steps"] if step.get("registry_name") == registry]
        if len(steps) != 1 or not steps[0].get("state_file"):
            raise ValueError(f"Missing saved normalization statistics: {filename}; retraining required")
        path = (root / steps[0]["state_file"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Missing/invalid normalization state file: {filename}")


def verify_processors(preprocessor, postprocessor):
    pre = [step for step in preprocessor.steps if isinstance(step, NormalizerProcessorStep)]
    post = [step for step in postprocessor.steps if isinstance(step, UnnormalizerProcessorStep)]
    if len(pre) != 1 or len(post) != 1: raise ValueError("Expected ACT normalizer and unnormalizer")
    for step, keys in ((pre[0], ("observation.state.mean", "observation.state.std", "action.q10", "action.q90")),
                       (post[0], ("action.q10", "action.q90"))):
        state = step.state_dict()
        for key in keys:
            tensor = state.get(key)
            if tensor is None or tensor.shape != (7,) or not torch.isfinite(tensor).all():
                raise ValueError(f"Missing/invalid normalization statistics: {key}")
        if torch.any(state["action.q90"] < state["action.q10"]): raise ValueError("Invalid action quantiles")
    state = post[0].state_dict()
    with torch.inference_mode():
        output = postprocessor(torch.zeros((1, 1, 7)))
        expected = (state["action.q10"] + state["action.q90"]) / 2
        if not torch.allclose(output.cpu()[0, 0], expected.cpu(), atol=1e-6, rtol=1e-5):
            raise ValueError("Saved postprocessor does not unnormalize known values")
    return {"normalization_state_verified": True, "known_value_unnormalization_verified": True}
