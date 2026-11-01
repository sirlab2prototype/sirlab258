"""Correction annotation contract. Does not control recording or any hardware."""
import math


def reference(row):
    alignment = row.get("alignment") or {}
    return (alignment.get("reference_monotonic_ns", row["sample_monotonic_ns"]),
            alignment.get("reference_system_time_ns", row.get("sample_system_time_ns")))


def validate(marker, frames, fps, rows=None):
    if not isinstance(marker, dict) or marker.get("version") != 1:
        raise ValueError("correction_marker_missing_or_unsupported")
    index = marker.get("start_frame_index")
    if type(index) is not int or not 0 <= index < frames:
        raise ValueError("correction_segment_empty")
    boundary = marker.get("boundary_monotonic_ns")
    received = marker.get("input_received_monotonic_ns")
    processed = marker.get("command_processed_monotonic_ns")
    if (type(boundary) is not int or type(processed) is not int or boundary < 0 or processed < 0 or boundary > processed or
            marker.get("frame_index_origin") != 0):
        raise ValueError("correction_input_clock_invalid")
    source = marker.get("boundary_source")
    if source in ("stdin_line_received", "gui_button_received"):
        if type(received) is not int or boundary != received:
            raise ValueError("correction_input_clock_invalid")
    elif source != "command_processing_fallback" or received is not None or boundary != processed:
        raise ValueError("correction_input_clock_invalid")
    stamp = marker.get("start_dataset_timestamp_s")
    ref = marker.get("start_reference_monotonic_ns")
    if (not isinstance(stamp, (float, int)) or not math.isclose(stamp, index / fps, abs_tol=1e-9) or
            type(ref) is not int or ref < boundary):
        raise ValueError("correction_frame_timestamp_mismatch")
    if rows is not None:
        if len(rows) != frames or reference(rows[index]) != (ref, marker.get("start_reference_system_time_ns")):
            raise ValueError("correction_timing_sidecar_mismatch")
        if index and reference(rows[index - 1])[0] >= boundary:
            raise ValueError("correction_start_is_not_first_causal_frame")
        if not math.isclose(rows[index]["dataset_nominal_timestamp_s"], stamp, abs_tol=1e-9):
            raise ValueError("correction_timing_sidecar_mismatch")
