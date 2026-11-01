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
    if reason == "missing_causal_observation":
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
    progress = (f"정상 시연  {stats['success']} / 108     교정 시연  {stats['correction']}     "
                f"진행률  {stats['success'] / 108 * 100:.0f}%") if stats is not None else "수집 현황 확인 중"
    mode = "교정 시연" if view.get("demonstration_type") == "correction" and view.get("pending") else "일반 시연"
    if mode == "교정 시연":
        mode += " · 마커 대응 대기" if (view.get("correction") or {}).get("start_frame_index") is None else " · 마커 기록됨"
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
    notice = ("정상 108개 수집을 완료했습니다." if state == "READY" and stats and stats["success"] == 108
              else DEFAULT_NOTICE[state])
    return {"progress": progress, "state": name, "state_color": color,
            "elapsed": recording_clock(view.get("elapsed_s", 0)), "mode": mode,
            "target_note": target, "default_notice": notice, "alert": alert}
