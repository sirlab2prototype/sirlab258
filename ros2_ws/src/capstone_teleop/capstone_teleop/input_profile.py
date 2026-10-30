"""Cartesian joystick input shaping, independent of ROS."""
import numpy as np
from .qp_core import shape_axis, shape_trigger


def radial_stick(x, y, deadzone, exponent):
    xy = np.clip(np.asarray([x, y], dtype=float), -1.0, 1.0)
    if not np.isfinite(xy).all() or not (
        0 <= deadzone < 1 and exponent > 0
    ):
        raise ValueError("invalid stick input")

    length = float(np.linalg.norm(xy))
    if length <= deadzone:
        return np.zeros(2)

    magnitude = (
        (min(length, 1.0) - deadzone) / (1.0 - deadzone)
    ) ** exponent
    return xy / length * magnitude


class MotionProfile:
    def __init__(
        self, hz, max_step, angular_speed,
        stick_deadzone, stick_exponent,
        trigger_deadzone, trigger_exponent,
        accel_mm_s2, yaw_accel_rad_s2,
    ):
        self.hz = hz
        self.max_step = max_step
        self.angular_speed = angular_speed
        self.dz = stick_deadzone
        self.exp = stick_exponent
        self.tdz = trigger_deadzone
        self.texp = trigger_exponent
        self.accel = accel_mm_s2
        self.yaw_accel = yaw_accel_rad_s2

        if not (
            hz > 0 and max_step > 0
            and angular_speed >= 0
            and accel_mm_s2 > 0
            and yaw_accel_rad_s2 > 0
            and 0 <= stick_deadzone < 1
            and stick_exponent > 0
            and 0 <= trigger_deadzone < 1
            and trigger_exponent > 0
        ):
            raise ValueError("invalid motion parameters")

        self.reset()

    def reset(self):
        self.xyz = np.zeros(3)
        self.yaw = 0.0

    def neutral(self, axes, buttons):
        if len(axes) < 8 or len(buttons) < 12:
            return False
        if not np.isfinite(
            np.asarray(axes[:8], dtype=float)
        ).all():
            return False

        left = radial_stick(
            axes[0], axes[1], self.dz, self.exp
        )

        return (
            not np.any(left)
            and shape_axis(axes[3], self.dz, self.exp) == 0
            and shape_axis(axes[4], self.dz, self.exp) == 0
            and shape_trigger(
                axes[2], self.tdz, self.texp
            ) == 0
            and shape_trigger(
                axes[5], self.tdz, self.texp
            ) == 0
            and abs(axes[6]) <= 0.5
            and abs(axes[7]) <= 0.5
            and not any(buttons)
        )

    def update(self, axes, step):
        if len(axes) < 8 or not np.isfinite(
            np.asarray(axes[:8], dtype=float)
        ).all():
            raise ValueError("invalid Xbox input")

        if not np.isfinite(step) or not (
            0 < step <= self.max_step
        ):
            raise ValueError("invalid speed step")

        left = radial_stick(
            axes[0], axes[1], self.dz, self.exp
        )
        lt = shape_trigger(
            axes[2], self.tdz, self.texp
        )
        rt = shape_trigger(
            axes[5], self.tdz, self.texp
        )
        right = shape_axis(
            axes[3], self.dz, self.exp
        )

        # Preserve cap's XYZ axis mapping.
        direction = np.array([
            left[1], left[0], lt - rt
        ])
        length = np.linalg.norm(direction)

        if length > 1:
            direction /= length

        target = direction * step

        # Cancel residual input when released.
        if not np.any(left):
            self.xyz[:2] = 0
        if lt == rt:
            self.xyz[2] = 0

        if not np.any(target):
            self.xyz[:] = 0
        else:
            # Apply a newly reduced speed limit immediately.
            current = np.linalg.norm(self.xyz)
            if current > step:
                self.xyz *= step / current

            difference = target - self.xyz
            delta = np.linalg.norm(difference)
            max_delta = self.accel / (self.hz ** 2)

            if delta > max_delta:
                difference *= max_delta / delta

            self.xyz += difference

        # All rotational axes follow cap's speed scale.
        angular_step = (
            self.angular_speed
            * (step / self.max_step)
            / self.hz
        )
        yaw_target = right * angular_step

        if yaw_target == 0:
            self.yaw = 0
        else:
            self.yaw = float(np.clip(
                self.yaw, -angular_step, angular_step
            ))
            max_yaw_delta = (
                self.yaw_accel / (self.hz ** 2)
            )
            self.yaw += float(np.clip(
                yaw_target - self.yaw,
                -max_yaw_delta,
                max_yaw_delta,
            ))

        rotation = np.array([
            -axes[6] * angular_step,
            -axes[7] * angular_step,
            self.yaw,
        ])

        return self.xyz.copy(), rotation
