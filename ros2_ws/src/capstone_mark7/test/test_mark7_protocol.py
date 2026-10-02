import pytest

from capstone_mark7.mark7_node import (
    ALL_FINGERS,
    DIRECTION_FORWARD,
    HAND_BOTH,
    build_packet,
    steps_to_raw,
)


def test_steps_to_raw():
    assert steps_to_raw(-1000) == 1
    assert steps_to_raw(-10) == 5
    assert steps_to_raw(0) == 10
    assert steps_to_raw(350) == 185
    assert steps_to_raw(10000) == 255


def test_open_packet():
    packet = build_packet(
        [-10, -10, -10, -10, -10, 0],
        speed_raw=50,
        current_raw=100,
    )

    assert len(packet) == 11

    assert packet == bytes([
        HAND_BOTH,
        ALL_FINGERS,
        50,
        100,
        5,
        5,
        5,
        5,
        5,
        10,
        DIRECTION_FORWARD,
    ])


def test_grasp_packet():
    packet = build_packet(
        [0, 350, 350, 350, 350, 0],
        speed_raw=50,
        current_raw=100,
    )

    assert len(packet) == 11
    assert packet[4:10] == bytes([
        10,
        185,
        185,
        185,
        185,
        10,
    ])


def test_packet_requires_six_positions():
    with pytest.raises(ValueError):
        build_packet(
            [0, 0],
            speed_raw=50,
            current_raw=100,
        )


@pytest.mark.parametrize(
    'speed,current',
    [
        (-1, 100),
        (256, 100),
        (50, -1),
        (50, 256),
    ],
)
def test_packet_rejects_invalid_byte_values(
    speed,
    current,
):
    with pytest.raises(ValueError):
        build_packet(
            [0, 0, 0, 0, 0, 0],
            speed_raw=speed,
            current_raw=current,
        )
