import numpy as np

from capstone_teleop.qp_core import (
    JOINT_MAX_DEG,
    JOINT_MIN_DEG,
    jacobian,
    qp_step,
    shape_axis,
    shape_trigger,
)


def test_shape_axis_deadzone():
    assert shape_axis(0.0) == 0.0
    assert shape_axis(0.10) == 0.0
    assert shape_axis(-0.10) == 0.0


def test_shape_axis_full_scale():
    assert np.isclose(shape_axis(1.0), 1.0)
    assert np.isclose(shape_axis(-1.0), -1.0)


def test_shape_trigger_xbox_range():
    # Xbox trigger convention used by this project:
    # released=+1, fully pressed=-1.
    assert np.isclose(shape_trigger(1.0), 0.0)
    assert np.isclose(shape_trigger(-1.0), 1.0)


def test_jacobian_shape_and_finite_values():
    q = np.zeros(6)
    j = jacobian(q)

    assert j.shape == (6, 6)
    assert np.all(np.isfinite(j))


def test_zero_command_at_center_is_zero():
    step = qp_step(
        np.zeros(6),
        np.zeros(3),
        np.zeros(3),
    )

    assert np.allclose(step, 0.0, atol=1e-9)


def test_qp_respects_max_joint_speed():
    control_hz = 30.0
    max_speed = 45.0
    max_step = max_speed / control_hz

    step = qp_step(
        np.zeros(6),
        np.array([10000.0, -10000.0, 10000.0]),
        np.array([10.0, -10.0, 10.0]),
        control_hz=control_hz,
        max_joint_speed_deg_s=max_speed,
    )

    assert np.all(np.abs(step) <= max_step + 1e-9)


def test_qp_stays_inside_soft_joint_limits():
    margin = 15.0

    q = np.zeros(6)

    step = qp_step(
        q,
        np.array([5000.0, 5000.0, 5000.0]),
        np.array([5.0, 5.0, 5.0]),
        soft_margin_deg=margin,
    )

    next_q = q + step

    assert np.all(
        next_q >= JOINT_MIN_DEG + margin - 1e-9
    )
    assert np.all(
        next_q <= JOINT_MAX_DEG - margin + 1e-9
    )
