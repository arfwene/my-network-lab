"""랩별 진행 상태 — 어느 단계까지 적용했고, 무엇이 검증을 통과했는가."""
import json
import time
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


def fresh(lab_id):
    """아무것도 하지 않은 랩의 상태. 랩을 지우면 여기로 되돌린다."""
    return {"lab_id": lab_id, "stage": None, "applied": [], "verified": [],
            "last_job": None, "broken": [], "provisioned": None,
            # 장애 실습: 주입한 뒤 **검사가 실제로 실패하는 것을 본** 모듈들.
            # 이것 없이 "검사가 통과했다" 만으로 인정하면, 아직 증상이 나타나기
            # 전에 검증한 사람과 다 고친 사람을 구분하지 못한다.
            "drill_seen": [],
            # 진단 연습 중에는 무엇이 주입됐는지 화면에 쓰지 않는다.
            # 이 값이 참인 동안 broken 은 있으나 보이지 않는다.
            "blind": False,
            # 중간 점검에서 받아 간 힌트 수. 새 회차마다 0 으로 돌아간다.
            "hints": 0}


def load(lab_id):
    p = _path(lab_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return fresh(lab_id)


def save(lab_id, st):
    _path(lab_id).write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def provisioned(lab_id):
    """이 랩의 VM 이 만들어져 있는가 — **마지막으로 알고 있는 값.**

    진실은 Proxmox 에 있다 (pve.lab_vms). 그런데 그건 API 왕복이라 요청마다
    물을 수 없다. 그래서 배포·삭제가 끝날 때 여기에 적어 두고, 화면 관문은
    이 값을 본다. 마법사는 들어갈 때 Proxmox 에 다시 물어 이 값을 고친다.

    None 은 '아니다' 가 아니라 '아직 모른다' 다 — 이 기능이 생기기 전에
    만들어진 랩이 그렇다. 모르면 한 번 물어보게 둔다.
    """
    return load(lab_id).get("provisioned")


def set_provisioned(lab_id, ok):
    st = load(lab_id)
    if st.get("provisioned") is bool(ok):
        return st                      # 바뀐 게 없으면 파일을 건드리지 않는다
    st["provisioned"] = bool(ok)
    return save(lab_id, st)


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


def repair():
    """앞뒤가 안 맞는 랩 상태를 바로잡는다. 콘솔이 뜰 때 한 번 부른다.

    **없는 랩이 단계를 갖고 있을 수 없다.** 전에는 [랩 삭제] 가 provisioned 만
    껐고 stage·applied 를 그대로 뒀다. 그 파일이 남아 있으면 지운 랩인데도
    모듈 카드가 「적용됨」·「통과」로 남는다. 지금은 지울 때 함께 되돌리지만,
    **이미 그렇게 남은 파일**은 스스로 낫지 않으므로 여기서 한 번 훑는다.

    고칠 것이 없으면 아무 파일도 건드리지 않는다.
    """
    fixed = []
    if not DIR.exists():
        return fixed
    for f in sorted(DIR.glob("lab*.json")):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if st.get("provisioned") is not False:
            continue
        if not (st.get("stage") or st.get("applied") or st.get("verified")
                or st.get("broken") or st.get("drill_seen")):
            continue
        lab_id = st.get("lab_id")
        keep = st.get("last_job")
        st = fresh(lab_id)
        st["provisioned"] = False
        st["last_job"] = keep
        save(lab_id, st)
        fixed.append(lab_id)
    return fixed


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
        # 주입 시각. 아래 drill_ready() 가 쓴다.
        st["drill_at"] = time.time()
    if action == "fix" and scenario:
        st["broken"] = [b for b in st["broken"] if b != scenario]
    if action == "reset":
        st["broken"] = []
        # 랩을 되돌렸으면 이번 장애 실습은 없던 일이다.
        st["drill_seen"] = []
    # 진단 연습: 서버가 고르고 화면에서 가린다. 끝내면 다시 보인다.
    if ok and action == "drill" and scenario:
        st["broken"] = [x for x in scenario.split(",") if x]
        st["blind"] = True
        st["hints"] = 0
    if action in ("drill-end", "reset", "exam"):
        st["blind"] = False
    if action == "drill-end" and ok:
        st["broken"] = []
    # 랩이 생겼는지 없어졌는지. 관문이 요청마다 Proxmox 를 두드리지 않도록
    # 여기서 적어 둔다 — 진실은 아니고, 마지막으로 확인한 값이다.
    if ok and action == "deploy":
        st["provisioned"] = True
    if ok and action == "destroy":
        # 랩이 없어졌으면 **진행 상태도 없어야 한다.**
        #   전에는 provisioned 만 껐다. 그래서 지운 랩인데도 모듈 카드가
        #   「적용됨」·「통과」로 남았고, 새로 만든 랩이 남의 진도를 물려받았다.
        #   지운 순간이 곧 시작점이므로 통째로 되돌린다.
        st = fresh(lab_id)
        st["provisioned"] = False
        st["last_job"] = {"action": action, "stage": stage, "ok": ok,
                          "scenario": scenario, "job_id": job_id}
    return save(lab_id, st)


def module_status(lab_id, module):
    """모듈 카드에 표시할 상태."""
    st = load(lab_id)
    stage = module["stage"]
    if stage in st.get("verified", []):
        return "verified"
    if stage in st.get("applied", []) or st.get("stage") == stage:
        return "applied"
    # 모르는 단계가 오면 "아직" 으로 본다.
    #   단계 목록은 **프로세스가 뜰 때** 한 번 읽는다. 단계를 늘리는 배포를 하고
    #   콘솔을 재시작하지 않으면, 저장된 진행에는 있는 단계가 목록에는 없다.
    #   그때 예외를 던지면 대시보드 전체가 500 이 되어 모든 교육생이 막힌다 —
    #   실제로 M5 를 끼워 넣은 배포에서 그렇게 됐다. 카드 하나가 덜 정확한 편이 낫다.
    now = st.get("stage")
    if now in L.STAGES and stage in L.STAGES and L.STAGES.index(now) > L.STAGES.index(stage):
        return "passed"
    return "pending"


def take_hint(lab_id):
    """힌트를 하나 더 받는다. 받은 횟수를 돌려준다 (1 부터)."""
    st = load(lab_id)
    st["hints"] = int(st.get("hints") or 0) + 1
    save(lab_id, st)
    return st["hints"]


def mark_drill_seen(lab_id, module_id):
    """이 모듈의 장애가 검사에 실제로 잡혔다.

    OSPF 인접은 dead interval(40초)이 지나야 끊기고, ARP 항목도 시간이 지나야
    영향이 보인다. 주입하자마자 검증하면 아직 멀쩡해서 전부 통과한다.
    그 상태로 인정해 주면 아무것도 안 하고 통과하는 길이 생긴다.

    그래서 **고장을 한 번 본 뒤에 고친 것**만 인정한다. 검사가 못 잡는
    시나리오도 이 규칙에서는 저절로 걸러진다 — 본 적이 없으니 인정되지 않는다.
    """
    st = load(lab_id)
    seen = st.setdefault("drill_seen", [])
    if module_id not in seen:
        seen.append(module_id)
        save(lab_id, st)
    return st


# 주입 직후에는 아직 고장이 나타나지 않는다. OSPF 는 dead interval(40초)이
# 지나야 인접을 내리고, ARP 도 시간이 걸린다. 그 사이에 검증하면 전부 통과한다.
DRILL_SETTLE = 90


def drill_ready(lab_id, module_id):
    """이 통과를 '스스로 고쳤다' 로 볼 수 있는가.

    둘 중 하나면 된다.
      · 검사가 실패하는 것을 한 번 봤다 — 고장이 확실히 있었다는 뜻
      · 주입한 지 충분히 지났다 — 터미널에서 진단하고 고친 뒤 검증을 한 번만
        누른 사람도 있다. 그 사람을 실패한 적 없다는 이유로 떨어뜨리면 안 된다.

    주입하자마자 누른 경우만 걸러진다. 그때는 다시 누르면 된다.
    """
    st = load(lab_id)
    if module_id in (st.get("drill_seen") or []):
        return True
    at = st.get("drill_at")
    return bool(at) and (time.time() - at) >= DRILL_SETTLE


def drill_seen(lab_id, module_id):
    return module_id in (load(lab_id).get("drill_seen") or [])


def drill_solved(lab_id):
    """검사를 통과했다 — 정답을 보지 않고 끝냈다.

    시나리오의 fix 를 돌리지 않는다. 교육생이 손으로 고쳐 검사가 통과한 상태라
    되돌릴 것이 없다. 여기서 fix 를 또 돌리면 교육생이 만든 정상 설정을
    덮어쓸 수도 있다.
    """
    st = load(lab_id)
    st["broken"] = []
    st["blind"] = False
    st["drill_seen"] = []
    return save(lab_id, st)
