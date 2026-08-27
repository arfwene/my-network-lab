"""퀴즈 채점과 검사 결과 해석. 정답은 서버 밖으로 내보내지 않는다."""
import ipaddress
import json
import re
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


# 퀴즈 통과 기준. 모듈이 assessment.yml 에 적어 두면 그 값이 이긴다.
#   100 = 다 맞혀야 넘어간다. 개념 확인 문항이라 "대충 알면 통과" 를 두지 않는다.
PASS_SCORE = 100

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


def has_drill(module):
    """이 모듈에 장애 시나리오가 있는가.

    있으면 **장애 실습이 필수**다. 검사만 통과하는 것으로는 부족하다 —
    검사는 "랩이 정상인가" 를 보는데 그건 [이 모듈 적용] 직후에도 참이라
    아무것도 안 해도 통과한다. 한 번은 망가뜨려 보고 되살려야 한다.
    """
    return bool(list((L.ROOT / "scenarios").glob(f"{module['id']}-*.yml")))


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
    return {"pass_score": q.get("pass_score", PASS_SCORE), "questions": out}


def _norm(s):
    """단답형 비교용 정규화.

    공백을 **전부** 없앤다. 남겨 두면 `경로 요약` 은 맞고 `경로요약` 은 틀린다 —
    맞는 답을 띄어쓰기로 떨어뜨리는 셈이다. 양쪽 다 지우므로 `ip route` 같은
    답도 서로 같게 비교된다.

    끝에 붙은 마침표·느낌표도 뗀다. 문장 습관이지 답의 일부가 아니다.
    """
    s = str(s or "").lower()
    s = "".join(s.split())
    return s.rstrip(".!?。")


_MAC = re.compile(r"(?:[0-9a-f]{2}([:-])[0-9a-f]{2}(?:\1[0-9a-f]{2}){4}"
                  r"|[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\Z", re.I)


def _mac_key(s):
    """MAC 으로 읽히면 16진수 12자리. 아니면 None."""
    s = str(s or "").strip()
    return re.sub(r"[^0-9a-f]", "", s.lower()) if _MAC.match(s) else None


def _addr_key(s):
    """주소로 읽히면 (주소, 접두길이 or None). 아니면 None."""
    body, _, pref = str(s or "").strip().partition("/")
    try:
        a = ipaddress.ip_address(body)
    except ValueError:
        return None
    if not pref:
        return (a, None)
    if not pref.isdigit() or int(pref) > a.max_prefixlen:
        return None
    return (a, int(pref))


def _same(given, want, notation=False):
    """단답형 한 건 비교.

    주소는 **글자가 아니라 주소로** 견준다. `fe80::5054:ff:fe12:1` 과
    `fe80::5054:ff:fe12:0001` 은 같은 주소인데 글자로는 다르다 — 맞게 적고도
    틀리는 자리였다. MAC 도 `:` `-` `.` 어느 표기든 받는다.
    접두 길이는 **정답이 달고 있을 때만** 따진다 (`10.30.8.0/22` 를 물었으면
    `/22` 까지 맞아야 하고, 주소만 물었으면 붙여 적어도 넘어간다).

    notation 이 참인 문항은 표기 자체를 묻는다(가장 짧게 축약하라).
    거기서 주소로 견주면 문제가 사라지므로 글자 그대로 비교한다.
    """
    if _norm(given) == _norm(want):
        return True
    if notation:
        return False
    mw = _mac_key(want)
    if mw:
        return _mac_key(given) == mw
    aw = _addr_key(want)
    if aw:
        ag = _addr_key(given)
        return bool(ag) and ag[0] == aw[0] and (aw[1] is None or ag[1] == aw[1])
    return False


def _snip(s, n=90):
    """결과 목록에 쓸 한 줄 요약.

    지문에는 교재와 같은 표기(`**굵게**`, 백틱 코드)가 들어 있다. 그냥 잘라
    내면 여는 표시만 남은 채 끝나 화면에 별 두 개가 그대로 보인다 —
    화면이 마크다운 원문을 노출하는 그 증상이다. 잘린 표시는 닫아 준다.
    """
    s = (s or "").strip().splitlines()
    s = s[0] if s else ""
    cut = len(s) > n
    if cut:
        s = s[:n].rstrip()
    if s.count("`") % 2:
        s += "`"
    if s.count("**") % 2:
        s += "**"
    return s + "…" if cut else s


def _picked(it, given, ctx):
    """교육생이 고른 답을 사람이 읽을 수 있는 문장으로. 정답은 담지 않는다."""
    if it["type"] in ("single", "multi"):
        out = []
        for g in given:
            try:
                out.append(Template(str(it["choices"][int(g)])).render(**ctx).strip())
            except (ValueError, IndexError, KeyError):
                continue
        return out
    return [str(g).strip() for g in given if str(g).strip()]


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
            # 정답도 랩 값으로 렌더한다. 단답형 답이 주소인 문항 —
            # "web 의 IP 는?" — 은 랩마다 답이 다르므로 `{{ ip.web }}` 로 적어야 하는데,
            # 지문만 렌더하고 정답을 원문 그대로 비교하면 아무도 맞힐 수 없다.
            want = [Template(str(a)).render(**ctx) for a in it["answer"]]
            ok = bool(given) and any(_same(given[0], a, it.get("notation"))
                                     for a in want)
        correct += 1 if ok else 0
        # 해설(explain)은 **여기서 내보내지 않는다.**
        #  해설은 정답을 그대로 말한다 ("UP 인데 NO-CARRIER 면 …"). 틀린 문항에
        #  해설을 바로 붙여 주면 교육생은 교재로 돌아가지 않고 해설만 읽고 다시
        #  제출한다 — 통과는 하지만 배우지는 않는다. 통과 기준이 100점이라
        #  이 지름길은 특히 매력적이다.
        #  틀렸다는 사실과 **자기가 고른 답**만 돌려준다. 무엇이 맞는지는
        #  교재에서 찾아야 한다. 해설은 [해설] 탭에 그대로 있고 관리자만 본다.
        detail.append({
            "id": it["id"], "ok": ok,
            "text": _snip(Template(it["text"]).render(**ctx)),
            "given": given,
            "given_text": _picked(it, given, ctx),
        })
    total = len(q.get("questions", []))
    score = round(correct / total * 100) if total else 100
    return {"score": score, "correct": correct, "total": total,
            "pass_score": q.get("pass_score", PASS_SCORE),
            "passed": score >= q.get("pass_score", PASS_SCORE), "items": detail}


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


def quiz_answers_md(module, lab_id):
    """[해설] 탭에 붙일 퀴즈 정답·해설 (마크다운).

    **손으로 적지 않는다.** answers.md.j2 에 옮겨 적으면 문항을 고칠 때마다
    두 곳을 고쳐야 하고, 한 곳만 고치면 강사가 틀린 답을 들고 서게 된다.
    assessment.yml 하나에서 그때그때 만든다.
    """
    q = spec(module).get("quiz") or {}
    items = q.get("questions") or []
    if not items:
        return ""
    ctx = L.doc_context(lab_id, module["stage"])

    def r(s):
        return Template(str(s)).render(**ctx)

    out = ["", "---", "", "## 퀴즈 정답 · 해설", "",
           f"{len(items)}문항 · 통과 기준 {q.get('pass_score', PASS_SCORE)}점. "
           "**교육생 화면에는 정답도 해설도 나오지 않습니다** — 틀린 문항과 "
           "자기가 고른 답까지만 보입니다.", ""]
    for i, it in enumerate(items, 1):
        kind = {"single": "택1", "multi": "복수 선택", "short": "단답"}.get(it["type"], it["type"])
        out.append(f"### {i}. ({kind}) {' '.join(r(it['text']).split())}")
        out.append("")
        if it["type"] == "short":
            got = " · ".join(f"`{r(a)}`" for a in it["answer"])
            out.append(f"**정답** {got}")
            out.append("")
            out.append("> 주소·MAC 은 표기가 달라도 같은 값이면 맞게 칩니다 "
                       "(`fe80::…:1` 과 `…:0001`). 표기 자체를 묻는 문항만 글자로 봅니다.")
        else:
            for j, c in enumerate(it["choices"]):
                mark = "**✔**" if j in it["answer"] else "　　"
                out.append(f"- {mark} {r(c)}")
        out.append("")
        if it.get("explain"):
            out.append("**해설** — " + " ".join(r(it["explain"]).split()))
            out.append("")
    return "\n".join(out)


def _checkpoint_for(stage):
    """그 단계에 중간 점검이 걸려 있는가. exam 과 서로 import 하므로 늦게 가져온다."""
    import exam
    return exam.checkpoint_for(stage)


def module_state(username, module, is_admin=False):
    """모듈 하나의 진행 상태."""
    pr = db.get_progress(username, module["id"]) or {}
    need_quiz, need_checks = has_quiz(module), has_checks(module)
    items = written_items(module)
    subs = db.latest_submissions(username, module["id"]) if items else {}

    need_drill = has_drill(module)
    quiz_ok = bool(pr.get("quiz_passed")) or not need_quiz
    checks_ok = bool(pr.get("checks_passed")) or not need_checks
    drill_ok = bool(pr.get("drill_passed")) or not need_drill

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
        "need_drill": need_drill, "drill_passed": bool(pr.get("drill_passed")),
        # 이 모듈의 장애 실습이 **중간 점검으로만** 인정되는가 (M3 · M6)
        "drill_checkpoint": bool(_checkpoint_for(module.get("stage"))),
        "need_written": bool(items), "need_review": bool(must),
        "written": subs,
        "written_submitted": submitted_ok,
        "written_approved": approved_ok,
        "awaiting_review": [i["title"] for i in awaiting],
        "changes_requested": [i["title"] for i in rejected],
        "complete": bool(pr.get("passed_at")) or (
            quiz_ok and checks_ok and drill_ok and submitted_ok and approved_ok
            and (need_quiz or need_checks or need_drill or items)),
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
