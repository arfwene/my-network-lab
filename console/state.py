"""랩별 진행 상태 — 어느 단계까지 적용했고, 무엇이 검증을 통과했는가."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L

# dist/ 는 make clean 대상이다. 진행 상태는 재생성할 수 없으므로 var/ 에 둔다.
DIR = L.ROOT / "var/state"


def _path(lab_id):
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        DIR.parent.chmod(0o700)      # var/ 는 계정 DB 도 들어 있다
    except OSError:
        pass
    return DIR / f"lab{lab_id}.json"


def load(lab_id):
    p = _path(lab_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"lab_id": lab_id, "stage": None, "applied": [], "verified": [],
            "last_job": None, "broken": []}


def save(lab_id, st):
    _path(lab_id).write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def stage_gap(lab_id, module_stage):
    """이 모듈을 하려면 랩이 어느 단계여야 하는가 — 지금 상태와 비교한다.

    설정은 누적이다(m4 를 적용하면 m1~m4 가 다 들어간다). 그래서 "적용 목록에
    있는가"가 아니라 "지금 단계가 어디까지 왔는가"로 본다.

      behind — 랩이 모자란다. [이 모듈 적용] 을 누르면 그 단계까지 올라간다.
      ok     — 맞다.
      ahead  — 랩이 앞서 있다. 되돌려야 실습 결과가 교재와 맞는다.

    site.yml 만 다시 올리는 것으로는 뒤로 갈 수 없다. node_active 가 거짓인 노드는
    역할이 통째로 건너뛰어지는데, 건너뛴다는 것은 "되돌린다"가 아니라 "손대지 않는다"라서
    netplan 만 되돌아가고 FRR·nftables 는 앞 단계 그대로 남는다. 그래서 [이 모듈 적용]
    버튼은 site.yml 이 아니라 reset.yml 을 부른다 (흔적을 지운 뒤 site.yml 을 import 한다).
    """
    now = load(lab_id).get("stage")
    if module_stage not in L.STAGES:
        return {"dir": "ok", "now": now, "need": module_stage}
    if now not in L.STAGES:
        return {"dir": "behind", "now": None, "need": module_stage}
    i, j = L.STAGES.index(now), L.STAGES.index(module_stage)
    return {"dir": "ok" if i == j else ("behind" if i < j else "ahead"),
            "now": now, "need": module_stage}


def record(lab_id, action, stage=None, ok=True, scenario=None, job_id=None):
    st = load(lab_id)
    st["last_job"] = {"action": action, "stage": stage, "ok": ok,
                      "scenario": scenario, "job_id": job_id}
    # 적용과 초기화는 **둘 다** 랩을 그 단계로 만든다 —
    # reset.yml 은 흔적을 지운 뒤 site.yml 을 import 한다.
    # 초기화를 빼놓으면 단계가 올라가지 않아, 실제로는 m4 인 랩을 화면은 계속
    # m1 이라고 말한다 ("아직 이 모듈의 단계가 아니다" 안내가 영원히 안 사라진다).
    if ok and action in ("apply", "reset") and stage:
        st["stage"] = stage
        if stage not in st["applied"]:
            st["applied"].append(stage)
        st["verified"] = [v for v in st["verified"] if v != stage]
    if ok and action == "verify" and stage and stage not in st["verified"]:
        st["verified"].append(stage)
    if action == "break" and scenario and scenario not in st["broken"]:
        st["broken"].append(scenario)
    if action == "fix" and scenario:
        st["broken"] = [b for b in st["broken"] if b != scenario]
    if action == "reset":
        st["broken"] = []
    return save(lab_id, st)


def module_status(lab_id, module):
    """모듈 카드에 표시할 상태."""
    st = load(lab_id)
    stage = module["stage"]
    if stage in st.get("verified", []):
        return "verified"
    if stage in st.get("applied", []) or st.get("stage") == stage:
        return "applied"
    if st.get("stage") and L.STAGES.index(st["stage"]) > L.STAGES.index(stage):
        return "passed"
    return "pending"
