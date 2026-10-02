import math
import numpy as np


# Existing Indy7 teleop implementation/reference-derived kinematic constants.
# Units: mm / rad
JOINT_ORIGINS = (
    ((0.0, 0.0, 77.5), (0.0, 0.0, 0.0)),
    ((0.0, -109.0, 222.0), (math.pi / 2, math.pi / 2, 0.0)),
    ((-450.0, 0.0, -30.5), (0.0, 0.0, 0.0)),
    ((-267.0, 0.0, -75.0), (-math.pi / 2, 0.0, math.pi / 2)),
    ((0.0, -114.0, 83.0), (math.pi / 2, math.pi / 2, 0.0)),
    ((-168.0, 0.0, 69.0), (-math.pi / 2, 0.0, math.pi / 2)),
)

TCP_MM = (0.0, 0.0, 60.0)

JOINT_MIN_DEG = np.array(
    [-175.0, -175.0, -175.0, -175.0, -175.0, -215.0]
)
JOINT_MAX_DEG = np.array(
    [175.0, 175.0, 175.0, 175.0, 175.0, 215.0]
)


def shape_axis(value, deadzone=0.18, exponent=2.0):
    value = float(np.clip(value, -1.0, 1.0))
    mag = abs(value)

    if mag <= deadzone:
        return 0.0

    mag = (mag - deadzone) / (1.0 - deadzone)
    mag = mag ** exponent

    return math.copysign(mag, value)


def shape_trigger(raw, deadzone=0.08, exponent=2.0):
    # Xbox: released=+1, pressed=-1
    amount = float(np.clip((1.0 - raw) / 2.0, 0.0, 1.0))

    if amount <= deadzone:
        return 0.0

    amount = (amount - deadzone) / (1.0 - deadzone)

    return amount ** exponent



def _rpy(rpy):
    r, p, y = rpy

    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)

    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])


def _tf(xyz=(0.0, 0.0, 0.0), rotation=None):
    t = np.eye(4)
    t[:3, :3] = np.eye(3) if rotation is None else rotation
    t[:3, 3] = xyz
    return t


def _rotz(q):
    c, s = math.cos(q), math.sin(q)

    return np.array([
        [c, -s, 0.0],
        [s, c, 0.0],
        [0.0, 0.0, 1.0],
    ])


def jacobian(joint_deg):
    t = np.eye(4)

    axes = []
    origins = []

    for q_deg, (xyz, rpy) in zip(joint_deg, JOINT_ORIGINS):
        t = t @ _tf(xyz, _rpy(rpy))

        origins.append(t[:3, 3].copy())
        axes.append(t[:3, :3] @ np.array([0.0, 0.0, 1.0]))

        t = t @ _tf(rotation=_rotz(math.radians(q_deg)))

    t = t @ _tf(TCP_MM)

    tcp = t[:3, 3]

    j = np.zeros((6, 6))

    for i, (axis, origin) in enumerate(zip(axes, origins)):
        j[:3, i] = np.cross(axis, tcp - origin)
        j[3:, i] = axis

    return j


def _bounded_least_squares(a, b, lower, upper):
    n = a.shape[1]

    x = np.zeros(n)
    free = list(range(n))
    fixed = []

    for _ in range(n + 1):
        residual = b.copy()

        if fixed:
            residual -= a[:, fixed] @ x[fixed]

        if free:
            x[free] = np.linalg.lstsq(
                a[:, free],
                residual,
                rcond=None,
            )[0]

        violations = []

        for i in free:
            if x[i] < lower[i]:
                violations.append((lower[i] - x[i], i, lower[i]))
            elif x[i] > upper[i]:
                violations.append((x[i] - upper[i], i, upper[i]))

        if not violations:
            break

        _, i, boundary = max(violations)

        x[i] = boundary
        free.remove(i)
        fixed.append(i)

    return np.clip(x, lower, upper)


def qp_step(
    commanded_joint_deg,
    translation_mm,
    rotation_rad=None,
    *,
    control_hz=30.0,
    soft_margin_deg=15.0,
    max_joint_speed_deg_s=45.0,
    orientation_weight=25.0,
    centering_weight=0.08,
    damping=0.04,
):
    q = np.asarray(commanded_joint_deg, dtype=float)
    translation = np.asarray(translation_mm, dtype=float)

    if rotation_rad is None:
        rotation = np.zeros(3)
    else:
        rotation = np.asarray(rotation_rad, dtype=float)

    lower_limit = JOINT_MIN_DEG + soft_margin_deg
    upper_limit = JOINT_MAX_DEG - soft_margin_deg

    j = jacobian(q)

    # optimisation variable: joint degrees / tick
    j_pos = j[:3] * (math.pi / 180.0)
    j_rot = j[3:] * (math.pi / 180.0)

    midpoint = (lower_limit + upper_limit) / 2.0
    half_range = np.maximum(
        (upper_limit - lower_limit) / 2.0,
        1.0,
    )

    centering = np.clip(
        (midpoint - q) / half_range,
        -1.0,
        1.0,
    )

    a = np.vstack([
        j_pos,
        orientation_weight * j_rot,
        centering_weight * np.eye(6),
        damping * np.eye(6),
    ])

    b = np.concatenate([
        translation,
        orientation_weight * rotation,
        centering_weight * centering,
        np.zeros(6),
    ])

    max_step = max_joint_speed_deg_s / control_hz

    lower = np.maximum(
        -max_step,
        lower_limit - q,
    )

    upper = np.minimum(
        max_step,
        upper_limit - q,
    )

    # If already inside a soft-margin region, escape motion remains possible.
    lower = np.minimum(lower, 0.0)
    upper = np.maximum(upper, 0.0)

    step = _bounded_least_squares(
        a,
        b,
        lower,
        upper,
    )

    return step
