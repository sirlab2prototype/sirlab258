# Capstone

Neuromeka Indy7 + Mark7 기반 캡스톤 시스템.

기존 프로젝트를 그대로 복사하지 않고 필요한 기능만 새 구조로 재구현한다.

## Environment

- Ubuntu 24.04
- ROS 2 Jazzy
- Python 3.12
- Neuromeka Indy7
- Mand.ro Mark7
- Xbox Controller

Python dependencies:

- neuromeka==3.4.2.2
- numpy==1.26.4
- empy==3.3.4

Official Neuromeka ROS 2 driver:

- Repository: `neuromeka-robotics/indy-ros2`
- Branch: `jazzy-indyDCP3`
- Commit: `ca0353f077620c877c1edff2e26c8cd976773f27`
- Managed as a Git submodule

## Project structure

```text
capstone/
├── requirements.txt
├── scripts/
│   ├── bootstrap.sh
│   ├── install_commands.sh
│   └── bin/
│       ├── capi
│       ├── capg
│       ├── capx
│       └── caps
└── ros2_ws/
    └── src/
        ├── capstone_teleop/
        ├── capstone_mark7/
        └── external/
            └── indy-ros2/
```

## Setup on a new PC

Required beforehand:

- Ubuntu 24.04
- ROS 2 Jazzy
- Python 3.12
- Git
- colcon

Clone:

```bash
git clone --recurse-submodules <repository-url>
cd capstone
```

Setup:

```bash
./scripts/bootstrap.sh
```

`bootstrap.sh` performs:

1. prerequisite checks
2. Git submodule initialization
3. project-local `.venv` creation
4. Python dependency installation
5. ROS 2 workspace build
6. capstone command installation

It does not install system packages or modify system-wide configuration.

## Running

Activate the environment:

```bash
source .venv/bin/activate
```

Commands:

```text
capi  = Indy7
capg  = Mark7 gripper
capx  = Xbox + QP teleop
caps  = Full manual teleop setup
```

## Xbox controls

```text
Share        ARM / DISARM

RT / LT      Cartesian X
Right stick  Cartesian Y / Z

Left stick   Rx / Ry
LB / RB      Rz

A            Mark7 grasp
B            Mark7 full open

View         DISARM
View + A     Recover
View + B     Zero
View + Y     Capstone Home
```

## Capstone Home

Configuration:

```text
ros2_ws/src/capstone_teleop/config/indy7.yaml
```

Joint target in degrees:

```text
[0.005221, 40.003174, -129.996640, 90.000870, 0.000435, 90.507]
```

Current Home velocity is configured in the YAML file.

## Mark7

Default serial device:

```text
/dev/ttyACM0
```

Axis mapping:

```text
0 = thumb flexion
1 = index
2 = middle
3 = ring
4 = little
5 = thumb rotation
```

Presets:

```text
open  = [-10, -10, -10, -10, -10, 0]
grasp = [0, 350, 350, 350, 350, 0]
```

Verified grasp setting:

```text
current_raw = 100
```

## Current hardware defaults

```text
Indy7 IP     = 192.168.1.10
Mark7 device = /dev/ttyACM0
```

These may need to be changed for another machine or hardware setup.

## Moving or cloning the project

Do not reuse:

```text
.venv/
ros2_ws/build/
ros2_ws/install/
ros2_ws/log/
```

Recreate the Python environment and rebuild the ROS workspace after moving or cloning.

Use:

```bash
colcon build
```

Do not use `--symlink-install` with the currently pinned Indy ROS 2 source.

## Safety

Before moving the real robot:

- E-stop accessible
- people and obstacles clear
- current pose checked
- velocity and acceleration appropriate
- gripper state checked
- motion path checked

Current QP manual teleoperation does not provide external obstacle collision avoidance.
