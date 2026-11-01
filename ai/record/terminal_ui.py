"""Append-only Korean terminal presentation. No ROS, Dataset or device imports."""
import json

FOOTER = "====================================="
STATUS = {"READY": "녹화 대기", "PREPARED": "시작 검사 대기", "RECORDING": "RECORDING",
          "REVIEW": "저장 검토", "INVALID": "INVALID", "SAVING": "저장 확인 중",
          "SUCCESS": "정상 저장", "CORRECTION": "교정 저장", "FAILURE": "과거 실패 저장",
          "DISCARD": "폐기", "INTERRUPTED": "중단"}


def panel(title, lines):
    return "\n".join([f"========== [{title}] ==========", "", *lines, FOOTER])


def counts(collection):
    if collection is None:
        return []
    stats = collection.stats()
    return [f"정상 시연 : {stats['success']} / {collection.total}", f"교정 시연 : {stats['correction']}",
            f"남은 조건 : {collection.total-stats['success']}"]


def condition(collection, title="현재 수집 조건", with_status=True):
    view = collection.condition_view()
    if view is None:
        return panel(title, [f"{collection.total}개 정상 시연 수집을 모두 완료했습니다."])
    lines = [f"수집 순서 : {view['collection_order']:03d} / {collection.total}",
             f"조건 ID   : {view['condition_id']:03d}", f"시도      : {view['attempt_number']}회차", "",
             f"바구니 위치 : {view['position']}", f"바구니 회전 : {view['yaw']}", f"바구니 깊이 : {view['depth']}", ""]
    if collection.custom:
        lines = [f"수집 순서 : {view['collection_order']:03d} / {collection.total}",
                 f"조건 ID   : {view['condition_id']:03d}", f"시도      : {view['attempt_number']}회차",
                 view['description'], json.dumps(view['fields'], ensure_ascii=False), ""]
    if with_status:
        lines.extend([f"[현재 상태] {STATUS.get(view['status'], view['status'])}", ""])
    lines.append("조건에 맞게 준비 후 s" if collection.custom else "바구니 배치 후 s")
    return panel(title, lines)


def startup(collection):
    lines = ["========== INDY7 ACT RECORDER ==========", ""]
    if collection:
        lines.extend(["[수집 현황]", *counts(collection), "", condition(collection)])
    else:
        lines.extend(["독립 Recorder 모드", "[현재 상태] 녹화 대기", "", "배치 후 s", FOOTER])
    return "\n".join(lines)


def identity(session):
    context = session.metadata.get("collection") or {}
    return [f"조건 ID : {context['condition_id']:03d}" if "condition_id" in context else "조건 ID : 미지정 (독립 모드)",
            f"시도    : {context['attempt_number']}회차" if "attempt_number" in context else "시도    : 미지정"]


def kind(session):
    return "교정 시연" if session.demonstration_type == "correction" else "일반 시연"


def started(session):
    return "[시작 검사] 통과\n\n" + panel("녹화 시작", [*identity(session), f"유형    : {kind(session)}",
                                                  "상태    : RECORDING", "", "r : 녹화 종료", "x : 교정 시연 전환"])


def correction(session):
    marker = session.correction
    index = marker["start_frame_index"]
    point = "기록 프레임 대응 대기" if index is None else f"{index}프레임 ({marker['start_dataset_timestamp_s']:.2f}초)"
    return panel("교정 시연 전환", [*identity(session), "유형    : 일반 → 교정", "",
                                 f"교정 시작 : {point}", "녹화 상태 : RECORDING", "", "r : 녹화 종료"])


def review(session):
    if not session.integrity["valid"]:
        return invalid(session)
    duration = None if session.stopped_ns is None else (session.stopped_ns-session.started_ns)/1e9
    lines = [identity(session)[0], f"유형    : {kind(session)}", "", f"녹화 프레임 : {session.frames}",
             f"녹화 시간   : {duration:.2f}초" if duration is not None else "녹화 시간   : 미확정"]
    data_status = "정상" if session.frames >= 2 else "프레임 부족 (저장 불가)"
    if session.demonstration_type == "correction":
        index = session.correction["start_frame_index"]
        lines.append(f"교정 시작   : {index}프레임" if index is not None else "교정 시작   : 대응 프레임 없음")
        if index is None:
            data_status = "교정 구간 없음 (저장 불가)"
    lines.extend([f"데이터 상태 : {data_status}",
                  f"저장 대상   : {'Correction' if session.demonstration_type == 'correction' else 'Success'} Dataset",
                  "", "o : 저장", "d : 폐기"])
    return panel("녹화 종료", lines)


def saved(session, outcome, index, condition_id, leftover=None):
    lines = [f"조건 ID : {condition_id:03d}" if condition_id is not None else "조건 ID : 미지정 (독립 모드)",
             f"Dataset : {'Correction' if outcome == 'correction' else 'Success'}", f"Episode : {index}"]
    if session.collection:
        stats = session.collection.stats()
        lines.extend(["", f"정상 시연 : {stats['success']} / {session.collection.total}"])
        if outcome == "correction":
            lines.append(f"교정 시연 : {stats['correction']}")
    result = panel("저장 완료", lines)
    if leftover:
        result += f"\n[정리 경고] 저장은 확인했으나 staging이 남아 있습니다: {leftover}"
    if session.collection:
        title = "재수집 조건" if outcome == "correction" else "다음 수집 조건"
        result += "\n\n" + condition(session.collection, title, with_status=False)
    return result


def recovery_command(collection, pending_root):
    if collection:
        import shlex
        directory = getattr(collection, "directory", collection)
        return f".venv-act/bin/python -m ai.record.collection reconcile --collection-dir {shlex.quote(str(directory))}"
    return f"자동 재저장하지 말고 Dataset과 Pending을 대조하세요. Pending 루트: {pending_root}"


def save_error(session, error):
    return panel("저장 오류 - SAVE FAILED", [f"오류 : {error}", f"Pending : {session.path}",
             "", "복구 : " + recovery_command(session.collection, session.pending_root), "", "q : 종료"])


def invalid(session):
    violation = session.integrity["violations"][-1] if session.integrity["violations"] else {}
    details = violation.get("details") or {}
    lines = [*identity(session), "", f"오류 코드 : {violation.get('reason', 'unknown')}"]
    if details.get("topic"):
        lines.append(f"대상      : {'robot_state' if details['topic'] == 'state' else details['topic']}")
    if "limit_ns" in details:
        lines.append(f"허용 지연 : {details['limit_ns']/1e6:g}ms")
    if "age_ns" in details:
        lines.append(f"실제 지연 : {details['age_ns']/1e6:g}ms")
    lines.extend(["진단 정보 : " + json.dumps(violation, ensure_ascii=False), "", f"Pending : {session.path}",
                  "", "d : 폐기", "q : 종료"])
    return panel("녹화 오류 - INVALID", lines)


def help_text():
    return panel("명령어 안내", ["s : 배치·시도 확정 및 녹화 시작", "x : 녹화 중 교정 시연 전환", "r : 녹화 종료 및 REVIEW",
        "o : 유형에 맞는 Dataset 저장 확정", "d : 시연 폐기", "q : 종료", "progress : 수집 현황",
        "progress all : 전체 계획 조건", "history ID : 조건별 시도 요약", "history ID detail : 세부 장부 JSON",
        "condition ID : 미완료 조건 선택", "placement key=value : 선택적 좌표 입력 (cm, degree; 미입력=null)",
        "result key=value : REVIEW 결과·품질·단계별 메모", "help : 명령어 안내", "guide : 배치 기준 안내"])


def guide():
    return panel("배치 기준", ["선반을 위에서 내려다보는 기준입니다. 0°는 바구니가 선반 정면과 평행한 자세입니다.",
        "선반 입구는 바깥쪽, 선반 안쪽은 벽 쪽입니다.",
        "시계방향(-): 바구니 왼쪽 끝이 벽 쪽, 오른쪽 끝이 입구 쪽으로 이동합니다.",
        "반시계방향(+): 바구니 오른쪽 끝이 벽 쪽, 왼쪽 끝이 입구 쪽으로 이동합니다.",
        "A1/A2/A3: 시계방향 많이/보통/조금, A4/A5/A6: 반시계방향 조금/보통/많이",
        "Depth는 입구 쪽에서 벽 쪽으로 구분하는 분포 기준이며 고정 칸이 아닙니다.",
        "좌표 입력은 선택 사항이며 미입력 값은 null로 기록합니다. 검증된 범위 안에서 직접 배치하세요."])
