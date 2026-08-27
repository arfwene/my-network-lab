"""
작업 실행기 — ansible-playbook / 생성기를 돌리고 로그를 실시간으로 흘린다.

원칙
  · 랩당 동시 1개. 같은 랩에 두 작업이 겹치면 설정이 깨진다.
  · 명령은 화이트리스트로만 조립한다. 사용자 입력을 셸에 넘기지 않는다.
  · **Proxmox 가 정상일 때만 실행한다.** 관문은 Runner.submit 한 곳에 둔다.
    화면마다 검사를 붙이면 언젠가 빠뜨린 경로가 생긴다.
  · 시험 잠금도 같은 자리에서 본다 (Runner.guard). 규칙 자체는 exam.py 가 갖고
    이 파일은 "어디서 물어보는가"만 정한다.
"""
import asyncio
import os
import re
import shlex
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import labdesign as L
import pve
import state

PY = sys.executable


def _bin(name):
    """같은 venv 안의 실행 파일을 우선 사용한다. 없으면 PATH 에 맡긴다."""
    cand = Path(sys.executable).parent / name
    return str(cand) if cand.exists() else name


APB = _bin("ansible-playbook")
TF = "terraform"                      # PATH 에서 찾는다 (Proxmox 관리 워크스테이션에 설치)

# terraform 을 한꺼번에 몇 개나 돌릴 것인가. 기본값은 10 이다.
#   13대를 동시에 복제하면 pveproxy 가 연결을 끊는다 — HTTP 596 Broken pipe.
#   실패한 자원 하나 때문에 배포 전체가 멈추고, 그 VM 만 안 뜬 채로 남는다.
#   4 로 낮추면 조금 느려지지만 그 실패가 사라진다.
TF_PARALLELISM = 4

# 화면 로그에 남길 최대 줄 수. terraform apply 하나가 수천 줄을 쏟는다.
MAX_LINES = 5000

# ANSI 이스케이프. terraform 은 파이프로 보내도 색을 넣는다(-no-color 로 껐지만,
# 다른 도구가 넣는 것까지 여기서 한 번 더 걷어낸다). 안 걷으면 화면에 ␛[0m 이 그대로 찍힌다.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def tf_cmd(sub, *extra):
    cmd = [TF, sub, "-input=false", "-no-color", *extra]
    if sub in ("apply", "destroy"):
        cmd.append(f"-parallelism={TF_PARALLELISM}")
    return cmd
ANSIBLE = L.ROOT / "infra/ansible"
JUMP_HELPER = "/usr/local/sbin/lab-access-apply"
MGMT_HELPER = "/usr/local/sbin/lab-mgmt-apply"


def tf_env(lab_id):
    return L.ROOT / f"infra/terraform/envs/lab{lab_id}"
# 설치·준비 작업. 랩이 아니라 **환경**을 만드는 것이라 lab_id 0 (가상의 랩)에서 돈다.
#   전에는 이것들이 전부 `make ...` 였다. 관리자가 서버에 SSH 로 들어가
#   저장소 경로를 찾아 명령을 외워야 한다는 뜻이었고, 그게 배포를 어렵게 만든 주범이다.
SETUP_ACTIONS = {"setup-mgmt", "setup-mgmt-net", "setup-docs", "setup-access",
                 "setup-jump-apply"}
SETUP_LAB = 0
ACTIONS = {"deploy", "destroy", "apply", "keys", "verify", "reset", "break", "fix",
           "check", "exam", "drill", "drill-end", "drill-check"} | SETUP_ACTIONS
# 문서·계정 파일 생성은 Proxmox 와 무관하다. 여기에 관문을 두면
# "Proxmox 가 아직 안 되니 안내 문서도 못 만든다" 는 막다른 길이 생긴다.
NO_PVE = {"setup-docs", "setup-access", "setup-jump-apply"}

# Terraform 은 Proxmox API 를 직접 부른다 — 실패하면 상태 파일만 어긋난다.
# 나머지도 결국 그 위의 VM 을 만지므로 모두 막되, 무거운 쪽만 캐시를 무시하고 새로 확인한다.
FRESH_CHECK = {"deploy", "destroy"}
# 새로 자원을 만드는 작업만 충돌 검사까지 한다 (VMID·브리지 이름·대역·템플릿).
PREFLIGHT = {"deploy"}


# 시작하자마자 한참 조용한 작업들. 무엇을 기다리는 중인지 미리 적어 둔다 —
# 이유를 모르는 침묵은 사용자에게 "멈췄다" 와 같은 뜻이다.
QUIET_FIRST = {
    "destroy": "   terraform 이 자원 27개를 지웁니다. 지우는 순서가 있어 한 번에 다 사라지지는 않습니다.\n"
               "   중간에 조용한 구간이 있습니다 — 멈춘 것이 아닙니다. 아래 경과 시간이 계속 올라가면 정상입니다.",
    "deploy":  "   terraform 이 먼저 상태를 확인하고 실행 계획을 세웁니다.\n"
               "   그동안 출력이 없습니다 — 멈춘 것이 아닙니다.",
}


# 끝났을 때 다음에 무엇을 누르면 되는지. 로그가 "완료" 에서 끊기면 사람은
# **끝난 것인지 멈춘 것인지 구분하지 못한다** — 실제로 [랩 삭제] 뒤에 그랬다.
NEXT_HINT = {
    "destroy": "   랩이 지워졌습니다. 다시 만들려면 [랩 생성] 을 누르세요 (1~2분).",
    "deploy":  "   가상 장비가 준비됐습니다. 이제 [이 모듈 적용] 으로 설정을 올리세요.",
    "reset":   "   이 모듈의 시작 상태입니다. 터미널에서 실습을 이어 가면 됩니다.",
}


class Locked(RuntimeError):
    """시험이 진행 중이거나 마감되어 실행을 거부했다."""

    def __init__(self, message, exam=None):
        super().__init__(message)
        self.message = message
        self.exam = exam


class NotReady(RuntimeError):
    """Proxmox 가 준비되지 않아 실행하지 않았다."""

    def __init__(self, message, health=None):
        super().__init__(message)
        self.message = message
        self.health = health


def scenario_ids():
    return sorted(p.stem for p in (L.ROOT / "scenarios").glob("*.yml"))


# 시나리오 파일 머리말. 34개가 같은 형식으로 적혀 있다.
#   #   증상  : ...
#   #   정답  : ...
#   #   노림수: ...
# 중간 점검의 힌트가 여기서 나온다 — 따로 힌트를 쓰지 않는다.
# 두 곳에 같은 말을 적으면 한쪽이 낡는다.
_DOC_KEY = re.compile(r"^#\s{2,}(증상|한줄|목표|정답|노림수)\s*:\s*(.*)$")


def scenario_menu(lab_stage=None):
    """시나리오 목록 + 증상 한 줄 + 지금 랩에서 쓸 수 있는가.

    화면에는 `m03-01` 같은 코드만 나왔다. 그 코드가 무슨 증상인지는 교재 5장에만
    있어서, 실행 패널만 보는 사람은 무엇을 고를지 알 길이 없었다. 증상은 시나리오
    파일 머리말이 이미 갖고 있다 — **증상만** 꺼내 온다 (정답은 같은 머리말에 있지만
    가져오지 않는다).

    단계도 함께 본다. 랩이 M1 인데 목록에 m09-01 이 보이면 고를 수 있고, 고르면
    edge 도 inet 도 없는 인벤토리에 대고 돌아 그냥 실패한다.
    """
    out = []
    for sid in scenario_ids():
        mod = sid.split("-")[0]                     # m03-01 -> m03
        stage = "m" + str(int(mod[1:]))             # m03 -> m3
        doc = scenario_doc(sid)
        # 목록에는 **한 줄**만 쓴다. 증상 전문을 잘라 넣었더니 어느 것을 고르든
        # 비슷하게 잘려서, 정작 무엇이 안 되는지가 보이지 않았다.
        sym = re.sub(r"\*\*", "", doc.get("한줄") or doc.get("증상", ""))
        if len(sym) > 34:
            sym = sym[:33].rstrip(" ,.") + "…"
        out.append({"id": sid, "module": mod, "stage": stage, "symptom": sym,
                    # 검사로 판정할 수 없는 것은 장애 실습으로 인정되지 않는다.
                    # 연습거리로는 좋으므로 목록에는 남기되, 그 사실을 밝힌다 —
                    # 모르고 고르면 다 고쳐 놓고도 통과가 안 되는 줄 안다.
                    "graded": sid not in set(
                        L.SITE.get("console", {}).get("ungraded_scenarios") or []),
                    "locked": not (lab_stage and L.stage_le(stage, lab_stage))})
    return out


def scenario_doc(sid):
    """{'증상': ..., '정답': ..., '노림수': ...}. 없으면 빈 dict."""
    f = L.ROOT / "scenarios" / f"{sid}.yml"
    if not f.exists():
        return {}
    out, key = {}, None
    for line in f.read_text(encoding="utf-8").split("\n"):
        if not line.startswith("#"):
            if out:
                break
            continue
        m = _DOC_KEY.match(line)
        if m:
            key = m.group(1)
            out[key] = m.group(2).strip()
        elif key and line.startswith("#") and line[1:].strip():
            # 다음 줄로 이어진 설명
            out[key] = (out[key] + " " + line[1:].strip()).strip()
    return out


def scenario_brief(sid):
    """주입해 놓은 동안 화면에 띄울 것 — 지금 무엇이 안 되고, 무엇이 되면 끝인가.

    **정답은 넣지 않는다.** 증상과 목표만 있으면 스스로 찾는 연습이 성립하고,
    목표가 없으면 "이만하면 고친 건가" 를 알 수 없어 [복구] 를 눌러 버린다.
    """
    d = scenario_doc(sid)
    if not d:
        return None
    return {"id": sid,
            "symptom": re.sub(r"\*\*", "", d.get("한줄") or d.get("증상", "")),
            "detail": re.sub(r"\*\*", "", d.get("증상", "")),
            "goal": re.sub(r"\*\*", "", d.get("목표", ""))}


def build_steps(action, lab_id, stage, scenario=None, module=None):
    """(작업 디렉토리, argv) 목록. 셸을 거치지 않는다."""
    if action not in ACTIONS:
        raise ValueError(f"허용되지 않은 작업: {action}")
    if stage not in L.STAGES:
        raise ValueError(f"허용되지 않은 단계: {stage}")
    inv = f"inventory/lab{lab_id}"
    gen = (L.ROOT, [PY, "tools/gen-inventory.py", "--lab", str(lab_id), "--stage", stage])
    gen_tf = (L.ROOT, [PY, "tools/gen-tfvars.py", "--lab", str(lab_id)])

    if action == "setup-docs":
        # dist/ 산출물. 콘솔 화면은 이것 없이도 돌지만, 관리자가 손에 쥐는 문서다.
        return [(L.ROOT, [PY, "tools/render-modules.py",  "--lab", str(lab_id or 1)]),
                (L.ROOT, [PY, "tools/render-appendix.py", "--lab", str(lab_id or 1)]),
                (L.ROOT, [PY, "tools/render-labmap.py"]),
                (L.ROOT, [PY, "tools/render-access.py"]),
                (L.ROOT, [PY, "tools/render-host-guard.py"]),
                (L.ROOT, [PY, "tools/render-opsvm.py"])]
    if action == "setup-access":
        # 교육생 접속에 필요한 두 스크립트. 만들기만 한다 — 적용은 root 가 한다.
        return [(L.ROOT, [PY, "tools/gen-jumpaccess.py"]),
                (L.ROOT, [PY, "tools/gen-console-access.py"])]
    if action == "setup-jump-apply":
        # root 헬퍼. 저장소가 아니라 **root 소유 /usr/local/sbin** 의 프로그램을 부른다.
        # 여기서 저장소의 스크립트를 sudo 로 돌리면, 저장소를 쓸 수 있는 이 프로세스가
        # 곧 root 가 된다 — sudo 를 나눈 의미가 사라진다.
        return [(L.ROOT, ["sudo", "-n", JUMP_HELPER])]
    if action == "setup-mgmt":
        n = L.SITE["labs"]["default_count"]
        env = L.ROOT / "infra/terraform/envs/mgmt"
        return [(L.ROOT, [PY, "tools/gen-mgmt.py", "--labs", str(n)]),
                (env, tf_cmd("init")),
                (env, tf_cmd("apply", "-auto-approve"))]
    if action == "setup-mgmt-net":
        # 이 서버를 관리망에 붙인다. 앞부분(Proxmox API)은 이 계정으로 하고,
        # netplan 부터는 스크립트가 root 헬퍼에 넘긴다 — `make mgmt-net` 과 같은 길이다.
        return [(L.ROOT, [PY, "tools/setup-mgmt-net.py"])]

    if action == "deploy":
        # 브리지 + VM 생성. 배선은 항상 전체 토폴로지로 만든다 (설정만 단계별).
        #
        # terraform 이 끝났다는 것은 "VM 을 만들었다" 는 뜻이지 "쓸 수 있다" 는
        # 뜻이 아니다. 게스트는 그때부터 부팅한다. 여기서 기다리지 않으면 바로
        # 이어지는 [이 모듈 적용] 이 아직 sshd 가 안 뜬 노드에서 UNREACHABLE 로
        # 떨어진다 — 어느 노드가 걸릴지는 매번 달라서 증상이 들쭉날쭉하다.
        return [gen_tf,
                (tf_env(lab_id), tf_cmd("init")),
                (tf_env(lab_id), tf_cmd("apply", "-auto-approve")),
                gen,
                (ANSIBLE, [APB, "-i", inv, "playbooks/wait.yml"])]
    if action == "destroy":
        # -refresh=false: 지우기 전에 현재 상태를 다시 읽지 않는다.
        #   VM 자원은 QEMU 게스트 에이전트가 주소를 알려 줄 때까지 기다리는데,
        #   게스트 네트워크가 망가져 있으면 그 응답이 안 와서 refresh 단계에서
        #   몇 분씩 멈춘다 (기본 대기 15분). 어차피 지울 것이라 현재 상태를
        #   알 필요가 없고, 이미 사라진 자원은 provider 가 조용히 넘어간다.
        return [(tf_env(lab_id), tf_cmd("destroy", "-auto-approve", "-refresh=false"))]
    if action == "apply":
        return [gen, (ANSIBLE, [APB, "-i", inv, "playbooks/site.yml",
                                "-e", f"lab_stage={stage}"])]
    if action == "keys":
        # 접속 키만. site.yml 을 통째로 다시 올리면 13대 × 전 역할이 다시 도는데,
        # authorized_keys 한 줄 때문에 그럴 이유가 없다 — 태그로 세 작업만 고른다.
        # 단계 설정은 건드리지 않으므로 state 의 stage 도 움직이지 않는다.
        return [gen, (ANSIBLE, [APB, "-i", inv, "playbooks/site.yml",
                                "-e", f"lab_stage={stage}",
                                "-e", "gather_facts_on=false",
                                "--tags", "keys"])]
    if action == "verify":
        return [(ANSIBLE, [APB, "-i", inv, "playbooks/verify.yml",
                           "-e", f"lab_stage={stage}"])]
    if action == "reset":
        return [gen, (ANSIBLE, [APB, "-i", inv, "playbooks/reset.yml",
                                "-e", f"lab_stage={stage}"])]
    if action in ("check", "drill-check"):
        if not module:
            raise ValueError("검사에는 모듈이 필요합니다")
        return [(L.ROOT, [PY, "tools/run-checks.py", "--lab", str(lab_id), "--module", module])]
    if action == "drill-end":
        # 진단 연습 끝내기 — 주입돼 있던 것을 그 시나리오 자신의 fix 로 되돌린다.
        # 이 작업의 로그는 가리지 않는다. **무엇이었는지 보여 주는 것이 정답 공개**다.
        known = scenario_ids()
        old_ = [x for x in (state.load(lab_id).get("broken") or []) if x in known]
        if not old_:
            raise ValueError("되돌릴 장애가 없습니다")
        return [gen] + [(ANSIBLE, [APB, "-i", inv, f"../../scenarios/{x}.yml",
                                   "-e", "scenario_action=fix"]) for x in old_]
    if action in ("exam", "drill"):
        # 시험 시작 — 깨끗한 랩에서 출발해야 채점이 성립한다.
        #   ① 이미 주입돼 있던 장애를 그 시나리오 자신의 fix 로 되돌리고
        #      (reset 은 템플릿으로 만들어지는 설정만 복구한다. 멈춘 서비스나
        #       남은 tc qdisc 처럼 파일 밖의 손상은 fix 만 알고 있다)
        #   ② reset 으로 손댄 흔적을 지우고 site.yml 을 다시 적용한 뒤
        #   ③ 이번 회차 장애를 순서대로 주입한다.
        # 시나리오 이름이 로그에 그대로 찍히므로 이 작업은 secret 으로 흘린다.
        want = [x for x in (scenario or "").split(",") if x]
        if not want:
            raise ValueError("시험에 주입할 시나리오가 없습니다")
        known = scenario_ids()
        bad = [x for x in want if x not in known]
        if bad:
            raise ValueError(f"없는 시나리오: {', '.join(bad)}")
        old = [x for x in (state.load(lab_id).get("broken") or []) if x in known]
        steps = [gen]
        steps += [(ANSIBLE, [APB, "-i", inv, f"../../scenarios/{x}.yml",
                             "-e", "scenario_action=fix"]) for x in old]
        steps += [(ANSIBLE, [APB, "-i", inv, "playbooks/reset.yml",
                             "-e", f"lab_stage={stage}"])]
        steps += [(ANSIBLE, [APB, "-i", inv, f"../../scenarios/{x}.yml",
                             "-e", "scenario_action=break"]) for x in want]
        return steps
    if action in ("break", "fix"):
        if scenario not in scenario_ids():
            raise ValueError(f"없는 시나리오: {scenario}")
        return [(ANSIBLE, [APB, "-i", inv, f"../../scenarios/{scenario}.yml",
                           "-e", f"scenario_action={action}"])]
    raise ValueError(action)


class Job:
    def __init__(self, lab_id, action, stage, scenario, steps, user, module=None,
                 secret=False):
        self.id = uuid.uuid4().hex[:12]
        # secret 인 작업은 로그와 요약에서 시나리오 이름을 가린다.
        # 시험 문제가 실행 로그로 새어 나가면 시험 자체가 성립하지 않는다.
        self.secret = secret
        self.lab_id, self.action, self.stage, self.scenario = lab_id, action, stage, scenario
        self.module = module
        self.user = user
        self.steps = steps
        self.lines: list[str] = []
        self.dropped = 0        # 너무 길어서 버린 앞부분 줄 수
        # 진행률. terraform 은 퍼센트를 찍지 않지만, 계획한 자원 수와 끝난 자원 수는
        # 로그에 그대로 나온다. 그 둘로 센다 — 지어내지 않는다.
        self.total = 0
        self.done = 0
        # 자원 중 **VM 만** 따로 센다. terraform 은 VM 13대와 링크 브리지 13개를
        # 합쳐 27개라고 말하는데, 교육생에게 "13/26대" 라고 보여 주면 장비가
        # 두 배로 늘어난 것처럼 읽힌다. 막대는 전체로, 숫자는 장비로 말한다.
        self.done_vm = 0
        self.status = "queued"          # queued | running | ok | failed
        # 마지막으로 한 줄이라도 나온 시각. 조용한 구간이 얼마나 길어졌는지 재려고 둔다 —
        # terraform 은 refresh 하는 동안 아무것도 찍지 않아서, 화면만 보면 멈춘 것과 같다.
        self.last_out = None
        self.rc = None
        self.started = self.finished = None
        self.event = asyncio.Event()

    def emit(self, line):
        self.last_out = time.time()
        self._count(line)
        self.lines.append(ANSI_RE.sub("", line).rstrip("\n"))
        # 로그는 무한히 자라지 않는다. terraform apply 하나가 수천 줄을 쏟아내고,
        # 그걸 다 들고 있으면 새로 붙는 화면마다 그 전부를 다시 받는다.
        if len(self.lines) > MAX_LINES:
            drop = len(self.lines) - MAX_LINES
            del self.lines[:drop]
            self.dropped += drop
        self.event.set()
        self.event = asyncio.Event()

    def quiet(self):
        """(전체 경과, 마지막 출력 이후 경과) — 둘 다 초."""
        now = time.time()
        return (round(now - (self.started or now), 1),
                round(now - (self.last_out or self.started or now), 1))

    # terraform 의 두 줄만 본다.
    #   Plan: 27 to add, 0 to change, 0 to destroy.
    #   module.lab.proxmox_virtual_environment_vm.node["pc1"]: Creation complete after 12s [id=...]
    # destroy 는 "Destruction complete" 로 끝난다.
    PLAN_RE = re.compile(r"^Plan:\s*(\d+)\s+to add(?:,\s*(\d+)\s+to change)?"
                         r"(?:,\s*(\d+)\s+to destroy)?")
    STEP_RE = re.compile(r": (?:Creation|Modifications|Destruction) complete after")
    # 자원 주소에 이 타입이 들어 있으면 가상 머신이다 (modules/lab/main.tf).
    VM_RE = re.compile(r"proxmox_virtual_environment_vm\.")

    def _count(self, line):
        if not self.total:
            m = self.PLAN_RE.match(line.strip())
            if m:
                self.total = sum(int(g) for g in m.groups() if g)
                return
        if self.STEP_RE.search(line):
            self.done += 1
            if self.VM_RE.search(line):
                self.done_vm += 1

    def pct(self):
        """0~100. 계획을 아직 못 봤으면 None — '모른다' 를 0% 로 속이지 않는다."""
        if self.status == "ok":
            return 100
        if not self.total:
            return None
        return min(99, round(self.done * 100 / self.total))

    def as_dict(self, reveal=True):
        return {"id": self.id, "lab_id": self.lab_id, "action": self.action,
                "stage": self.stage,
                "scenario": self.scenario if (reveal or not self.secret) else None,
                "module": self.module,
                "status": self.status,
                "rc": self.rc, "user": self.user,
                "elapsed": round((self.finished or time.time()) - (self.started or time.time()), 1),
                "pct": self.pct(), "done": self.done, "total": self.total,
                "vm": self.done_vm}


class Runner:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.active: dict[int, str] = {}     # 랩 -> 진행 중 job id
        # (lab_id, action, username) -> 거부 사유 | None. app 이 exam.gate 를 꽂는다.
        # 여기에 두는 이유는 pve.gate 와 같다 — 관문은 한 곳이어야 빠뜨리지 않는다.
        self.guard = None

    def lock(self, lab_id):
        return self.locks.setdefault(lab_id, asyncio.Lock())

    def busy(self, lab_id):
        # 락 상태를 보면 안 된다. submit 과 태스크의 락 획득 사이에 틈이 생겨
        # 두 번째 요청이 통과한 뒤 큐에 쌓인다. 제출 시점에 동기적으로 표시한다.
        return lab_id in self.active

    async def submit(self, lab_id, action, stage, scenario, user, on_done=None,
                     module=None, secret=False):
        if self.busy(lab_id):
            raise RuntimeError(f"lab{lab_id} 에서 다른 작업이 실행 중이다")
        steps = build_steps(action, lab_id, stage, scenario, module)   # 검증 먼저
        # 시험 잠금. Proxmox 를 두드리기 전에 본다 — 잠긴 랩은 물어볼 것도 없다.
        if self.guard:
            why = self.guard(lab_id, action, user)
            if why:
                raise Locked(why)
        # Proxmox 점검. 소켓을 쓰므로 이벤트 루프를 막지 않게 스레드로 돌린다.
        if action not in NO_PVE:
            ok, why, health = await asyncio.to_thread(
                pve.gate, action in FRESH_CHECK, lab_id if action in PREFLIGHT else None)
            if not ok:
                raise NotReady(why, health)
        job = Job(lab_id, action, stage, scenario, steps, user, module, secret)
        self.jobs[job.id] = job
        self.active[lab_id] = job.id                           # 동기적으로 점유
        asyncio.create_task(self._run(job, on_done))
        return job

    async def _run(self, job, on_done):
        async with self.lock(job.lab_id):
            job.status, job.started = "running", time.time()
            job.emit(f"$ [lab{job.lab_id}] {job.action}"
                     + (f" {job.scenario}" if job.scenario and not job.secret else "")
                     + (f" (단계 {job.stage})" if job.stage else ""))
            if job.action in QUIET_FIRST:
                job.emit(QUIET_FIRST[job.action])
            rc = 0
            for cwd, argv in job.steps:
                job.emit(f"$ {shlex.join(argv)}")
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *argv, cwd=str(cwd),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                        env={**os.environ, **pve.env(), "ANSIBLE_FORCE_COLOR": "0",
                             "PYTHONUNBUFFERED": "1"})
                except FileNotFoundError as e:
                    job.emit(f"!! 실행 파일을 찾을 수 없다: {e}")
                    rc = 127
                    break
                assert proc.stdout
                async for raw in proc.stdout:
                    job.emit(raw.decode("utf-8", "replace"))
                rc = await proc.wait()
                if rc != 0:
                    job.emit(f"!! 종료 코드 {rc} — 중단한다")
                    break
            job.rc, job.finished = rc, time.time()
            job.status = "ok" if rc == 0 else "failed"
            job.emit(f"== {'완료' if rc == 0 else '실패'} ({job.as_dict()['elapsed']}초)")
            if rc == 0 and job.action in NEXT_HINT and not job.secret:
                job.emit(NEXT_HINT[job.action])
        self.active.pop(job.lab_id, None)
        if on_done:
            on_done(job)

    async def stream(self, job_id):
        """이미 쌓인 로그부터 보내고, 이후 실시간으로 이어 보낸다.

        한 줄씩이 아니라 **그 순간 쌓여 있는 만큼 묶어서** 준다.
        terraform apply 는 짧은 시간에 수천 줄을 쏟는다. 한 줄에 SSE 프레임 하나면
        브라우저가 그만큼 콜백을 돌고 그만큼 DOM 을 건드린다 — 그 사이 탭이 굳는다.
        묶음은 저절로 조절된다: 몰아칠 때는 크게, 한가할 때는 한 줄씩이라 지연이 없다.
        """
        job = self.jobs.get(job_id)
        if not job:
            return
        idx = 0                     # 절대 줄 번호 — 버린 앞부분까지 센다
        while True:
            have = job.dropped + len(job.lines)
            if idx < have:
                if idx < job.dropped:
                    lost = job.dropped - idx
                    idx = job.dropped
                    yield [f"… 앞부분 {lost}줄은 너무 길어 버렸다 "
                           f"(최근 {MAX_LINES}줄만 남긴다)"]
                    continue
                chunk = job.lines[idx - job.dropped:]
                idx = have
                yield chunk
                continue
            if job.status in ("ok", "failed"):
                return
            try:
                await asyncio.wait_for(job.event.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield []          # keep-alive
