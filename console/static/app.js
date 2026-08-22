/* my-network-lab console — 의존성 없는 클라이언트 (CDN 불필요) */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const log = $('#log'), jobinfo = $('#jobinfo'), consoleBox = $('#console');
  let es = null;

  const labId = () => $('.actions')?.dataset.lab || '1';
  const curModule = () => $('.tabs')?.dataset.module || '';

  // ------------------------------------------------------------ 확인 모달
  //  경고 문구 + 영향 범위를 보여주고, 최종 확인 버튼을 따로 누르게 한다.
  const MODAL = {
    reset: {
      title: '이 모듈을 초기 상태로 되돌린다',
      body: `<ul>
        <li>실습 중 만든 설정이 <b>모두 사라진다</b> — 추가한 주소, 라우팅, 정적 ARP, 임시로 꽂은 스위치 포트</li>
        <li>주입된 장애도 함께 해제된다</li>
        <li>VM 은 지워지지 않는다. 설정만 이 모듈의 시작 상태로 돌아간다</li>
        <li><b>퀴즈 점수와 제출 이력은 그대로 남는다</b></li>
      </ul>`,
      ok: '정말 초기화한다'
    },
    destroy: {
      title: '랩의 VM 을 전부 삭제한다',
      body: `<ul>
        <li>이 랩의 <b>가상 머신과 브리지가 삭제된다</b></li>
        <li>다시 쓰려면 [랩 생성] 으로 처음부터 만들어야 한다 (수 분 소요)</li>
        <li>제출 이력은 남는다</li>
      </ul>`,
      ok: '정말 삭제한다'
    }
  };

  function confirmAction(kind, scope) {
    return new Promise(resolve => {
      const m = MODAL[kind];
      $('#modal-title').textContent = m.title;
      $('#modal-body').innerHTML = m.body;
      $('#modal-scope').textContent = scope;
      $('#modal-ok').textContent = m.ok;
      $('#modal').hidden = false;
      const close = v => {
        $('#modal').hidden = true;
        $('#modal-ok').onclick = $('#modal-cancel').onclick = null;
        resolve(v);
      };
      $('#modal-ok').onclick = () => close(true);
      $('#modal-cancel').onclick = () => close(false);
      $('#modal').onclick = e => { if (e.target.id === 'modal') close(false); };
    });
  }

  // ------------------------------------------------------------ 모듈 전환
  async function loadModule(id, kind = 'README') {
    const r = await fetch(`/m/${encodeURIComponent(id)}?lab=${labId()}&kind=${kind}`);
    $('#module').innerHTML = await r.text();
    $$('.mod').forEach(b => b.classList.toggle('on', b.dataset.module === id));
    const stage = $('.tabs')?.dataset.stage;
    if (stage) {
      $('.actions').dataset.stage = stage;
      $('.side h3 small').textContent = stage;
      fetch(`/topology.svg?stage=${stage}`).then(r => r.text()).then(s => $('#topo').innerHTML = s);
    }
    const cap = $('.actions')?.dataset.capstone;
    if ($('#capstone')) $('#capstone').hidden = (id !== cap);
    const u = new URL(location);
    u.searchParams.set('m', id); u.searchParams.set('lab', labId());
    history.replaceState({}, '', u);
    bind();
  }

  function bind() {
    $$('.tab').forEach(t => t.onclick = () => loadModule(curModule(), t.dataset.kind));
    // 교재 본문에서 [과제](#tasks) 같은 링크를 누르면 그 탭으로 간다.
    // 교재는 웹과 인쇄본 양쪽으로 나가므로, 인쇄본에서는 그냥 앵커로 남는다.
    $$('.doc a[href^="#"]').forEach(a => {
      const kind = a.getAttribute('href').slice(1);
      if (!['tasks', 'quiz', 'README'].includes(kind)) return;
      a.onclick = e => { e.preventDefault(); loadModule(curModule(), kind); };
    });
    const f = $('#quizform');
    if (f) f.onsubmit = submitAssessment;
    $$('[data-goto]').forEach(b => b.onclick = () => loadModule(b.dataset.goto));
  }

  document.addEventListener('click', e => {
    const mod = e.target.closest('.mod:not(.locked)');
    if (mod) loadModule(mod.dataset.module);
  });

  // ------------------------------------------------------------ 제출·검증
  async function submitAssessment(ev) {
    ev.preventDefault();
    const form = ev.target, mid = form.dataset.module;
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true; form.querySelector('.submitting').hidden = false;

    const res = await fetch(`/m/${mid}/submit`, { method: 'POST', body: new FormData(form) });
    const j = await res.json();

    if (j.error) { consoleBox.classList.remove('collapsed'); paint('!! ' + j.error); }
    // 시간이 끝난 뒤의 제출은 정상 경로다 — 서술은 저장됐고 검사만 돌지 않았다.
    if (j.locked && j.note) { consoleBox.classList.remove('collapsed'); paint('== ' + j.note); }
    if (res.status === 503) window.pveHealth?.show(j.health);
    if (j.job_id) {
      consoleBox.classList.remove('collapsed');
      jobinfo.textContent = `검사 · ${j.job_id}`;
      await stream(j.job_id);
    }
    const r = await fetch(`/m/${mid}/result?lab=${labId()}`);
    $('#assessresult').innerHTML = await r.text();
    $$('[data-goto]').forEach(b => b.onclick = () => loadModule(b.dataset.goto));
    // 모듈 목록의 잠금·진도 갱신
    const idx = await fetch(`/?lab=${labId()}&m=${mid}`).then(r => r.text());
    const doc = new DOMParser().parseFromString(idx, 'text/html');
    $('.modules').innerHTML = doc.querySelector('.modules').innerHTML;

    btn.disabled = false; form.querySelector('.submitting').hidden = true;
    $('#assessresult').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // ------------------------------------------------------------ 작업 실행
  function paint(line) {
    const span = document.createElement('span');
    if (line.startsWith('$ ')) span.className = 'cmd';
    else if (line.startsWith('!!')) span.className = 'err';
    else if (line.startsWith('== ')) span.className = line.includes('완료') ? 'ok' : 'err';
    else if (/^\s*\[PASS\]/.test(line)) span.className = 'ok';
    else if (/^\s*\[FAIL\]/.test(line)) span.className = 'err';
    else if (/^(TASK|PLAY)/.test(line)) span.className = 'task';
    else if (/failed=[1-9]|fatal:|ERROR/.test(line)) span.className = 'err';
    span.textContent = line + '\n';
    log.appendChild(span);
    log.scrollTop = log.scrollHeight;
  }

  // 초 -> "1분 20초". 로그 옆에 계속 붙는 값이라 짧게 읽혀야 한다.
  function fmt(sec) {
    sec = Math.round(sec);
    return sec < 60 ? `${sec}초` : `${Math.floor(sec / 60)}분 ${sec % 60}초`;
  }

  function stream(jobId) {
    return new Promise(resolve => {
      if (es) es.close();
      es = new EventSource(`/jobs/${jobId}/stream`);
      es.onmessage = ev => { const l = JSON.parse(ev.data); if (l !== '') paint(l); };
      // 조용한 구간을 보이게 한다. terraform 은 refresh 하는 동안 아무것도 찍지 않아서,
      // 이게 없으면 "멈췄다" 와 화면상 구분이 되지 않는다.
      es.addEventListener('tick', ev => {
        const d = JSON.parse(ev.data);
        jobinfo.textContent = `진행 중 · ${fmt(d.elapsed)} 경과`
          + (d.quiet > 20 ? ` · 마지막 출력 ${fmt(d.quiet)} 전` : '');
      });
      es.addEventListener('done', ev => {
        const d = JSON.parse(ev.data);
        jobinfo.textContent = `${d.action} · ${d.status} · ${d.elapsed}초`;
        es.close(); es = null;
        refreshStatus();
        resolve(d);
      });
      es.onerror = () => { paint('!! 로그 스트림이 끊겼다'); es.close(); es = null; resolve(null); };
    });
  }

  async function run(action) {
    const box = $('.actions');
    const scenario = $('#scenario')?.value || '';
    if ((action === 'break' || action === 'fix') && !scenario) {
      paint('!! 시나리오를 먼저 고를 것'); return;
    }
    if (MODAL[action]) {
      const scope = `대상: lab${box.dataset.lab} · 단계 ${box.dataset.stage}`
        + (action === 'reset' ? ` · 모듈 ${curModule().toUpperCase()}` : '');
      if (!await confirmAction(action, scope)) return;
    }
    consoleBox.classList.remove('collapsed');
    $$('.actions .btn').forEach(x => x.disabled = true);
    const body = new URLSearchParams({
      lab: box.dataset.lab, action, stage: box.dataset.stage,
      scenario, module: curModule()
    });
    const r = await fetch('/action', { method: 'POST', body });
    const j = await r.json();
    if (!r.ok) {
      paint('!! ' + (j.error || '실행 실패'));
      // Proxmox 문제라면 어디가 막혔는지 바로 띄운다. 로그만 보고는 알 수 없다.
      if (r.status === 503) window.pveHealth?.show(j.health);
      // 423 = 시험 잠금. 화면이 오래된 것일 수 있으니 상태부터 맞춘다.
      if (r.status === 423) refreshStatus();
      restoreActions();
      return;
    }
    jobinfo.textContent = `${action} · ${j.job_id}`;
    await stream(j.job_id);
    restoreActions();
  }

  // ------------------------------------------------------------ 시험 세션
  //  잠금의 본체는 서버(exam.gate)다. 여기서 하는 일은 남은 시간을 보여주고
  //  단계가 바뀐 순간 화면을 서버 상태에 맞추는 것뿐이다.
  let lastPhase = $('#exambar')?.dataset.phase || 'none';

  function refreshStatus() {
    return fetch(`/status?lab=${labId()}`).then(r => r.text()).then(h => {
      $('#status').innerHTML = h;
      const ph = $('#exambar')?.dataset.phase || 'none';
      // open -> overtime -> closed 로 넘어가면 버튼 잠금 상태 자체가 달라진다.
      // 부분 갱신으로는 맞출 수 없으므로 한 번만 다시 그린다.
      if (ph !== lastPhase && (ph === 'closed' || lastPhase === 'closed')) {
        lastPhase = ph; location.reload(); return;
      }
      lastPhase = ph;
    });
  }

  function restoreActions() {
    // 시험 중 잠긴 버튼을 되살리면 안 된다. 서버가 정한 잠금이 화면에 남아 있어야 한다.
    const locked = $('.actions')?.classList.contains('examlock');
    const allow = ['verify'];
    $$('.actions .btn').forEach(x => {
      x.disabled = locked && !allow.includes(x.dataset.action);
    });
  }

  function examTick() {
    const bar = $('#exambar'), c = $('#examclock');
    if (!bar || !c || bar.dataset.phase !== 'open') return;
    const r = Math.max(0, parseInt(bar.dataset.remaining || '0', 10) - 1);
    bar.dataset.remaining = r;
    const m = Math.floor(r / 60), s = r % 60;
    c.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    c.classList.toggle('urgent', r > 0 && r <= 300);
    if (r === 0) refreshStatus();     // 마감 확정은 서버가 한다. 결과를 받아 온다.
  }

  setInterval(examTick, 1000);
  // 시계가 없어도(다른 브라우저에서 연장·마감했을 수 있다) 주기적으로 서버와 맞춘다.
  setInterval(() => { if ($('#exambar')) refreshStatus(); }, 30000);

  async function startExam() {
    const btn = $('#examstart');
    if (!confirm('캡스톤 시험을 시작한다.\n\n'
      + '· 랩이 초기화되고 서버가 고른 장애가 주입된다 (무엇인지는 알려주지 않는다)\n'
      + '· 주입이 끝나는 순간부터 시계가 간다\n'
      + '· 시간이 끝나면 그 순간의 검사 결과가 확정 성적이 된다\n'
      + '· 못 고친 것은 인계 보고서에 적으면 된다')) return;
    btn.disabled = true;
    consoleBox.classList.remove('collapsed');
    const r = await fetch('/exam/start', {
      method: 'POST', body: new URLSearchParams({ lab: labId() })
    });
    const j = await r.json();
    if (!r.ok) {
      paint('!! ' + (j.error || '시험을 시작하지 못했다'));
      if (r.status === 503) window.pveHealth?.show(j.health);
      btn.disabled = false;
      return;
    }
    jobinfo.textContent = '시험 준비';
    await stream(j.job_id);
    location.reload();               // 시계와 잠금이 함께 켜진다
  }

  document.addEventListener('click', e => {
    const b = e.target.closest('.actions .btn');
    if (b) { run(b.dataset.action); return; }
    if (e.target.closest('#examstart')) startExam();
  });

  $('#clearlog').onclick = () => log.textContent = '';
  $('#togglelog').onclick = () => {
    consoleBox.classList.toggle('collapsed');
    $('#togglelog').textContent = consoleBox.classList.contains('collapsed') ? '펼치기' : '접기';
  };
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !$('#modal').hidden) $('#modal-cancel').click();
  });

  bind();
})();
