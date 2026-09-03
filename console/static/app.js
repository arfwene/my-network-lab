/* my-network-lab console — 의존성 없는 클라이언트 (CDN 불필요) */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const log = $('#log'), jobinfo = $('#jobinfo'), consoleBox = $('#console');
  // 관리자에게만 있는 버튼. 가려서 받은 로그를 일부러 다시 받을 때 쓴다.
  const rawlog = $('#rawlog');
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
  //  문서만 갈아 끼우면 **오른쪽 실행 패널은 앞 모듈 것이 그대로 남는다.**
  //  패널의 내용은 그 모듈의 단계에 달려 있다 — 중간 점검 단추(M3·M7 에만),
  //  캡스톤 칸, 그리고 시나리오 목록(그 단계에 없는 장비는 잠긴다).
  //  실제로 M3 에서 M4 로 넘어가도 [중간 점검 시작] 이 남아 있었다.
  //  그래서 패널도 서버가 그 모듈로 그린 것으로 통째로 바꾼다.
  async function refreshSide(id) {
    if (es) return false;                 // 작업이 도는 중에는 건드리지 않는다
    try {
      const html = await fetch(`/?lab=${labId()}&m=${encodeURIComponent(id)}`)
        .then(r => r.ok ? r.text() : null);
      if (!html) return false;
      const side = new DOMParser().parseFromString(html, 'text/html').querySelector('.side');
      if (!side) return false;
      $('.side').innerHTML = side.innerHTML;
      return true;
    } catch (e) { return false; }
  }

  async function loadModule(id, kind = 'README') {
    const r = await fetch(`/m/${encodeURIComponent(id)}?lab=${labId()}&kind=${kind}`);
    $('#module').innerHTML = await r.text();
    $$('.mod').forEach(b => b.classList.toggle('on', b.dataset.module === id));
    const swapped = await refreshSide(id);
    const stage = $('.tabs')?.dataset.stage;
    if (stage) {
      if ($('.actions')) $('.actions').dataset.stage = stage;
      const small = $('.side h3 small');
      if (small) small.textContent = stage;
      // 패널을 통째로 바꿨으면 토폴로지도 그 안에 이미 그려져 있다.
      if (!swapped) {
        fetch(`/topology.svg?stage=${stage}`).then(r => r.text()).then(s => $('#topo').innerHTML = s);
      }
    }
    paintLabStage();
    const cap = $('.actions')?.dataset.capstone;
    if ($('#capstone')) $('#capstone').hidden = (id !== cap);
    const u = new URL(location);
    u.searchParams.set('m', id); u.searchParams.set('lab', labId());
    history.replaceState({}, '', u);
    bind();
  }

  // 다른 탭·다른 모듈로 보내는 단추를 묶는다.
  //   [data-kind] — 같은 모듈의 다른 탭   [data-goto] — 다음 모듈
  // 결과 화면은 채점 뒤에 통째로 갈아 끼운다. 그때 다시 묶지 않으면 그 안의
  // 단추는 눌러도 아무 일도 일어나지 않는다. 실제로 퀴즈를 통과한 뒤 [과제]
  // 단추가 그렇게 죽어 있었다 — 갈아 끼운 자리에서 [data-goto] 만 다시
  // 묶고 있었기 때문이다. 그래서 묶는 일을 한 곳에 모은다.
  function bindJumps(root) {
    const r = root || document;
    r.querySelectorAll('[data-kind]:not(.tab)').forEach(b =>
      b.onclick = () => loadModule(curModule(), b.dataset.kind));
    r.querySelectorAll('[data-goto]').forEach(b =>
      b.onclick = () => loadModule(b.dataset.goto));
  }

  function bind() {
    $$('.tab').forEach(t => t.onclick = () => loadModule(curModule(), t.dataset.kind));
    // 탭으로 보내는 단추들 — 과제 끝의 [검증], 퀴즈 통과 뒤의 [과제].
    // 탭과 같은 곳으로 간다. 길이 둘이면 둘이 어긋나는 날이 온다.
    bindJumps();
    // 교재 본문의 `#` 링크. 두 종류를 처리한다.
    //   #tasks · #quiz · #README  → 그 탭으로 간다
    //   #5-장애-연습-…            → **교재 탭으로 옮긴 뒤** 그 절로 스크롤한다
    // 두 번째가 없으면, 과제 탭에서 "교재 5장을 보라" 는 링크를 눌러도 아무
    // 일도 일어나지 않는다 — 그 절은 지금 화면에 없기 때문이다.
    // 교재는 웹과 인쇄본 양쪽으로 나가므로 인쇄본에서는 그냥 앵커로 남는다.
    $$('.doc a[href^="#"]').forEach(a => {
      const frag = decodeURIComponent(a.getAttribute('href').slice(1));
      if (['tasks', 'quiz', 'README', 'verify'].includes(frag)) {
        a.onclick = e => { e.preventDefault(); loadModule(curModule(), frag); };
        return;
      }
      a.onclick = async e => {
        e.preventDefault();
        if (!document.getElementById(frag)) await loadModule(curModule(), 'README');
        const el = document.getElementById(frag);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      };
    });
    const f = $('#quizform');
    if (f) f.onsubmit = submitAssessment;
    // 배너의 [단계 올리기] 는 옆의 [이 모듈 적용] 과 같은 것이다.
    // 따로 요청을 만들지 않는다 — 확인 창·시험 잠금·로그 처리를 두 벌 두지 않기 위해.
    const up = $('#stage-up');
    if (up) up.onclick = () => run('reset');
  }

  // ------------------------------------------------------ 화이트모드 / 다크모드
  //  값은 이 브라우저에만 남는다. 서버에 저장하지 않는 이유는 _theme.html 에 적어 뒀다.
  //  <head> 의 짧은 스크립트가 이미 표시를 박아 뒀다 — 여기서는 단추만 맡는다.
  //  단추 글자는 CSS 가 고른다(_theme.html 참고). 여기서는 표시를 바꾸고 적어 둘 뿐이다.
  const themebtn = $('#themebtn');
  if (themebtn) themebtn.onclick = () => {
    const root = document.documentElement;
    // 기본은 화이트다. 표시가 없으면 화이트로 보고 다크로 넘긴다.
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {}
  };

  // ------------------------------------------------------------ 그림 크게 보기
  //  토폴로지 SVG 는 선이 가늘어 작게 나오면 읽을 수 없다. 원본을 복제해 띄운다 —
  //  옮기면 원래 자리가 비고, 닫을 때 되돌려 놓는 일이 남는다.
  const lb = $('#lightbox');
  function zoom(el, href) {
    if (!lb) return;
    const inner = lb.querySelector('.lb-inner');
    inner.innerHTML = '';
    inner.appendChild(el.cloneNode(true));
    // 우리가 그린 그림에만 내려받기를 연다. 교재에 끼워 넣은 일반 그림은 받을 것이 없다.
    const dl = lb.querySelector('.lb-dl');
    if (dl) { if (href) dl.href = href; dl.hidden = !href; }
    lb.hidden = false;
  }
  //  토폴로지는 단계로, 교재 구성도는 '모듈:몇 번째' 로 가리킨다.
  function diaHref(el) {
    if (el.dataset.stage) return '/topology.drawio?stage=' + encodeURIComponent(el.dataset.stage);
    if (el.dataset.dia) {
      const [m, n] = el.dataset.dia.split(':');
      return '/diagram.drawio?module=' + encodeURIComponent(m) + '&n=' + encodeURIComponent(n);
    }
    return '';
  }
  function unzoom() { if (lb) { lb.hidden = true; lb.querySelector('.lb-inner').innerHTML = ''; } }
  document.addEventListener('click', e => {
    if (lb && !lb.hidden) {
      // 내려받기는 화면을 닫지 않는다 — 눌렀는데 그림이 사라지면 실패로 보인다.
      if (e.target.closest('.lb-dl')) return;
      // 그림 자체를 누른 것이 아니면 닫는다 (바깥 · [닫기] 둘 다).
      if (!e.target.closest('.lb-inner') || e.target.closest('.lb-close')) unzoom();
      return;
    }
    const pic = e.target.closest('.topo-wrap, .topo-box, .dia-wrap, .doc img');
    if (pic) zoom(pic.matches('img') ? pic : (pic.querySelector('svg') || pic), diaHref(pic));
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
    // 어느 탭에서 냈는지 함께 보낸다 — 결과 화면이 '다음 한 걸음' 을 그것으로 고른다.
    // 이 값이 없으면 퀴즈를 100% 로 통과하고도 "고친 뒤 다시 제출해 주세요" 가 뜬다.
    const kind = $('.tab.on')?.dataset.kind || form.dataset.phase || '';
    const r = await fetch(`/m/${mid}/result?lab=${labId()}&kind=${encodeURIComponent(kind)}`);
    $('#assessresult').innerHTML = await r.text();
    bindJumps($('#assessresult'));
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

  // 경과 시간은 **브라우저가 1초마다 센다.** 서버 값만 쓰면 조용할 때 15초씩
  // 건너뛰고, 로그가 쏟아지는 동안에는 아예 멈춘 것처럼 보인다 — 실제로
  // [랩 삭제] 에서 그렇게 보였다. 서버가 보내 줄 때는 그 값으로 시계를 맞춘다.
  let clock = null, t0 = 0, lastOut = 0;

  function showElapsed() {
    const el = (Date.now() - t0) / 1000;
    const q = (Date.now() - lastOut) / 1000;
    jobinfo.textContent = `진행 중 · ${fmt(el)} 경과`
      + (q > 20 ? ` · 마지막 출력 ${fmt(q)} 전` : '');
  }

  function startClock() {
    t0 = lastOut = Date.now();
    clearInterval(clock);
    clock = setInterval(showElapsed, 1000);
    showElapsed();
  }

  function stopClock() { clearInterval(clock); clock = null; }

  function stream(jobId, reveal) {
    return new Promise(resolve => {
      if (es) es.close();
      startClock();
      if (rawlog && !reveal) rawlog.hidden = true;
      es = new EventSource(`/jobs/${jobId}/stream` + (reveal ? '?reveal=1' : ''));
      es.onmessage = ev => { lastOut = Date.now(); paint(JSON.parse(ev.data)); };
      // 서버가 알려 주는 진짜 경과로 시계를 맞춘다. 새로고침해서 도중에 붙었거나
      // 브라우저가 절전으로 멈췄던 경우를 여기서 바로잡는다.
      es.addEventListener('tick', ev => {
        const d = JSON.parse(ev.data);
        t0 = Date.now() - d.elapsed * 1000;
        lastOut = Date.now() - d.quiet * 1000;
        showElapsed();
      });
      es.addEventListener('done', ev => {
        const d = JSON.parse(ev.data);
        stopClock();
        jobinfo.textContent = `${d.action} · ${d.status} · ${fmt(d.elapsed)}`;
        // 가려서 받은 로그였다면 관리자에게만 다시 받을 길을 열어 둔다.
        if (rawlog && d.secret && !reveal) { rawlog.dataset.job = d.id; rawlog.hidden = false; }
        es.close(); es = null;
        refreshStatus();
        resolve(d);
      });
      es.onerror = () => {
        stopClock();
        paint('!! 로그 스트림이 끊겼습니다'); es.close(); es = null; resolve(null);
      };
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

  // 로그 콘솔은 **index.html 에만** 있다. 설치·관리 화면에는 없다.
  //   가드 없이 만지면 그 줄에서 예외가 나고, 이 파일의 **나머지가 통째로 안 돈다** —
  //   아래 window.copyText 대입까지 못 가서 그 화면의 [복사] 가 죽는다.
  //   페이지마다 있는 것이 다르므로 여기서는 늘 있는지부터 본다.
  if (log && consoleBox) {
    $('#clearlog')?.addEventListener('click', () => { log.textContent = ''; });
    $('#togglelog')?.addEventListener('click', () => {
      consoleBox.classList.toggle('collapsed');
      $('#togglelog').textContent = consoleBox.classList.contains('collapsed') ? '펼치기' : '접기';
    });
  }
  if (rawlog) rawlog.onclick = async () => {
    rawlog.hidden = true;
    paint('$ ── 원본 로그 (관리자) ── 여기부터는 무엇을 주입했는지 그대로 보입니다');
    await stream(rawlog.dataset.job, true);
  };
  document.addEventListener('keydown', e => {
    const m = $('#modal');
    if (e.key === 'Escape' && m && !m.hidden) $('#modal-cancel')?.click();
  });

  // 클립보드 복사. **HTTP 로 서비스하면 navigator.clipboard 가 아예 없다** —
  // 브라우저가 보안 컨텍스트(HTTPS·localhost)에서만 내주기 때문이다. 이 콘솔은
  // 사내 IP 의 8080 으로 뜨므로 해당되지 않는다. 그래서 [복사] 를 눌러도
  // TypeError 만 나고 화면에는 아무 일도 일어나지 않았다 — 계정 만들고 임시
  // 비밀번호를 복사하려던 관리자가 그걸 만났다.
  //
  // 셋을 차례로 시도한다. 마지막은 "직접 Ctrl+C 하세요" 라고 말하되,
  // 적어도 선택은 해 준다.
  async function copyText(text, el) {
    const say = (m) => {
      if (!el) return;
      const t0 = el.dataset.label || el.textContent;
      el.dataset.label = t0;
      el.textContent = m;
      setTimeout(() => { el.textContent = el.dataset.label; }, 1600);
    };
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        say('복사됨');
        return true;
      }
    } catch (e) { /* 아래로 */ }
    // execCommand 는 지원이 끊겨 가지만 HTTP 에서도 아직 동작한다.
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) { say('복사됨'); return true; }
    } catch (e) { /* 아래로 */ }
    // 둘 다 막혔다. 값을 골라 놓고 사람이 복사하게 한다.
    const src = el && el.closest('.pwbox') ? el.closest('.pwbox').querySelector('[data-pw]') : null;
    if (src) {
      // 가려 둔 상태라면 값을 드러낸다. 안 그러면 점만 선택된다.
      src.textContent = text;
      const r = document.createRange();
      r.selectNodeContents(src);
      const s = window.getSelection();
      s.removeAllRanges(); s.addRange(r);
    }
    say('Ctrl+C 로 복사');
    return false;
  }
  window.copyText = copyText;

  bind();
})();
