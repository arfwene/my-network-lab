"""퀴즈 채점과 검사 결과 해석. 정답은 서버 밖으로 내보내지 않는다."""
import json
import sys
import yaml
from pathlib import Path
from jinja2 import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L      # noqa: E402
import db                  # noqa: E402
import docs                # noqa: E402

SRC = L.ROOT / "modules"


def spec(module):
    f = SRC / module["dir"] / "assessment.yml"
    if not f.exists():
        return {}
    return yaml.safe_load(f.read_text(encoding="utf-8")) or {}


def has_quiz(module):
    return bool((spec(module).get("quiz") or {}).get("questions"))


def has_checks(module):
    # 캡스톤은 자기 항목 없이 **앞 모듈의 검사를 가져오기만** 할 수도 있다 (run-checks 의 include).
    # items 만 보면 그런 모듈이 "검사 없음" 으로 취급되어 통과 게이팅이 통째로 빠진다.
    blk = spec(module).get("checks") or {}
    return bool(blk.get("items") or blk.get("include"))


# ------------------------------------------------------------------ 서술형
#  자동 채점이 불가능하거나 부적절한 과제만 여기로 온다.
#  정책 (PLAN 6.7):
#    review: optional  비차단 — 제출하면 다음 모듈로 넘어간다. 관리자는 나중에 몰아서 본다
#    review: required  차단  — 관리자 승인이 있어야 통과 (캡스톤)
#  루브릭을 전부 충족하면 optional 항목은 **자동 통과** 처리되어 검토 큐에 뜨지 않는다.
#  관리자 업무를 줄이는 핵심 장치다 — 사람은 "자동으로 못 거른 것"만 본다.
def written_items(module, lab_id=None):
    items = (spec(module).get("written") or {}).get("items") or []
    if lab_id is None:
        return items
    ctx = L.doc_context(lab_id, module["stage"])
    out = []
    for it in items:
        it = dict(it)
        it["prompt"] = Template(it.get("prompt", "")).render(**ctx)
        out.append(it)
    return out


def has_written(module):
    return bool(written_items(module))


def grade_written(item, body):
    """루브릭 자동 사전검토. 사람의 판단을 대신하지 않고 **훑는 시간을 줄인다.**"""
    text = (body or "").strip()
    rubric = item.get("rubric") or []
    detail = []
    for r in rubric:
        words = r.get("any") or []
        hit = any(_norm(w) in _norm(text) for w in words) if words else True
        detail.append({"key": r.get("key", ""), "desc": r.get("desc", ""), "ok": hit,
                       "any": words})
    hit = sum(1 for d in detail if d["ok"])
    min_chars = int(item.get("min_chars", 0))
    return {"hit": hit, "total": len(detail), "detail": detail,
            "chars": len(text), "min_chars": min_chars,
            "long_enough": len(text) >= min_chars,
            "full": bool(text) and hit == len(detail) and len(text) >= min_chars}


def written_status(item, grade):
    """제출 직후의 상태. 관리자 큐에 올릴지 여기서 갈린다."""
    if item.get("review") == "required":
        return "submitted"                       # 사람이 반드시 본다
    if item.get("auto_pass", True) and grade["full"]:
        return "auto_ok"                         # 루브릭을 다 채웠다 — 검토 불필요
    return "submitted"


def quiz_for_client(module, lab_id):
    """정답(answer)과 해설(explain)을 뺀 문항만 내보낸다."""
    q = spec(module).get("quiz") or {}
    ctx = L.doc_context(lab_id, module["stage"])
    out = []
    for it in q.get("questions", []):
        out.append({
            "id": it["id"], "type": it["type"],
            "text": Template(it["text"]).render(**ctx),
            "choices": [Template(c).render(**ctx) for c in it.get("choices", [])],
        })
    return {"pass_score": q.get("pass_score", 80), "questions": out}


def _norm(s):
    return " ".join(str(s or "").strip().lower().split())


def grade_quiz(module, lab_id, answers):
    """answers: {질문id: [값...]}  →  점수와 문항별 결과."""
    q = spec(module).get("quiz") or {}
    ctx = L.doc_context(lab_id, module["stage"])
    detail, correct = [], 0
    for it in q.get("questions", []):
        given = answers.get(it["id"], [])
        if it["type"] == "single":
            ok = len(given) == 1 and str(given[0]).isdigit() and int(given[0]) in it["answer"]
        elif it["type"] == "multi":
            try:
                ok = {int(g) for g in given} == set(it["answer"])
            except ValueError:
                ok = False
        else:  # short
            ok = bool(given) and any(_norm(given[0]) == _norm(a) for a in it["answer"])
        correct += 1 if ok else 0
        detail.append({
            "id": it["id"], "ok": ok,
            "text": Template(it["text"]).render(**ctx).strip().splitlines()[0][:90],
            "explain": Template(it.get("explain", "")).render(**ctx).strip(),
            "given": given,
        })
    total = len(q.get("questions", []))
    score = round(correct / total * 100) if total else 100
    return {"score": score, "correct": correct, "total": total,
            "pass_score": q.get("pass_score", 80),
            "passed": score >= q.get("pass_score", 80), "items": detail}


def checks_result_path(lab_id, module_id):
    return L.ROOT / f"var/state/checks-lab{lab_id}-{module_id}.json"


def read_checks_result(lab_id, module_id):
    p = checks_result_path(lab_id, module_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ------------------------------------------------------------------ 진도·잠금
OK_STATUS = ("auto_ok", "approved")


def module_state(username, module, is_admin=False):
    """모듈 하나의 진행 상태."""
    pr = db.get_progress(username, module["id"]) or {}
    need_quiz, need_checks = has_quiz(module), has_checks(module)
    items = written_items(module)
    subs = db.latest_submissions(username, module["id"]) if items else {}

    quiz_ok = bool(pr.get("quiz_passed")) or not need_quiz
    checks_ok = bool(pr.get("checks_passed")) or not need_checks

    # 비차단 원칙: 서술형은 "냈는가"만 본다. 승인이 필요한 항목만 승인까지 본다.
    submitted_ok = all(i["id"] in subs for i in items)
    must = [i for i in items if i.get("review") == "required"]
    approved_ok = all(subs.get(i["id"], {}).get("status") in OK_STATUS for i in must)
    awaiting = [i for i in must if subs.get(i["id"], {}).get("status") == "submitted"]
    rejected = [i for i in items
                if subs.get(i["id"], {}).get("status") == "changes_requested"]

    return {
        "quiz_passed": bool(pr.get("quiz_passed")), "checks_passed": bool(pr.get("checks_passed")),
        "need_quiz": need_quiz, "need_checks": need_checks,
        "need_written": bool(items), "need_review": bool(must),
        "written": subs,
        "written_submitted": submitted_ok,
        "written_approved": approved_ok,
        "awaiting_review": [i["title"] for i in awaiting],
        "changes_requested": [i["title"] for i in rejected],
        "complete": bool(pr.get("passed_at")) or (
            quiz_ok and checks_ok and submitted_ok and approved_ok
            and (need_quiz or need_checks or items)),
        "best_score": pr.get("best_score", 0), "tries": pr.get("tries", 0),
        "passed_at": pr.get("passed_at"),
    }


def unlocked_modules(username, is_admin=False):
    """앞 모듈을 통과해야 다음 모듈이 열린다. 관리자는 전부 열려 있다."""
    mods = docs.modules()
    out, prev_done = {}, True
    for m in mods:
        st = module_state(username, m, is_admin)
        out[m["id"]] = is_admin or prev_done
        prev_done = st["complete"]
    return out


def submit_written(username, lab_id, module, answers):
    """answers: {item_id: 본문}. 저장하고 항목별 상태를 돌려준다."""
    out = []
    for it in written_items(module, lab_id):
        body = (answers.get(it["id"]) or "").strip()
        if not body:
            continue
        g = grade_written(it, body)
        st = written_status(it, g)
        db.add_submission(username, lab_id, module["id"], it["id"], body,
                          status=st, auto=g)
        out.append({"id": it["id"], "title": it["title"], "status": st, "auto": g,
                    "review": it.get("review", "optional")})
    return out


def sync_progress(username, lab_id, module, quiz=None, checks=None):
    """채점 결과를 이력과 진도에 반영한다."""
    st_before = module_state(username, module)
    if quiz is not None:
        db.record_attempt(username, lab_id, module["id"], "quiz", quiz["score"],
                          quiz["correct"], quiz["total"], quiz["passed"], quiz["items"])
        db.update_progress(username, module["id"], quiz_passed=quiz["passed"],
                           score=quiz["score"], bump_try=True)
    if checks is not None:
        db.record_attempt(username, lab_id, module["id"], "checks",
                          round(checks["ok"] / checks["total"] * 100) if checks["total"] else 100,
                          checks["ok"], checks["total"], checks["passed"], checks["items"])
        db.update_progress(username, module["id"], checks_passed=checks["passed"])
    st = module_state(username, module)
    if st["complete"] and not st_before["complete"]:
        db.update_progress(username, module["id"], module_complete=True)
    return module_state(username, module)
