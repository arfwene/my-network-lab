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


def record(lab_id, action, stage=None, ok=True, scenario=None, job_id=None):
    st = load(lab_id)
    st["last_job"] = {"action": action, "stage": stage, "ok": ok,
                      "scenario": scenario, "job_id": job_id}
    if ok and action == "apply" and stage:
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
