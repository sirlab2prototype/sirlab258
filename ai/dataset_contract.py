"""Shared Indy7 ACT data contract. No ROS, model, or Dataset initialization."""

FPS = 20
CAMERA_NAMES = ("camera_317222074298", "camera_844212071939")
CAMERA_KEYS = tuple(f"observation.images.{name}" for name in CAMERA_NAMES)
IMAGE_SHAPE = (480, 640, 3)
JOINT_NAMES = tuple(f"joint{i}" for i in range(6))
VECTOR_NAMES = (*JOINT_NAMES, "gripper")
STATE_DIM = ACTION_DIM = 7
GRIPPER_INDEX = 6
SUCCESS_ROOT = "datasets/indy7_act"
FAILURE_ROOT = "datasets/indy7_act_failures"
SUCCESS_REPO_ID = "local/indy7_act"
FAILURE_REPO_ID = "local/indy7_act_failures"
TIMING_DIRECTORY = "meta/recorder_timing"
JOINT_STATE_SEMANTICS = "measured joint position, radians"
JOINT_ACTION_SEMANTICS = "absolute target joint position, radians"
GRIPPER_SEMANTICS = "successful command state, open=0 / grasp=1; not physical feedback"


def dataset_features(image_shape=IMAGE_SHAPE):
    """Return fresh mutable dictionaries matching existing Recorder datasets."""
    return {
        **{key: {"dtype": "video", "shape": tuple(image_shape),
                 "names": ["height", "width", "channels"]} for key in CAMERA_KEYS},
        **{key: {"dtype": "float32", "shape": (7,), "names": list(VECTOR_NAMES)}
           for key in ("observation.state", "action")},
    }
