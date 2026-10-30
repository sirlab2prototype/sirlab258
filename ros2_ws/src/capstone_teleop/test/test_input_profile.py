import numpy as np
import pytest

from capstone_teleop.input_profile import (
    MotionProfile, radial_stick
)

NEUTRAL = [0., 0., 1., 0., 0., 1., 0., 0.]

def make():
    return MotionProfile(
        30., 5., 0.3, 0.18, 2.,
        0.08, 2., 600., 1.5
    )

def test_radial_and_autoarm_neutral():
    p = make()
    assert np.allclose(
        radial_stick(1, 1, .18, 2),
        [2**-.5, 2**-.5],
    )
    assert p.neutral(NEUTRAL, [0]*12)
    diagonal = NEUTRAL.copy()
    diagonal[:2] = [.15, .15]
    assert not p.neutral(diagonal, [0]*12)

def test_smooth_turn_and_release():
    p = make()
    x = NEUTRAL.copy()
    x[0] = 1
    for _ in range(20):
        before, _ = p.update(x, 5.)
    y = NEUTRAL.copy()
    y[1] = 1
    after, _ = p.update(y, 5.)
    assert after[1] > 0 and after[0] > 0
    assert np.linalg.norm(after-before) <= 600/900 + 1e-9
    stopped, rotation = p.update(NEUTRAL, 5.)
    assert np.array_equal(stopped, np.zeros(3))
    assert np.array_equal(rotation, np.zeros(3))

def test_yaw_speed_scaling_and_release():
    p = make()
    axes = NEUTRAL.copy()
    axes[3] = 1
    for _ in range(30):
        _, rot = p.update(axes, 5.)
    assert rot[2] == pytest.approx(.3/30.)
    _, rot = p.update(axes, .25)
    assert abs(rot[2]) <= .3/30 * .25/5 + 1e-10
    _, rot = p.update(NEUTRAL, .25)
    assert rot[2] == 0

def test_xy_z_release_independently():
    p = make()
    axes = NEUTRAL.copy()
    axes[0] = 1
    axes[5] = -1
    for _ in range(20):
        p.update(axes, 5.)
    axes[0] = 0
    xyz, _ = p.update(axes, 5.)
    assert xyz[0] == 0 and xyz[1] == 0 and xyz[2] < 0
    axes[5] = 1
    xyz, _ = p.update(axes, 5.)
    assert np.array_equal(xyz, np.zeros(3))

def test_invalid_input_rejected():
    p = make()
    bad = NEUTRAL.copy()
    bad[1] = float("nan")
    with pytest.raises(ValueError):
        p.update(bad, 5.)
