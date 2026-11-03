"""Home recording tests: fake clocks, SDK, ROS and isolated temporary datasets."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ai.home_action import (ActionContextGuard, HomeActionError, HomeArrivalVerifier,
    SimulatedHomeContract, SimulatedQdesReader, completion_status, JOINTS)
from ai.record.test.test_indy7_act_record import recorder, HistoryHarness, EPOCH, MONO
from ai.record.test.test_collection import managed

MS = 1_000_000


def envelope(ms, seq, phase="QP_TELEOP", generation=0):
    stamp = EPOCH + ms * MS
    return {"version": 1, "stream_id": "fake-qp", "sequence": seq,
        "stamp_ns": stamp, "transition_ns": stamp, "phase": phase,
        "home_generation": generation, "last_unrecordable_home_ns": 0,
        "arrival_verified": phase == "HOME_COMPLETE",
        "sample": {"simulated": True, "evidence_id": "fake-only", "kind": "home_qdes",
            "unit": "rad", "joint_names": JOINTS, "target_rad": [min(ms, 100) / 1000] * 6,
            "request_system_ns": stamp - 2 * MS, "response_system_ns": stamp,
            "request_monotonic_ns": MONO + (ms-2)*MS,
            "response_monotonic_ns": MONO + ms*MS, "rpc_elapsed_ns": 2*MS,
            "response_ok": True}}


def harness(module, root, *, simulated=True, mutate=None, session=None, require_home=False):
    h = HistoryHarness(module, root)
    if session is not None:
        h.session = session
    h.push("action_context", -1, -.5, envelope(-1, 0))
    timing = h.timing(0)
    timing["require_action_context"] = True
    timing["require_home_complete"] = require_home
    a = module.HistoryAligner.prepare(h.options, h.history.snapshot(MONO), timing,
        home_contract=SimulatedHomeContract("fake-only") if simulated else None)
    h.session.start(a.target, MONO / 1e9, timing, a)
    h.generate(900)
    # Delayed measured joints track a distinct desired trajectory. The final
    # Home target is 0.1rad, reached after the QP→Home transition, not at startup.
    h.events = [(receipt, name, generated,
        np.full(6, min(max(generated-20, 0), 100)/1000, np.float32) if name == "state" else value)
        for receipt, name, generated, value in h.events]
    arrival = HomeArrivalVerifier(np.full(6, .1), MONO+60*MS)
    h.events.append((11, "action", 10, np.full(6, .01, np.float32)))
    for seq, ms in enumerate(range(20, 901, 20), 1):
        phase = ("QP_TELEOP" if ms < 60 or ms >= 440 else
                 "HOME_REQUESTED" if ms == 60 else
                 "HOME_MOVING" if ms < 180 else
                 "HOME_SETTLING" if ms < 420 else "HOME_COMPLETE")
        value = envelope(ms, seq, phase, int(ms >= 60))
        if phase.startswith("HOME_"):
            arrival.update(now_ns=MONO+ms*MS, sample_ns=MONO+ms*MS, status_ns=MONO+ms*MS,
                q_rad=np.full(6, min(max(ms-20, 0), 100)/1000),
                velocity_rad_s=np.full(6, 1. if ms < 120 else 0.),
                in_motion=ms < 120, connected=True, healthy=True)
            value["arrival_verified"] = arrival.state == "HOME_COMPLETE"
        if mutate:
            mutate(value, ms)
        h.events.append((ms + 1, "action_context", ms, value))
    h.events.append((446, "action", 445, np.full(6, .6, np.float32)))
    h.events.sort(key=lambda event: event[0])
    return h


def test_qp_home_complete_rearm_saved_as_one_7d_episode(recorder, tmp_path):
    h = harness(recorder, tmp_path)
    for ms in range(50, 551, 50):
        h.tick(ms)
    assert h.session.integrity["valid"]
    assert h.row(0)["recorded_action"]["kind"] == "qp_target"
    row = h.row(1)
    assert row["recorded_action"]["kind"] == "home_qdes"
    assert row["recorded_action"]["source"]["sample"]["request_system_ns"] == EPOCH + 98*MS
    assert row["recorded_action"]["source"]["header_semantics"].startswith("RPC_response")
    with np.load(h.session.path / "00000001.npz") as data:
        np.testing.assert_allclose(data["action"][:6], .1)
        assert data["action"].shape == data["observation.state"].shape == (7,)
        assert data["action"][6] == data["observation.state"][6] == 0
        assert not np.array_equal(data["action"][:6], data["observation.state"][:6])
    assert h.row(8)["recorded_action"]["kind"] == "qp_target"
    h.session.finish_aligned(h.history.snapshot(MONO+610*MS), h.timing(610))
    assert h.session.state == "REVIEW" and h.session.integrity["valid"]
    assert "저장 완료" in h.session.save("success", tmp_path / "success")
    assert len(h.saved["success"]) == 1 and len(h.saved["success"][0]) == 11
    meta = json.loads((tmp_path / "success/meta/recorder_timing/episode-000000/episode.json").read_text())
    assert meta["home_action_acceptance"] == "simulated_fixture_only"
    assert meta["action_context_stop"]["phase"] == "QP_TELEOP"


@pytest.mark.parametrize("fault,reason", [
    ("missing", "unverified_home_action"), ("old", "stale_home_action"),
    ("failure", "home_query_failed"), ("slow", "home_rpc_too_slow"),
    ("order", "home_action_semantics_mismatch"), ("unit", "home_action_semantics_mismatch"),
    ("shape", "home_joint_vector_must_be_finite_6d"), ("nan", "home_joint_vector_must_be_finite_6d"),
    ("cancel", "home_failed_or_cancelled"), ("arrival", "home_arrival_unverified"),
    ("rearm", "home_rearm_before_verified_completion"),
])
def test_bad_home_blocks_success_and_correction(recorder, tmp_path, fault, reason):
    def mutate(v, ms):
        # Inject at an inter-frame event too; a newer good sample cannot hide it.
        if ms != (420 if fault == "arrival" else 80):
            return
        s = v["sample"]
        if fault == "missing": v["sample"] = None
        if fault == "old":
            s["response_system_ns"] -= 100*MS
            s["request_system_ns"] -= 100*MS
        if fault == "failure": s["response_ok"] = False
        if fault == "slow": s["request_system_ns"] -= 50*MS
        if fault == "order": s["joint_names"] = JOINTS[::-1]
        if fault == "unit": s["unit"] = "deg"
        if fault == "shape": s["target_rad"] = [0]*7
        if fault == "nan": s["target_rad"][2] = float("nan")
        if fault == "cancel": v["phase"] = "HOME_CANCELLED"
        if fault == "arrival": v["arrival_verified"] = False
        if fault == "rearm": v["phase"] = "QP_TELEOP"
    h = harness(recorder, tmp_path, mutate=mutate)
    for ms in range(50, 451, 50):
        h.tick(ms)
        if not h.session.integrity["valid"]:
            break
    assert h.session.state == "REVIEW" and not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == reason
    for outcome in ("success", "correction"):
        h.session.save(outcome, tmp_path / outcome)
    assert h.opens == []


def test_unverified_real_home_locked_even_if_message_claims_verified(recorder, tmp_path):
    h = harness(recorder, tmp_path, simulated=False)
    h.tick(50)
    h.tick(100)
    assert not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == "live_home_action_locked"


def test_stop_during_home_invalid_even_between_frame_releases(recorder, tmp_path):
    h = harness(recorder, tmp_path)
    h.tick(50)  # callbacks through 110ms include Home, reference still 50ms
    h.session.finish_aligned(h.history.snapshot(MONO+115*MS), h.timing(115))
    assert not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == "recording_ended_before_home_complete"


def test_begin_during_home_rejected_without_pending(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    h.push("action_context", -1, -.5, envelope(-1, 0, "HOME_MOVING", 1))
    with pytest.raises(recorder.IntegrityError, match="recording_started_during_home"):
        recorder.HistoryAligner.prepare(h.options, h.history.snapshot(MONO), h.timing(0),
                                       SimulatedHomeContract("fake-only"))
    assert h.session.path is None and h.opens == []


def test_missing_and_stale_context_block_new_live_recorders(recorder, tmp_path):
    h = HistoryHarness(recorder, tmp_path)
    timing = h.timing(0)
    timing["require_action_context"] = True
    with pytest.raises(recorder.IntegrityError, match="missing_action_context"):
        recorder.HistoryAligner.prepare(h.options, h.history.snapshot(MONO), timing)
    h.push("action_context", -100, -.5, envelope(-100, 0))
    with pytest.raises(recorder.IntegrityError, match="stale_action_context"):
        recorder.HistoryAligner.prepare(h.options, h.history.snapshot(MONO), timing)


@pytest.mark.parametrize("mode", ["gap", "publisher", "sticky", "silence"])
def test_stream_failures_cannot_hide_home(recorder, tmp_path, mode):
    def mutate(v, ms):
        if mode == "publisher" and ms >= 60: v["stream_id"] = "restarted"
        if mode == "gap" and ms >= 60: v["sequence"] += 1
        if mode == "sticky":
            v["phase"], v["home_generation"] = "QP_TELEOP", 0
            if ms >= 60: v["last_unrecordable_home_ns"] = EPOCH+60*MS
    h = harness(recorder, tmp_path, mutate=mutate)
    if mode == "silence":
        h.events = [e for e in h.events if e[1] != "action_context"]
    h.tick(50)
    h.tick(100)
    assert not h.session.integrity["valid"]


def test_home_state_skew_rejected():
    c = SimulatedHomeContract("fake-only")
    with pytest.raises(HomeActionError, match="home_action_state_skew"):
        c.target(envelope(100, 1, "HOME_MOVING", 1), EPOCH+100*MS, EPOCH)


def test_rearm_needs_a_new_qp_target_before_recording(recorder, tmp_path):
    h = harness(recorder, tmp_path)
    h.events = [e for e in h.events if not (e[1] == "action" and e[2] == 445)]
    for ms in range(50, 451, 50):
        h.tick(ms)
    assert not h.session.integrity["valid"]
    assert h.session.integrity["violations"][-1]["reason"] == "home_to_qp_action_gap"


def test_qp_command_during_home_is_a_source_conflict(recorder, tmp_path):
    h = harness(recorder, tmp_path)
    h.events.append((91, "action", 90, np.full(6, .02, np.float32)))
    h.events.sort(key=lambda e: e[0])
    h.tick(50)
    h.tick(100)
    assert h.session.integrity["violations"][-1]["reason"] == "qp_action_during_home"


@pytest.mark.parametrize("return_home", [False, True])
def test_full_return_option_requires_home_endpoint_at_stop(recorder, tmp_path, return_home):
    def mutate(v, ms):
        if not return_home:
            v["phase"], v["home_generation"] = "QP_TELEOP", 0
        elif ms >= 420:
            v["phase"], v["arrival_verified"] = "HOME_COMPLETE", True
    h = harness(recorder, tmp_path, require_home=True, mutate=mutate)
    h.events = [e for e in h.events if not (e[1] == "action" and e[2] == 445)]
    for ms in range(50, 551, 50):
        h.tick(ms)
    h.session.finish_aligned(h.history.snapshot(MONO+610*MS), h.timing(610))
    assert h.session.integrity["valid"] is return_home
    if not return_home:
        assert h.session.integrity["violations"][-1]["reason"] == "required_home_endpoint_missing"


def test_omitted_context_cannot_bypass_required_home():
    guard = ActionContextGuard(EPOCH, require_home_complete=True)
    with pytest.raises(HomeActionError, match="missing_action_context"):
        guard.update({"items": {}}, EPOCH, finishing=True)


@pytest.mark.parametrize("correction", [False, True])
def test_home_uses_existing_collection_receipts_and_recovery(managed, tmp_path, correction, monkeypatch):
    module, ledger, session = managed
    ledger.prepare()
    h = harness(module, tmp_path, session=session)
    if correction:
        with monkeypatch.context() as patch:
            patch.setattr(module.time, "monotonic_ns", lambda: MONO+30*MS)
            session.mark_correction(SimpleNamespace(received_monotonic_ns=MONO+20*MS,
                received_system_time_ns=EPOCH+20*MS, boundary_source="gui_button_received"))
    for ms in range(50, 551, 50):
        h.tick(ms)
    session.finish_aligned(h.history.snapshot(MONO+610*MS), h.timing(610))
    assert session.integrity["valid"]
    kind = "correction" if correction else "success"
    message = session.save(kind, ledger.roots[kind])
    assert not session.save_failed and session.path is None, message
    assert ledger.completed() == (set() if correction else {108})
    receipt = next((ledger.directory / "receipts").glob("*.json"))
    before = receipt.read_bytes()
    from ai.record.collection import Collection
    ledger.close()
    reopened = Collection(ledger.directory)
    try:
        assert reopened.completed() == (set() if correction else {108})
        assert receipt.read_bytes() == before
    finally:
        reopened.close()


def test_home_sidecar_validator_requires_simulated_contract(recorder, tmp_path):
    from ai.train.validate_dataset import _Validator
    h = harness(recorder, tmp_path)
    h.tick(50)
    h.tick(100)
    row = h.row(1)
    validator = _Validator(tmp_path, None)
    validator.alignment(row, h.session.metadata, 0)
    assert not validator.errors and validator.warnings
    validator.alignment(row, {**h.session.metadata, "home_action_acceptance": "live_locked"}, 0)
    assert validator.errors["integrity"] == 1
    invalid = deepcopy(row)
    invalid["recorded_action"]["source"]["sample"]["simulated"] = False
    invalid["recorded_action"]["action_context"]["sample"]["simulated"] = False
    with pytest.raises(ValueError, match="unverified_home_action"):
        validator.alignment(invalid, h.session.metadata, 0)


@pytest.mark.parametrize("policy", ["act1", "act2"])
def test_independent_policy_example_uses_existing_json_contract(policy):
    from ai.record.plans import read_json, validate_definition
    p = Path(__file__).parents[3] / "docs/examples" / f"{policy}_home.json"
    definition = validate_definition(read_json(p))
    assert definition["action"]["id"] == policy
    assert definition["dataset_id"].startswith(policy + "_")
    assert definition["plan"]["conditions"][0]["fields"]["end"] == "Home"


def feedback(verifier, ms, **overrides):
    t = ms*MS
    return verifier.update(**{**dict(now_ns=t, sample_ns=t, status_ns=t, q_rad=np.zeros(6),
        velocity_rad_s=np.zeros(6), in_motion=False, connected=True, healthy=True), **overrides})


def test_home_arrival_needs_distinct_fresh_stationary_feedback():
    v = HomeArrivalVerifier(np.zeros(6), 0)
    assert feedback(v, 0) == "HOME_SETTLING"
    assert feedback(v, 50, velocity_rad_s=np.ones(6)) == "HOME_MOVING"
    for ms in range(100, 351, 50):
        state = feedback(v, ms)
    assert state == "HOME_COMPLETE"
    assert feedback(v, 400, connected=False) == "HOME_COMPLETE"  # historical event, not ongoing readiness


@pytest.mark.parametrize("fault", ["timeout", "stale", "health", "moving", "far", "repeat", "gap", "cancel"])
def test_home_not_declared_complete_from_ack_or_bad_feedback(fault):
    v = HomeArrivalVerifier(np.zeros(6), 0, timeout_ns=500*MS)
    feedback(v, 0)
    if fault == "timeout": assert feedback(v, 501) == "HOME_FAILED"
    if fault == "stale": assert feedback(v, 100, sample_ns=0) == "HOME_FAILED"
    if fault == "health": assert feedback(v, 50, healthy=None) == "HOME_FAILED"
    if fault == "moving": assert feedback(v, 50, in_motion=True) == "HOME_MOVING"
    if fault == "far": assert feedback(v, 50, q_rad=np.ones(6)) == "HOME_MOVING"
    if fault == "repeat": assert feedback(v, 50, sample_ns=0) == "HOME_SETTLING"
    if fault == "gap": assert feedback(v, 300) == "HOME_SETTLING"
    if fault == "cancel": v.cancel()
    assert v.state != "HOME_COMPLETE"


def test_home_and_task_success_are_independent():
    base = dict(task_running=False, task_failed=False, task_success_verified=False,
                home_complete=True, stop_confirmed=True, authority_released=True)
    assert completion_status(**base) == {"TASK_RUNNING": False, "TASK_FAILED": False,
        "TASK_SUCCESS": False, "HOME_COMPLETE": True, "READY_FOR_NEXT_TASK": False}
    assert completion_status(**{**base, "task_success_verified": True})["READY_FOR_NEXT_TASK"]
    assert not completion_status(**{**base, "task_success_verified": True, "authority_released": False})["READY_FOR_NEXT_TASK"]


def load_diagnostic():
    p = Path(__file__).parents[3] / "scripts/observe_indy7_home_readonly.py"
    spec = importlib.util.spec_from_file_location("home_diagnostic", p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_diagnostic_defaults_do_not_connect_or_overwrite(tmp_path, monkeypatch):
    m = load_diagnostic()
    import builtins
    original = builtins.__import__
    def reject_sdk(name, *args, **kwargs):
        if name == "neuromeka": raise AssertionError("SDK must not be imported")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", reject_sdk)
    assert m.main(["--describe"]) == 0
    with pytest.raises(SystemExit): m.main([])
    existing = tmp_path/"existing.jsonl"
    existing.write_text("protected")
    assert m.main(["--connect-readonly", "--ip", "192.0.2.1", "--approval-record", "fake", "--output", str(existing)]) == 2
    assert existing.read_text() == "protected"


def test_diagnostic_calls_only_getters_and_preserves_raw_values():
    m = load_diagnostic()
    calls = []
    class Fake:
        def __getattr__(self, name):
            assert name in m.GETTERS
            def read():
                calls.append(name)
                return {"q": [10]*6, "qdes": [20]*6, "response": {"code": 0}}
            return read
    counter = iter(range(100, 200))
    row = m.snapshot(Fake(), mono=lambda: next(counter), system=lambda: next(counter))
    assert calls == list(m.GETTERS)
    assert row["controller_sample_timestamp"] is None and row["qdes_unit"] == "unverified"
    assert row["calls"]["get_control_state"]["raw"]["qdes"] == [20]*6
    assert all(call["rpc_elapsed_ns"] > 0 for call in row["calls"].values())


def test_fake_qdes_reader_uses_desired_not_measured_and_converts_once():
    calls = []
    class FakeSDK:
        is_mock = True
        def get_control_state(self):
            calls.append("get_control_state")
            return {"q": [999]*6, "qdes": [10, 20, 30, 40, 50, 60], "response": {"code": 0}}
    ticks = iter([MONO, MONO+2*MS])
    system = iter([EPOCH, EPOCH+2*MS])
    contract = SimulatedHomeContract("fake-only")
    sample = SimulatedQdesReader(FakeSDK(), contract, source_unit="deg",
        mono=lambda: next(ticks), system=lambda: next(system)).read()
    context = envelope(2, 1, "HOME_MOVING", 1)
    context["sample"] = sample
    target = contract.target(context, EPOCH+2*MS, EPOCH+2*MS)
    np.testing.assert_allclose(np.rad2deg(target), [10, 20, 30, 40, 50, 60], rtol=1e-6)
    assert calls == ["get_control_state"] and sample["rpc_elapsed_ns"] == 2*MS
    with pytest.raises(ValueError, match="live_home_action_locked"):
        SimulatedQdesReader(object(), contract, source_unit="rad", mono=None, system=None)


def test_fake_qdes_reader_preserves_failure_without_fallback_to_q():
    class FailedSDK:
        is_mock = True
        def get_control_state(self): return {"q": [1]*6, "response": {"code": 0}}
    sample = SimulatedQdesReader(FailedSDK(), SimulatedHomeContract("fake"), source_unit="rad",
                                mono=lambda: MONO, system=lambda: EPOCH).read()
    assert sample["response_ok"] is False and "target_rad" not in sample
