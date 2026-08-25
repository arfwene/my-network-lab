/* Proxmox 연결 상태 — 헤더 칩 + 팝업. 모든 화면에서 함께 쓴다.
   실패했을 때 "왜 안 되는지"를 화면을 옮기지 않고 바로 볼 수 있어야 한다. */
(() => {
  const $ = s => document.querySelector(s);
  const chip = $('#pvechip'), box = $('#pvebox');
  if (!chip && !box) return;

  const LEVEL = { ok: '정상', warn: '주의', error: '오류', unknown: '확인 중' };
  const MARK = { ok: '✔', warn: '!', error: '✕', skip: '·' };
  let last = null, timer = null;

  function paintChip(h) {
    if (!chip) return;
    const lv = h ? h.level : 'unknown';
    chip.className = `hchip ${lv}`;
    chip.querySelector('.htext').textContent = `Proxmox ${LEVEL[lv]}`;
    chip.title = h ? `${h.summary} (${h.checked_at})` : 'Proxmox 연결 상태';
  }

  function paintBox(h) {
    if (!box || !h) return;
    $('#pvebox-sum').className = `pve-sum ${h.level}`;
    $('#pvebox-sum').textContent = h.summary;
    $('#pvebox-list').innerHTML = h.checks.map(c => `
      <li class="${c.status}">
        <span class="m">${MARK[c.status] || '·'}</span>
        <div>
          <b>${esc(c.title)}</b>
          <div class="d">${esc(c.detail || '')}</div>
          ${c.hint ? `<div class="h">→ ${esc(c.hint)}</div>` : ''}
        </div>
      </li>`).join('');
    $('#pvebox-when').textContent =
      `${h.endpoint} · 노드 ${h.node} · ${h.checked_at} · ${h.elapsed_ms}ms`
      + (h.confirmed ? '' : ' · 관리자 확인 전');
    // 교육생 화면에는 이 링크 자체가 없다 (base.html 이 관리자에게만 그린다).
    const fix = $('#pvebox-fix');
    if (fix) fix.hidden = !h.can_fix;
  }

  const esc = s => String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  async function refresh(force = false) {
    try {
      const r = await fetch('/health/pve' + (force ? '?force=1' : ''));
      if (r.status === 401) { clearInterval(timer); return null; }
      last = await r.json();
      paintChip(last); paintBox(last);
      return last;
    } catch (e) { return null; }
  }

  function open() {
    if (!box) return;
    box.hidden = false;
    if (last) paintBox(last); else refresh();
  }
  const close = () => { if (box) box.hidden = true; };

  chip?.addEventListener('click', open);
  $('#pvebox-close')?.addEventListener('click', close);
  $('#pvebox-recheck')?.addEventListener('click', async e => {
    e.target.disabled = true;
    $('#pvebox-sum').textContent = '점검 중…';
    await refresh(true);
    e.target.disabled = false;
  });
  box?.addEventListener('click', e => { if (e.target.id === 'pvebox') close(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && box && !box.hidden) close();
  });

  /* 작업이 Proxmox 문제로 거부됐을 때 app.js 가 부른다 */
  window.pveHealth = {
    refresh, open, close,
    show(h) { if (h) { last = h; paintChip(h); } open(); }
  };

  refresh();
  timer = setInterval(() => refresh(), 30000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
})();
