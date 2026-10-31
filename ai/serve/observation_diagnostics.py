"""Bounded callback diagnostics; no ROS initialization, images, or control API."""
from collections import Counter, deque

import numpy as np

from ai import dataset_contract as contract


class ReceptionDiagnostics:
    """Called under HistorySource.lock. Quantiles use at most 2048 recent values.

    FPS is callback delivery frequency, not proof of sensor exposure frequency.
    A Header gap is evidence to inspect, not a proven number of dropped frames.
    """
    def __init__(self, sample_limit=2048, epoch_window_ns=500_000_000):
        self.limit = sample_limit
        self.epoch_window_ns = epoch_window_ns
        self.topics = {}

    def record(self, name, source, value=None, *, rejected=None, message_format=None):
        row = self.topics.setdefault(name, {
            "callbacks": 0, "accepted": 0, "rejections": Counter(),
            "first_receipt_ns": None, "last_receipt_ns": None,
            "last_header_ns": None, "duplicate_headers": 0, "header_reversals": 0,
            "header_delay_unavailable": 0,
            "header_gaps_over_1_5_period": 0,
            "callback_intervals_ms": deque(maxlen=self.limit),
            "header_intervals_ms": deque(maxlen=self.limit),
            "callback_system_minus_header_ms": deque(maxlen=self.limit)})
        mono = source["callback_monotonic_ns"]
        row["callbacks"] += 1
        if row["first_receipt_ns"] is None:
            row["first_receipt_ns"] = mono
        if row["last_receipt_ns"] is not None and mono > row["last_receipt_ns"]:
            row["callback_intervals_ms"].append((mono-row["last_receipt_ns"])/1e6)
        row["last_receipt_ns"] = mono
        error = rejected or source.get("history_error")
        if error:
            row["rejections"][error] += 1
        else:
            row["accepted"] += 1
        if message_format is not None:
            row["last_message_format"] = message_format
        stamp = source.get("header_stamp")
        # Only use the epoch comparison after the shared History clock checks.
        # Backlog was also checked against the local ROS/system clock pair.
        comparable = source.get("history_error") is None and rejected in (None, "pre_subscription_header_discarded")
        if comparable and stamp is not None:
            header = stamp["sec"]*1_000_000_000+stamp["nanosec"]
            # Epoch compatibility sanity check, NOT a clock conversion or proof
            # of the publisher's clock domain. Relative/device epochs and very
            # old backlog must not be presented as measured transport latency.
            difference = source["callback_system_time_ns"]-header
            if 0 <= difference <= self.epoch_window_ns:
                row["callback_system_minus_header_ms"].append(difference/1e6)
            else:
                row["header_delay_unavailable"] += 1
            previous = row["last_header_ns"]
            if previous is not None:
                if header == previous:
                    row["duplicate_headers"] += 1
                elif header < previous:
                    row["header_reversals"] += 1
                else:
                    interval = (header-previous)/1e6
                    row["header_intervals_ms"].append(interval)
                    period = 1000/(30 if name in contract.CAMERA_NAMES else 20)
                    row["header_gaps_over_1_5_period"] += int(interval > 1.5*period)
            row["last_header_ns"] = header
        elif error == "header_reversal":
            row["header_reversals"] += 1
        if name == "gripper" and not error:
            row["command_state"] = float(value)

    def snapshot(self, now_ns):
        def distribution(values):
            if not values:
                return {"retained_samples": 0, "p50": None, "p95": None, "max": None}
            return {"retained_samples": len(values), "p50": float(np.median(values)),
                    "p95": float(np.percentile(values, 95)), "max": float(max(values))}
        result = {}
        for name in (*contract.CAMERA_NAMES, "state", "gripper"):
            row = self.topics.get(name)
            if row is None:
                result[name] = {"callbacks": 0, "accepted": 0, "callback_fps": None,
                                "last_receive_age_ms": None, "command_state": None}
                continue
            elapsed = row["last_receipt_ns"]-row["first_receipt_ns"]
            result[name] = {key: value for key, value in row.items()
                            if not isinstance(value, deque) and key != "rejections"}
            result[name].update(rejections=dict(row["rejections"]),
                                callback_fps=(row["callbacks"]-1)*1e9/elapsed if elapsed > 0 else None,
                                last_receive_age_ms=(now_ns-row["last_receipt_ns"])/1e6)
            for key, value in row.items():
                if isinstance(value, deque):
                    result[name][key] = distribution(value)
        return {"topics": result, "quantile_sample_limit_per_metric": self.limit,
                "quantile_scope": "most recent bounded samples",
                "header_epoch_sanity_window_ns": self.epoch_window_ns,
                "header_delay_semantics": "callback system time minus Header; not verified exposure latency",
                "fps_semantics": "callback receipt rate, including rejected/backlog messages",
                "gripper_semantics": "successful command event; not physical sensor feedback"}
