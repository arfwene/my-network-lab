/* my-network-lab console — 의존성 없는 클라이언트 (CDN 불필요) */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const log = $('#log'), jobinfo = $('#jobinfo'), consoleBox = $('#console');
  let es = null;

  const labId = () => $('.actions')?.dataset.lab || '1';
  const curModule = () => $('.tabs')?.dataset.module || '';
  // 랩 단계와 이 모듈의 관계. 서버가 _module.html 에 실어 보낸다.
  const gapDir = () => $('.tabs')?.dataset.gap || 'ok';
  const need = () => $('.tabs')?.dataset.stage || '';
  const now = () => $('.tabs')?.dataset.now || '';

  // 헤더의 랩 단계 칩. 모듈을 옮기거나 작업이 끝날 때마다 다시 칠한다.
  function paintLabStage() {
    const el = $('#labstage');
    if (!el) return;
    const ok = gapDir() === 'ok', n = now(), s = el.querySelector('.need');
    el.classList.toggle('off', !ok);
    el.querySelector('.now').textContent = n ? n.toUpperCase() : '미적용';
    s.textContent = ok ? '' : `\u00b7 이 모듈 ${need().toUpperCase()}`;
    s.hidden = ok;
  }

  // ------------------------------------------------------------ 확인 모달
  //  경고 문구 + 영향 범위를 보여주고, 최종 확인 버튼을 따로 누르게 한다.
  const MODAL = {
    // 버튼은 하나지만 랩이 어디에 있느냐에 따라 하는 일의 의미가 다르다.
    // (동작은 언제나 같다 — reset.yml 이 흔적을 지우고 site.yml 을 다시 올린다)
    reset: {
      title: () => ({
        behind: `랩을 ${need().toUpperCase()} 단계까지 올린다`,
        ok: '이 모듈을 초기 상태로 되돌립니다',
        ahead: `랩을 ${need().toUpperCase()} 단계로 되돌린다`
      })[gapDir()],
      body: () => `<ul>
        ${{
          behind: `<li>이 단계에서 등장하는 <b>장비가 깨어나고</b>, 주소와 라우팅이 올라갑니다</li>
                   <li>건너뛴 단계도 함께 들어갑니다 — 설정은 누적입니다</li>`,
          ok: '',
          ahead: `<li>지금 랩은 <b>${(now() || '?').toUpperCase()}</b> 다.
                      뒤 모듈에서 올린 설정(라우팅 · 방화벽 · 서비스)이 <b>지워집니다</b></li>`
        }[gapDir()]}
        <li>실습 중 만든 설정이 <b>모두 사라집니다</b> — 추가한 주소, 라우팅, 정적 ARP, 임시로 꽂은 스위치 포트</li>
        <li>주입된 장애도 함께 해제됩니다</li>
        <li>VM 은 지워지지 않습니다. 설정만 이 모듈의 시작 상태가 됩니다</li>
        <li><b>퀴즈 점수와 제출 이력은 그대로 남습니다</b></li>
      </ul>`,
      ok: () => gapDir() === 'behind' ? '적용합니다' : '정말 되돌립니다'
    },
    // 중간 점검. 시작하면 랩이 이 모듈의 시작 상태로 맞춰진 뒤 장애가 들어간다 —
    // 그래야 "지금 안 되는 것" 이 실습 중 만든 것인지 주입된 것인지 헷갈리지 않는다.
    drill: {
      title: '중간 점검을 시작합니다',
      body: `<ul>
        <li>랩이 <b>이 모듈의 시작 상태</b>로 맞춰집니다 — 실습 중 만든 설정은 사라집니다</li>
        <li>그 뒤 <b>서버가 고른 장애</b>가 들어갑니다. <b>무엇인지 알려주지 않습니다</b></li>
        <li>시계도 성적도 없습니다. 몇 번이든 다시 할 수 있습니다</li>
        <li>랩은 <b>터미널에서</b> 고칩니다</li>
      </ul>`,
      ok: '시작합니다'
    },
    'drill-end': {
      title: '정답을 보고 복구합니다',
      body: `<ul>
        <li>무엇이 주입됐는지 <b>로그에 그대로 나옵니다</b>. 이 회차는 여기서 끝납니다</li>
        <li>아직 안 눌러 봤다면 <b>[결과 확인]</b> 과 <b>[힌트]</b> 가 먼저입니다</li>
        <li>이미 손으로 고쳤어도 괜찮습니다 — 복구는 여러 번 돌려도 됩니다</li>
      </ul>`,
      ok: '정답을 봅니다'
    },
    destroy: {
      title: '랩의 VM 을 전부 삭제합니다',
      body: `<ul>
        <li>이 랩의 <b>가상 머신과 브리지가 삭제됩니다</b></li>
        <li>다시 쓰려면 [랩 생성] 으로 처음부터 만들어야 합니다 (수 분 소요)</li>
        <li>제출 이력은 남습니다</li>
      </ul>`,
      ok: '정말 삭제합니다'
    }
  };

  function confirmAction(kind, scope) {
    return new Promise(resolve => {
      // 문구는 값일 수도, 랩 상태를 보고 만드는 함수일 수도 있다.
      const m = MODAL[kind], txt = x => typeof x === 'function' ? x() : x;
      $('#modal-title').textContent = txt(m.title);
      $('#modal-body').innerHTML = txt(m.body);
      $('#modal-scope').textContent = scope;
      $('#modal-ok').textContent = txt(m.ok);
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
    paintLabStage();
    const cap = $('.actions')?.dataset.capstone;
    if ($('#capstone')) $('#capstone').hidden = (id !== cap);
    const u = new URL(location);
    u.searchParams.set('m', id); u.searchParams.set('lab', labId());
    history.replaceState({}, '', u);
    bind();
  }

  function bind() {
    $$('.tab').forEach(t => t.onclick = () => loadModule(curModule(), t.dataset.kind));
    // 과제 탭 끝의 [검증하러 가기]. 탭과 같은 곳으로 간다 — 길이 둘이면
    // 둘이 어긋나는 날이 온다.
    $$('.next-step [data-kind]').forEach(b =>
      b.onclick = () => loadModule(curModule(), b.dataset.kind));
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
    // 배너의 [단계 올리기] 는 옆의 [이 모듈 적용] 과 같은 것이다.
    // 따로 요청을 만들지 않는다 — 확인 창·시험 잠금·로그 처리를 두 벌 두지 않기 위해.
    const up = $('#stage-up');
    if (up) up.onclick = () => run('reset');
  }

  // ------------------------------------------------------------ 그림 크게 보기
  //  토폴로지 SVG 는 선이 가늘어 작게 나오면 읽을 수 없다. 원본을 복제해 띄운다 —
  //  옮기면 원래 자리가 비고, 닫을 때 되돌려 놓는 일이 남는다.
  const lb = $('#lightbox');
  function zoom(el) {
    if (!lb) return;
    const inner = lb.querySelector('.lb-inner');
    inner.innerHTML = '';
    inner.appendChild(el.cloneNode(true));
    lb.hidden = false;
  }
  function unzoom() { if (lb) { lb.hidden = true; lb.querySelector('.lb-inner').innerHTML = ''; } }
  document.addEventListener('click', e => {
    if (lb && !lb.hidden) {
      // 그림 자체를 누른 것이 아니면 닫는다 (바깥 · [닫기] 둘 다).
      if (!e.target.closest('.lb-inner') || e.target.closest('.lb-close')) unzoom();
      return;
    }
    const pic = e.target.closest('.topo-wrap, .topo-box, .doc img');
    if (pic) zoom(pic.matches('img') ? pic : (pic.querySelector('svg') || pic));
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') unzoom(); });

  // 헤더 묶음 메뉴는 한 번에 하나만 열린다. 하나가 열려 있는 채로 다른 것을
  // 누르면, 열린 메뉴가 깔아 둔 덮개가 그 클릭을 먼저 먹어서 두 번 눌러야 한다.
  document.addEventListener('click', e => {
    const sum = e.target.closest('.menu > summary');
    document.querySelectorAll('.menu[open]').forEach(d => {
      if (!sum || d !== sum.parentElement) d.removeAttribute('open');
    });
  }, true);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') document.querySelectorAll('.menu[open]').forEach(d => d.removeAttribute('open'));
  });

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
  const LOG_MAX = 4000;          // 화면에 남길 줄 수. 넘으면 앞에서 버린다.

  function lineNode(line) {
    const span = document.createElement('span');
    if (line.startsWith('$ ')) span.className = 'cmd';
    else if (line.startsWith('!!')) span.className = 'err';
    else if (line.startsWith('== ')) span.className = line.includes('완료') ? 'ok' : 'err';
    else if (/^\s*\[PASS\]/.test(line)) span.className = 'ok';
    else if (/^\s*\[FAIL\]/.test(line)) span.className = 'err';
    else if (/^(TASK|PLAY)/.test(line)) span.className = 'task';
    else if (/failed=[1-9]|fatal:|ERROR/.test(line)) span.className = 'err';
    span.textContent = line + '\n';
    return span;
  }

  // 여러 줄을 **한 번에** 붙인다.
  //   전에는 줄마다 appendChild 하고 줄마다 scrollHeight 를 읽었다.
  //   scrollHeight 읽기는 그 자리에서 레이아웃을 강제한다 — terraform 이 수천 줄을
  //   쏟으면 그만큼 강제 레이아웃이 일어나 탭이 통째로 굳는다.
  //   이제 조각 하나를 fragment 로 모아 한 번 붙이고, 스크롤도 한 번만 건드린다.
  function paint(lines) {
    if (typeof lines === 'string') lines = [lines];
    if (!lines.length) return;
    const frag = document.createDocumentFragment();
    for (const l of lines) frag.appendChild(lineNode(l));
    log.appendChild(frag);
    while (log.childNodes.length > LOG_MAX) log.removeChild(log.firstChild);
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
      es.onmessage = ev => paint(JSON.parse(ev.data));
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
      es.onerror = () => { paint('!! 로그 스트림이 끊겼습니다'); es.close(); es = null; resolve(null); };
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
    $$('.actions .btn, #stage-up').forEach(x => x.disabled = true);
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
    try {
      await stream(j.job_id);
      // 중간 점검은 실행 패널 자체가 바뀐다(시작 <-> 끝내기). 부분 갱신으로는 못 맞춘다.
      if (action === 'drill' || action === 'drill-end') { location.reload(); return; }
      // 검사를 통과했으면 패널 자체가 사라진다. 통과 못 했으면 그대로 두고 로그만 남긴다.
      if (action === 'drill-check') { await refreshStatus(); if (await drillGone()) location.reload(); return; }
      // 단계가 올라갔으면 배너와 헤더 칩이 거짓말을 하고 있다. 조각을 다시 받아 맞춘다.
      // (퀴즈 제출 경로에서는 하지 않는다 — 채점 결과가 화면에서 날아간다)
      if (curModule()) await loadModule(curModule(), $('.tab.on')?.dataset.kind || 'README');
    } finally {
      // 여기서 빠지면 버튼이 잠긴 채로 남는다. 새로고침 말고는 되살릴 길이 없다.
      restoreActions();
    }
  }

  // ------------------------------------------------------------ 시험 세션
  //  잠금의 본체는 서버(exam.gate)다. 여기서 하는 일은 남은 시간을 보여주고
  //  단계가 바뀐 순간 화면을 서버 상태에 맞추는 것뿐이다.
  let lastPhase = $('#exambar')?.dataset.phase || 'none';

  // ------------------------------------------------------------ 중간 점검
  //  힌트는 서버가 단계별로 내준다. 정답 줄은 창구가 아예 내보내지 않는다.
  async function drillGone() {
    const r = await fetch(`/status?lab=${labId()}`);
    return !(await r.text()).includes('중간 점검 중');
  }

  $('#hintbtn')?.addEventListener('click', async e => {
    const btn = e.currentTarget, box = $('#hints');
    btn.disabled = true;
    try {
      const r = await fetch('/drill/hint', {
        method: 'POST', body: new URLSearchParams({ lab: labId() })
      });
      const j = await r.json();
      if (!r.ok) { paint('!! ' + (j.error || '힌트를 받지 못했습니다')); return; }
      const el = document.createElement('p');
      el.className = 'hint';
      el.innerHTML = j.done
        ? `<b>더 없습니다</b> ${j.text}`
        : `<b>힌트 ${j.level} · ${j.title}</b> ${j.text}`;
      box.appendChild(el);
      btn.textContent = j.done ? '힌트를 다 봤습니다' : '다음 힌트 보기';
      if (j.done) return;                       // 마지막이면 잠근 채로 둔다
    } finally {
      if (btn.textContent !== '힌트를 다 봤습니다') btn.disabled = false;
    }
  });

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
    // 배너 버튼도 같이 잠갔다가 같이 푼다. 여기 빠뜨리면 작업이 도는 동안
    // 배너만 눌리고, 서버가 "다른 작업이 실행 중이다" 로 되받는다.
    const up = $('#stage-up');
    if (up) up.disabled = locked;
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
    if (!confirm('캡스톤 시험을 시작합니다.\n\n'
      + '· 랩이 초기화되고 서버가 고른 장애가 주입됩니다 (무엇인지는 알려주지 않습니다)\n'
      + '· 주입이 끝나는 순간부터 시계가 간다\n'
      + '· 시간이 끝나면 그 순간의 검사 결과가 확정 성적이 된다\n'
      + '· 못 고친 것은 인계 보고서에 적으면 됩니다')) return;
    btn.disabled = true;
    consoleBox.classList.remove('collapsed');
    const r = await fetch('/exam/start', {
      method: 'POST', body: new URLSearchParams({ lab: labId() })
    });
    const j = await r.json();
    if (!r.ok) {
      paint('!! ' + (j.error || '시험을 시작하지 못했습니다'));
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
