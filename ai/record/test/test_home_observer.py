"""Getter diagnostics exercised with fake SDK/clock/files only."""
import io
import json
import sys

import pytest

from ai.record.test.test_home_action import load_diagnostic


class Clock:
    def __init__(self): self.ns = 10_000_000_000
    def mono(self): return self.ns
    def system(self): return self.ns + 1_700_000_000_000_000_000
    def sleep(self, seconds): self.ns += round(seconds*1e9)


class FakeSDK:
    def __init__(self, clock, *, fault=None, rpc_ns=1_000_000):
        self.clock, self.fault, self.rpc_ns = clock, fault, rpc_ns
        self.calls, self.closed = [], []
        self.index = -1
        self.rtde = object()
        for name in ("boot", "control", "device", "config", "rtde", "cri"):
            setattr(self, name+"_channel", type("Channel", (), {"close": lambda _, n=name: self.closed.append(n)})())

    def __getattr__(self, name):
        assert name in ("get_control_state", "get_control_data", "get_motion_data", "get_servo_data"), name
        def getter():
            self.calls.append(name)
            self.clock.ns += self.rpc_ns
            if name == "get_control_state":
                self.index += 1
                if self.fault == "rpc": raise TimeoutError("fake deadline")
                if self.fault == "interrupt": raise KeyboardInterrupt
                if self.fault == "shape": return {"response": {"code": 0}, "q": [0]*6, "qdes": [0]*5}
                value = {"q": [self.index]*6, "qdes": [self.index+.25]*6, "response": {"code": 0}}
                if self.fault == "nan": value["qdes"][2] = float("nan")
                if self.fault == "reject": value["response"]["code"] = 7
                if self.fault == "unknown_response": value.pop("response")
                if self.fault == "non_object": return [0]*6
                return value
            return {"response": {"code": 0}, "is_in_motion": self.index in (1,2)}
        return getter


def test_bounded_getters_capture_before_during_after_and_20hz():
    m = load_diagnostic()
    c = Clock(); sdk = FakeSDK(c); stream = io.StringIO()
    assert m.collect(sdk, stream, .21, mono=c.mono, system=c.system, sleep=c.sleep) == 0
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    samples, summary = rows[:-1], rows[-1]
    assert len(samples) == 5 and summary["samples"] == 5
    assert summary["actual_response_hz"] == pytest.approx(20)
    assert set(summary["motion_segments_approximate"]) == {"reported_before_motion", "reported_moving", "reported_after_motion"}
    assert all(call["rpc_elapsed_ns"] == 1_000_000 for row in samples for call in row["calls"].values())
    assert all(row["controller_sample_timestamp"] is None for row in samples)
    assert summary["qdes_unit"] == "unverified" and summary["qdes_semantics_verified"] is False
    assert summary["motion_segments_approximate"]["reported_moving"]["max_abs_qdes_minus_q"] == [.25]*6
    assert sdk.calls == list(m.GETTERS)*5


@pytest.mark.parametrize("fault", ["rpc", "shape", "nan", "reject", "non_object"])
def test_error_stops_observation_without_fallback_or_more_rpcs(fault):
    m = load_diagnostic(); c = Clock(); sdk = FakeSDK(c, fault=fault); stream = io.StringIO()
    assert m.collect(sdk, stream, .2, mono=c.mono, system=c.system, sleep=c.sleep) == 2
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert sdk.calls == ["get_control_state"]
    assert rows[-1]["reason"] == "getter_failed"
    assert "error" in rows[0]["calls"]["get_control_state"]
    if fault == "nan": assert rows[0]["calls"]["get_control_state"]["raw"]["qdes"][2] == "nan"
    assert not sdk.closed  # collect does not own an injected client's lifecycle


def test_overrun_does_not_burst_or_send_stop():
    m = load_diagnostic(); c = Clock(); sdk = FakeSDK(c, rpc_ns=20_000_000); stream = io.StringIO()
    assert m.collect(sdk, stream, .5, mono=c.mono, system=c.system, sleep=c.sleep) == 2
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(rows) == 2 and rows[-1]["reason"] == "observation_overrun"
    assert sdk.calls == list(m.GETTERS)


def test_keyboard_interrupt_ends_only_observer():
    m = load_diagnostic(); c = Clock(); sdk = FakeSDK(c, fault="interrupt"); stream = io.StringIO()
    assert m.collect(sdk, stream, 1, mono=c.mono, system=c.system, sleep=c.sleep) == 130
    assert json.loads(stream.getvalue())["reason"] == "operator_interrupted_observation"
    assert sdk.calls == ["get_control_state"]


def test_init_failure_is_recorded_without_sdk_import(tmp_path, monkeypatch):
    m = load_diagnostic(); p = tmp_path/"new.jsonl"
    def fail(ip): raise RuntimeError("fake SDK init failure")
    assert m.main(["--connect-readonly", "--ip", "192.0.2.1", "--approval-record", "offline fixture",
                   "--output", str(p)], sdk_factory=fail) == 2
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    assert rows[0]["kind"] == "observation_session" and rows[-1]["kind"] == "observation_error"


def test_owned_channels_all_close_even_if_one_fails(tmp_path, monkeypatch):
    m = load_diagnostic(); c = Clock(); sdk = FakeSDK(c)
    def fail(): sdk.closed.append("boot"); raise RuntimeError("fake close failure")
    sdk.boot_channel.close = fail
    monkeypatch.setattr(m, "collect", lambda *args: 0)
    assert m.main(["--connect-readonly", "--ip", "192.0.2.1", "--approval-record", "offline fixture",
                   "--output", str(tmp_path/"new.jsonl")], sdk_factory=lambda ip:sdk) == 2
    assert sdk.closed == ["boot", "control", "device", "config", "rtde", "cri"]
    assert not sdk.calls
    rows = [json.loads(line) for line in (tmp_path/"new.jsonl").read_text().splitlines()]
    assert rows[-1]["stage"] == "channel_close" and rows[-1]["channel"] == "boot"


def test_offline_modes_and_invalid_args_never_import_sdk(tmp_path, monkeypatch, capsys):
    m = load_diagnostic()
    import builtins
    original = builtins.__import__
    def checked(name, *args, **kwargs):
        assert name != "neuromeka", "unexpected SDK import"
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", checked)
    assert m.main(["--describe"]) == 0
    for args in ([], ["--ip","fake"], ["--describe","--connect-readonly"],
                 ["--connect-readonly","--ip","fake","--output",str(tmp_path/"never"),"--approval-record"," "]):
        with pytest.raises(SystemExit): m.main(args)
    p = tmp_path/"sample.jsonl"
    c = Clock(); stream = io.StringIO()
    m.collect(FakeSDK(c), stream, .11, mono=c.mono, system=c.system, sleep=c.sleep)
    p.write_text(stream.getvalue())
    capsys.readouterr()
    assert m.main(["--analyze", str(p)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["samples"] == 3 and result["qdes_unit"] == "unverified"
    assert not (tmp_path/"never").exists()


def test_deadline_wrapper_allows_only_getters():
    m = load_diagnostic(); calls = []
    class Stub:
        def GetControlState(self, request, **kwargs): calls.append(kwargs); return "fake"
    wrapped = m.DeadlineRTDE(Stub())
    assert wrapped.GetControlState(object()) == "fake" and calls == [{"timeout": .1}]
    for name in ("MoveJ", "StopMotion", "StartTeleop"):
        with pytest.raises(AttributeError): getattr(wrapped, name)


@pytest.mark.parametrize("seconds", ["nan", "inf", "-1", "0", "121"])
def test_duration_rejected_before_output_or_sdk(tmp_path, seconds):
    m = load_diagnostic(); output = tmp_path/"never.jsonl"
    def forbidden(ip): raise AssertionError("SDK initialization forbidden")
    with pytest.raises(SystemExit):
        m.main(["--connect-readonly", "--ip", "192.0.2.1", "--approval-record", "offline fixture",
                "--output", str(output), "--seconds", seconds], sdk_factory=forbidden)
    assert not output.exists()


def test_offline_analysis_preserves_init_errors(tmp_path, capsys):
    m = load_diagnostic(); p = tmp_path/"failed.jsonl"
    p.write_text(json.dumps({"kind":"observation_error","error":"fake init failure"})+"\n")
    assert m.main(["--analyze",str(p)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["samples"] == 0 and report["session_errors"] == 1


def test_controller_rtde_without_application_response_is_not_a_fake_ack():
    m = load_diagnostic(); c=Clock(); sdk=FakeSDK(c,fault="unknown_response")
    row=m.snapshot(sdk,mono=c.mono,system=c.system)
    call=row["calls"]["get_control_state"]
    assert "error" not in call and call["application_status"] == "unavailable"
    assert "response" not in call["raw"]
    assert m.payload_error("get_control_state",{"q":[0]*6,"qdes":[0]*6,"response":{"code":"0"}}) is None
    assert m.payload_error("get_control_state",{"q":[0]*6,"qdes":[0]*6,"response":{"code":"7"}})
