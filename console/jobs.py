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
ANSIBLE = L.ROOT / "infra/ansible"
JUMP_HELPER = "/usr/local/sbin/lab-access-apply"


def tf_env(lab_id):
    return L.ROOT / f"infra/terraform/envs/lab{lab_id}"
# 설치·준비 작업. 랩이 아니라 **환경**을 만드는 것이라 lab_id 0 (가상의 랩)에서 돈다.
#   전에는 이것들이 전부 `make ...` 였다. 관리자가 서버에 SSH 로 들어가
#   저장소 경로를 찾아 명령을 외워야 한다는 뜻이었고, 그게 배포를 어렵게 만든 주범이다.
SETUP_ACTIONS = {"setup-mgmt", "setup-docs", "setup-access", "setup-jump-apply"}
SETUP_LAB = 0
ACTIONS = {"deploy", "destroy", "apply", "verify", "reset", "break", "fix", "check",
           "exam"} | SETUP_ACTIONS
# 문서·계정 파일 생성은 Proxmox 와 무관하다. 여기에 관문을 두면
# "Proxmox 가 아직 안 되니 안내 문서도 못 만든다" 는 막다른 길이 생긴다.
NO_PVE = {"setup-docs", "setup-access", "setup-jump-apply"}

# Terraform 은 Proxmox API 를 직접 부른다 — 실패하면 상태 파일만 어긋난다.
# 나머지도 결국 그 위의 VM 을 만지므로 모두 막되, 무거운 쪽만 캐시를 무시하고 새로 확인한다.
FRESH_CHECK = {"deploy", "destroy"}
# 새로 자원을 만드는 작업만 충돌 검사까지 한다 (VMID·브리지 이름·대역·템플릿).
PREFLIGHT = {"deploy"}


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
                (env, [TF, "init", "-input=false"]),
                (env, [TF, "apply", "-auto-approve", "-input=false"])]

    if action == "deploy":
        # 브리지 + VM 생성. 배선은 항상 전체 토폴로지로 만든다 (설정만 단계별).
        return [gen_tf,
                (tf_env(lab_id), [TF, "init", "-input=false"]),
                (tf_env(lab_id), [TF, "apply", "-auto-approve", "-input=false"])]
    if action == "destroy":
        return [(tf_env(lab_id), [TF, "destroy", "-auto-approve", "-input=false"])]
    if action == "apply":
        return [gen, (ANSIBLE, [APB, "-i", inv, "playbooks/site.yml",
                                "-e", f"lab_stage={stage}"])]
    if action == "verify":
        return [(ANSIBLE, [APB, "-i", inv, "playbooks/verify.yml",
                           "-e", f"lab_stage={stage}"])]
    if action == "reset":
        return [gen, (ANSIBLE, [APB, "-i", inv, "playbooks/reset.yml",
                                "-e", f"lab_stage={stage}"])]
    if action == "check":
        if not module:
            raise ValueError("검사에는 모듈이 필요하다")
        return [(L.ROOT, [PY, "tools/run-checks.py", "--lab", str(lab_id), "--module", module])]
    if action == "exam":
        # 시험 시작 — 깨끗한 랩에서 출발해야 채점이 성립한다.
        #   ① 이미 주입돼 있던 장애를 그 시나리오 자신의 fix 로 되돌리고
        #      (reset 은 템플릿으로 만들어지는 설정만 복구한다. 멈춘 서비스나
        #       남은 tc qdisc 처럼 파일 밖의 손상은 fix 만 알고 있다)
        #   ② reset 으로 손댄 흔적을 지우고 site.yml 을 다시 적용한 뒤
        #   ③ 이번 회차 장애를 순서대로 주입한다.
        # 시나리오 이름이 로그에 그대로 찍히므로 이 작업은 secret 으로 흘린다.
        want = [x for x in (scenario or "").split(",") if x]
        if not want:
            raise ValueError("시험에 주입할 시나리오가 없다")
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
        self.status = "queued"          # queued | running | ok | failed
        self.rc = None
        self.started = self.finished = None
        self.event = asyncio.Event()

    def emit(self, line):
        self.lines.append(line.rstrip("\n"))
        self.event.set()
        self.event = asyncio.Event()

    def as_dict(self, reveal=True):
        return {"id": self.id, "lab_id": self.lab_id, "action": self.action,
                "stage": self.stage,
                "scenario": self.scenario if (reveal or not self.secret) else None,
                "module": self.module,
                "status": self.status,
                "rc": self.rc, "user": self.user,
                "elapsed": round((self.finished or time.time()) - (self.started or time.time()), 1)}


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
        self.active.pop(job.lab_id, None)
        if on_done:
            on_done(job)

    async def stream(self, job_id):
        """이미 쌓인 로그부터 보내고, 이후 실시간으로 이어 보낸다."""
        job = self.jobs.get(job_id)
        if not job:
            return
        idx = 0
        while True:
            while idx < len(job.lines):
                yield job.lines[idx]
                idx += 1
            if job.status in ("ok", "failed"):
                return
            try:
                await asyncio.wait_for(job.event.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ""          # keep-alive
