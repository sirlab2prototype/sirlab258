"""Operator-facing text/colors only. No engine, ROS, file or state mutation."""
import re

STATUS = {
    "INITIALIZING": ("장부 확인 중", "#64748b"),
    "READY": ("녹화 대기", "#64748b"),
    "PRESTART": ("시작 검사 중", "#9a5b00"),
    "RECORDING": ("녹화 중", "#087443"),
    "REVIEW": ("검토 중", "#9a5b00"),
    "SAVING": ("저장 중", "#2454cc"),
    "INVALID": ("오류 · INVALID", "#b4232c"),
    "ERROR": ("오류 · 복구 필요", "#b4232c"),
    "CLOSED": ("Recorder 종료", "#64748b"),
}
DEFAULT_NOTICE = {
    "INITIALIZING": "수집 장부를 확인하고 있습니다.",
    "READY": "바구니 배치 후 녹화를 시작하세요.",
    "PRESTART": "녹화 시작에 필요한 관측 데이터를 확인하고 있습니다.",
    "RECORDING": "녹화 중입니다.",
    "REVIEW": "녹화를 종료했습니다. 시연 결과를 확인하세요.",
    "SAVING": "시연을 저장하고 무결성을 확인하고 있습니다.",
    "INVALID": "데이터 무결성 오류입니다. 저장할 수 없습니다.",
    "ERROR": "수집이 차단되었습니다. 오류 상세를 확인하세요.",
    "CLOSED": "Recorder를 종료했습니다.",
}


def recording_clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def fault_text(reason, topic=None, *, starting=False):
    target = {"camera_317222074298": "Camera A 영상", "camera_844212071939": "Camera B 영상",
              "state": "로봇 관측 상태", "robot_state": "로봇 관측 상태"}.get(topic, "관측 데이터")
    context_errors = {
        "missing_action_context": "QP 수집 상태(/capstone/action_context)를 수신하지 못했습니다. QP와 Recorder의 실행 버전을 확인하세요.",
        "missing_initial_action_context": "녹화 시작 기준의 QP 수집 상태가 없습니다. 같은 버전의 QP 상태 수신을 확인하세요.",
        "stale_action_context": "QP 수집 상태 갱신이 중단되었거나 지연되었습니다.",
        "action_context_gap": "QP 수집 상태 메시지가 누락되었습니다. 연결과 실행 버전을 확인하세요.",
        "action_context_publisher_changed": "녹화 중 QP 발행 프로세스가 변경되었습니다.",
        "invalid_action_context": "QP 수집 상태 형식이 맞지 않습니다. QP와 Recorder의 실행 버전을 확인하세요.",
        "action_context_protocol_mismatch": "QP 수집 상태의 프로토콜 버전이 맞지 않습니다. 같은 프로젝트 버전인지 확인하세요.",
        "unverified_home_action": "미검증 Home 구간이 포함되어 저장할 수 없습니다. 기존 수집은 Home 전에 녹화를 종료하세요.",
        "live_home_action_locked": "실물 Home Action 수집은 검증 전 잠금 상태입니다.",
        "recording_started_during_home": "Home 처리 중에는 기존 시연 녹화를 시작할 수 없습니다.",
        "home_failed_or_cancelled": "Home 실패·취소 또는 미검증 상태가 포함되어 저장할 수 없습니다.",
        "recording_ended_before_home_complete": "Home 완료가 확인되기 전에 녹화가 종료되어 저장할 수 없습니다.",
        "required_home_endpoint_missing": "필수 Home 종료 조건이 확인되지 않아 저장할 수 없습니다.",
        "qp_action_during_home": "Home 중 QP 목표가 함께 수신되어 저장할 수 없습니다.",
    }
    if reason in context_errors:
        text = context_errors[reason]
    elif reason == "missing_causal_observation":
        text = f"{target}의 시작 기준 데이터를 확인하지 못했습니다."
    elif reason == "observation_too_old":
        text = f"{target}이 허용 지연을 초과했습니다."
    elif reason == "no_initial_state":
        text = "로봇의 초기 관측 상태를 확인하지 못했습니다."
    elif reason == "unknown_initial_gripper":
        text = "그리퍼의 초기 상태를 확인하지 못했습니다."
    else:
        text = f"관측·시간 검사에 실패했습니다 ({reason})."
    return text + (" 녹화를 시작할 수 없습니다." if starting else "")


def message_notice(message):
    """Return (one-line notice, is_error), or None for reports/help/diagnostics.

    Camera absence alone never generates an alert. Interpret actual operation
    responses only; keep their original text separately for full diagnostics.
    """
    if "[저장 완료]" in message and "[정리 경고]" in message:
        return "시연은 저장됐지만 Pending 정리 경고가 있습니다. 상세 정보를 확인하세요.", True
    if message.startswith(("[미저장 시연]", "[복구 기록]")):
        return "이전 시연의 Pending 보존·복구 안내가 있습니다. 상세 정보를 확인하세요.", True
    if message.startswith("[시작 검사] 취소:") or message.startswith("[시작 검사] 실패:"):
        tail = message.split(":", 1)[1].strip()
        words = tail.split(":", 1)[0].split()
        reason = words[0] if words else "unknown"
        if reason.startswith("user_") or reason in ("input_eof", "recorder_exit"):
            return "녹화 시작 검사를 취소했습니다.", False
        match = re.search(r"['\"]topic['\"]\s*:\s*['\"]([^'\"]+)", message)
        return fault_text(reason, match[1] if match else None, starting=True), True
    if "[저장 완료]" in message:
        correction = re.search(r"Dataset\s*:\s*Correction\b", message)
        return ("교정 시연이 저장되었습니다." if correction else "정상 시연이 저장되었습니다."), False
    if message.startswith("[폐기 완료]"):
        return "시연을 폐기했습니다.", False
    if "[교정 시연 전환]" in message:
        return "교정 시점을 표시했습니다. 녹화를 계속합니다.", False
    if "[녹화 종료]" in message:
        return DEFAULT_NOTICE["REVIEW"], False
    if "[녹화 시작]" in message:
        return DEFAULT_NOTICE["RECORDING"], False
    if message.startswith("[시작 검사] 관측"):
        return DEFAULT_NOTICE["PRESTART"], False
    if message.startswith(("[좌표 기록]", "[결과 기록]")):
        return "입력한 배치·메모를 반영했습니다.", False
    if any(title in message for title in ("[저장 오류 - SAVE FAILED]", "[수집 차단 - Collection Blocked]", "[시작 차단]")):
        return "수집·저장 오류가 발생했습니다. 오류 상세의 경로와 복구 안내를 확인하세요.", True
    if "[녹화 오류 - INVALID]" in message:
        return DEFAULT_NOTICE["INVALID"], True
    if message.startswith(("[Recorder 오류]", "[수집 안내 오류]", "[시작 거부]", "[저장 거부]", "[저장 차단]", "[폐기 차단]", "[입력 거부]")):
        return " ".join(message.splitlines())[:150], True
    if message.startswith("[종료 보류]"):
        return "미저장 시연을 저장하거나 폐기한 뒤 종료하세요.", False
    return None


def operator_fields(view):
    state = view["state"]
    name, color = STATUS[state]
    stats = view.get("stats")
    total = view.get("total", 108) or 108
    progress = (f"정상 시연  {stats['success']} / {total}     교정 시연  {stats['correction']}     "
                f"진행률  {stats['success'] / total * 100:.0f}%") if stats is not None else "수집 현황 확인 중"
    mode = "교정 시연" if view.get("demonstration_type") == "correction" and view.get("pending") else "일반 시연"
    if mode == "교정 시연":
        mode += " · 마커 대응 대기" if (view.get("correction") or {}).get("start_frame_index") is None else " · 마커 기록됨"
    if view.get("home_capture"):
        mode += " · Home 포함"
    target = ""
    if state in ("REVIEW", "SAVING"):
        target = "저장 대상 · " + ("Correction" if view.get("demonstration_type") == "correction" else "Success")
    alert = ""
    if state == "INVALID":
        violations = view.get("integrity", {}).get("violations", [])
        last = violations[-1] if violations else {}
        alert = fault_text(last.get("reason", "unknown"), (last.get("details") or {}).get("topic"))
    elif state == "ERROR":
        alert = DEFAULT_NOTICE[state]
    notice = (f"정상 {total}개 수집을 완료했습니다." if state == "READY" and stats and stats["success"] == total
              else DEFAULT_NOTICE[state])
    if view.get("custom_plan") and state == "READY" and (not stats or stats["success"] != total):
        notice = "계획 조건에 맞게 준비 후 녹화를 시작하세요."
    if view.get("home_capture"):
        if state=="READY":notice += " Home 복귀까지 녹화한 뒤 종료하세요."
        if state=="RECORDING" and view.get("home_phase") in ("HOME_REQUESTED","HOME_MOVING","HOME_SETTLING"):
            notice="Home 복귀 구간을 녹화하고 있습니다."
        if state=="RECORDING" and view.get("home_phase")=="HOME_COMPLETE":
            notice="Home 도착을 확인했습니다. 녹화를 종료하고 실제 작업 결과를 확인하세요."
    return {"progress": progress, "state": name, "state_color": color,
            "elapsed": recording_clock(view.get("elapsed_s", 0)), "mode": mode,
            "target_note": target, "default_notice": notice, "alert": alert}
