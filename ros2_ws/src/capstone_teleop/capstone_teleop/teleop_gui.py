#!/usr/bin/env python3

import time
import tkinter as tk
from tkinter import ttk

import rclpy
from rclpy.node import Node

from std_msgs.msg import Float64, String


class TeleopGui(Node):
    def __init__(self):
        super().__init__('teleop_gui')

        self.root = tk.Tk()
        self.root.title('Capstone Xbox Teleop')
        # Start roughly at the preferred large layout, while keeping
        # the window inside smaller displays.
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        width = min(860, screen_w - 40)
        height = min(1360, screen_h - 80)

        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)

        self.root.geometry(
            f'{width}x{height}+{x}+{y}'
        )
        self.root.resizable(True, True)

        self.status_var = tk.StringVar(
            value='연결 대기 / WAITING'
        )
        self.last_status_time = None
        self.speed_var = tk.StringVar(
            value='-- mm/tick'
        )

        self.create_subscription(
            Float64,
            '/capstone/teleop_speed_mm',
            self.speed_callback,
            10,
        )

        self.create_subscription(
            String,
            '/capstone/teleop_status',
            self.status_callback,
            10,
        )

        self.build_ui()

        self.root.protocol(
            'WM_DELETE_WINDOW',
            self.close,
        )

        self.root.after(
            50,
            self.spin_ros,
        )

    def build_ui(self):
        outer = ttk.Frame(
            self.root,
            padding=20,
        )
        outer.pack(
            fill='both',
            expand=True,
        )

        title = ttk.Label(
            outer,
            text='CAPSTONE XBOX TELEOP',
            font=('TkDefaultFont', 17, 'bold'),
        )
        title.pack(pady=(0, 18))

        status_frame = ttk.LabelFrame(
            outer,
            text='상태 / Status',
            padding=12,
        )
        status_frame.pack(
            fill='x',
            pady=(0, 12),
        )

        self.status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            font=('TkDefaultFont', 15, 'bold'),
        )
        self.status_label.pack()

        speed_frame = ttk.LabelFrame(
            outer,
            text='속도 / Speed',
            padding=12,
        )
        speed_frame.pack(
            fill='x',
            pady=(0, 12),
        )

        ttk.Label(
            speed_frame,
            textvariable=self.speed_var,
            font=('TkDefaultFont', 16, 'bold'),
        ).pack()

        self.add_section(
            outer,
            '이동 / Translation',
            [
                ('Left Stick ↑ / ↓', 'X'),
                ('Left Stick ← / →', 'Y'),
                ('RT / LT', 'Z'),
            ],
        )

        self.add_section(
            outer,
            '회전 / Rotation',
            [
                ('Right Stick ← / →', 'Rz / Yaw'),
                ('D-pad ← / →', 'Rx'),
                ('D-pad ↑ / ↓', 'Ry'),
            ],
        )

        self.add_section(
            outer,
            '그리퍼 / Gripper',
            [
                ('A', 'Grasp / 잡기'),
                ('B', 'Open / 열기'),
            ],
        )

        self.add_section(
            outer,
            '시스템 / System',
            [
                ('Share', 'ARM / DISARM'),
                ('View + A', 'Recover / 복구'),
                ('View + B', 'Zero'),
                ('View + Y', 'Home'),
                ('View + LB', 'Speed -0.25'),
                ('View + RB', 'Speed +0.25'),
            ],
        )

        ttk.Label(
            outer,
            text=(
                'View 단독은 동작하지 않음 / '
                'View alone has no action'
            ),
        ).pack(pady=(8, 0))

    def add_section(self, parent, title, rows):
        frame = ttk.LabelFrame(
            parent,
            text=title,
            padding=10,
        )
        frame.pack(
            fill='x',
            pady=(0, 10),
        )

        for key, action in rows:
            row = ttk.Frame(frame)
            row.pack(
                fill='x',
                pady=2,
            )

            ttk.Label(
                row,
                text=key,
                width=23,
            ).pack(side='left')

            ttk.Label(
                row,
                text=action,
            ).pack(side='left')

    def speed_callback(self, msg):
        self.speed_var.set(
            f'{msg.data:.2f} mm/tick'
        )

    def status_callback(self, msg):
        self.last_status_time = time.monotonic()
        status = msg.data.upper()

        if status == 'ARMED':
            text = '● ARMED / 조작 가능'
            color = '#16803a'

        elif status == 'ARMING':
            text = '● ARMING / 준비 중'
            color = '#b26a00'

        else:
            text = '● DISARMED / 조작 정지'
            color = '#b02020'

        self.status_var.set(text)
        self.status_label.configure(
            fg=color,
        )

    def spin_ros(self):
        if not rclpy.ok():
            return

        rclpy.spin_once(
            self,
            timeout_sec=0.0,
        )

        if (
            self.last_status_time is not None
            and time.monotonic() - self.last_status_time > 1.0
        ):
            self.status_var.set(
                '● TELEOP OFFLINE / 연결 끊김'
            )
            self.status_label.configure(
                fg='#666666',
            )

        self.root.after(
            50,
            self.spin_ros,
        )

    def close(self):
        self.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()

        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(args=None):
    rclpy.init(args=args)

    node = TeleopGui()

    try:
        node.run()
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()


if __name__ == '__main__':
    main()
