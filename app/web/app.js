/* 乐清理 — 前端逻辑（原生 JS，无外部依赖） */
'use strict';

const S = {
  meta: null,
  result: null,
  drive: 'C:',
  sortKey: 'size',
  sortDir: 'desc',
  scanRows: [],
  selected: new Set(),      // 清理模块勾选
  groups: {},               // drive -> groups
  ctxTarget: null,
  volumes: [],
  lockTarget: '',
  openGroups: new Set(),    // 已展开的分组（重渲染时保留展开状态）
  groupSort: { key: 'size', dir: 'desc' },   // 清理模块组内排序
  swFilter: 'all',          // 软件清单分类：all / app / system
  swSort: { key: 'size', dir: 'desc' },      // 软件清单排序
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function api() { return window.pywebview && window.pywebview.api; }

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, ms);
}

function human(n) {
  n = Number(n || 0);
  const u = [['TB', 1099511627776], ['GB', 1073741824], ['MB', 1048576], ['KB', 1024]];
  for (const [s, d] of u) if (Math.abs(n) >= d) return (n / d).toFixed(2) + ' ' + s;
  return Math.round(n) + ' B';
}

/* ============================ 初始化 ============================ */
async function init() {
  bindNav();
  bindEvents();
  window.__evt = onEvent;
  try {
    S.meta = await api().get_meta();
    renderMeta();
  } catch (e) {
    toast('初始化失败：' + e);
  }
  if (window.__MOCK_MODE__) setTimeout(() => startScan(), 300);
}

function bindNav() {
  $$('#nav .navbtn').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.classList.contains('locked')) { toast('请先完成扫描'); return; }
      showView(btn.dataset.view);
    });
  });
}

function showView(name) {
  $$('.navbtn').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  if (name === 'diff') loadDiff();
  if (name === 'settings') loadSettings();
  if (name === 'about') loadAbout();
}

function unlock(...views) {
  views.forEach(v => {
    const b = $(`.navbtn[data-view="${v}"]`);
    if (b) b.classList.remove('locked');
  });
}

/* ============================ 元信息渲染 ============================ */
function renderMeta() {
  const m = S.meta;
  $('#brandSub').textContent = `v${m.version} · 便携版 · 只读扫描 · 回收站可恢复`;

  const ce = $('#chipEngine');
  if (m.wiztree_ok) {
    ce.className = 'chip ok';
    ce.textContent = `引擎：WizTree 就绪（${m.wiztree_name}）`;
    ce.title = m.wiztree;
  } else {
    ce.className = 'chip bad';
    ce.textContent = '引擎：未检测到 WizTree';
    ce.title = '请把 WizTree 便携版放到程序目录，或在“设置”中指定路径';
  }
  const ca = $('#chipAdmin');
  ca.className = 'chip ' + (m.is_admin ? 'ok' : 'warn');
  ca.textContent = m.is_admin ? '权限：管理员' : '权限：普通用户（可扫描，速度略慢）';

  // 盘符选择（动态枚举全部磁盘）
  renderVolumes(m.volumes || []);
  renderDiskSummary(m);

  const sc = $('#spaceCard');
  sc.innerHTML = (m.volumes || []).filter(v => v.ready).map(v => `
    <div style="margin-bottom:8px">
      <b>${esc(v.letter)}</b> ${esc(v.label || '')} ${esc(v.free_h)} 可用
      <div class="bar"><i style="width:${Math.max(2, 100 - v.pct_free)}%"></i></div>
    </div>`).join('') +
    `<div style="color:#94a3b8">分区 ${(m.volumes || []).length} 个 · 物理磁盘 ${(m.physical || {}).count || 0} 块
     <br>快照 ${m.snapshot_count} 份${m.last_snapshot ? ' · 最近 ' + esc(m.last_snapshot) : ''}</div>`;

  $('#wiztreePath').value = m.wiztree || '';
  renderWhitelist(m.whitelist || []);
}

function renderVolumes(vols) {
  S.volumes = vols;
  const dp = $('#drivePick');
  if (!dp) return;
  dp.innerHTML = '';
  if (!vols.length) { dp.innerHTML = '<span class="muted">未检测到任何磁盘</span>'; return; }
  vols.forEach(v => {
    const lb = document.createElement('label');
    lb.className = 'drivechk' + (v.scannable ? ' on' : ' dis');
    const meta = v.ready
      ? `${v.label || v.type} · ${v.fs || '?'} · ${v.free_h} 可用 / ${v.total_h}（${v.pct_free}% 空闲）`
      : (v.type + ' · ' + (v.note || '不可用'));
    lb.innerHTML = `<input type="checkbox" value="${esc(v.letter)}"
        ${v.scannable ? 'checked' : 'disabled'}>
      <span><b>${esc(v.letter)}</b> <span class="meta">${esc(meta)}</span></span>`;
    if (!v.scannable) lb.title = v.note || '该磁盘不支持扫描';
    else if (v.note) lb.title = v.note;
    const cb = lb.querySelector('input');
    cb.addEventListener('change', (e) => lb.classList.toggle('on', e.target.checked));
    dp.appendChild(lb);
  });
}

function pickAllVolumes(state) {
  $$('#drivePick input').forEach(cb => {
    if (cb.disabled) return;
    cb.checked = state;
    cb.closest('.drivechk').classList.toggle('on', state);
  });
}

function renderDiskSummary(m) {
  const el = $('#diskSummary');
  if (!el) return;
  const vols = m.volumes || [];
  const phy = m.physical || {};
  const ready = vols.filter(v => v.ready);
  const totalFree = ready.reduce((a, b) => a + b.free, 0);
  const devs = (phy.devices || []).map(d => `Disk${d.number} ${d.size_h}`).join(' · ');
  const notes = vols.filter(v => v.note);
  el.innerHTML =
    `检测到 <b>${vols.length}</b> 个分区、<b>${phy.count || 0}</b> 块物理磁盘` +
    (devs ? `（${esc(devs)}）` : '') +
    `　|　合计可用 <b>${human(totalFree)}</b>` +
    (notes.length
      ? `<br>${notes.map(v => `⚠ ${esc(v.letter)} ${esc(v.note)}`).join('　')}`
      : '') +
    `<br>提示：非 NTFS（FAT32/exFAT/网络盘）WizTree 会走慢速遍历；光驱与未就绪的盘已自动禁用。`;
}

/* ============================ 事件 ============================ */
function bindEvents() {
  $('#btnScan').addEventListener('click', startScan);
  $('#btnPickAllVol').addEventListener('click', () => pickAllVolumes(true));
  $('#btnPickNoneVol').addEventListener('click', () => pickAllVolumes(false));
  $('#btnRescanVol').addEventListener('click', async () => {
    const r = await api().refresh_volumes();
    if (!S.meta) S.meta = {};
    S.meta.volumes = r.volumes;
    S.meta.physical = r.physical;
    renderVolumes(r.volumes);
    renderDiskSummary(S.meta);
    toast('已重新枚举磁盘');
  });
  $('#lockClose').addEventListener('click', () => { $('#lockModal').hidden = true; });

  // 关闭询问框
  $('#closeTray').addEventListener('click', () => closeChoose('tray'));
  $('#closeQuit').addEventListener('click', () => closeChoose('exit'));
  $('#closeCancel').addEventListener('click', () => { $('#closeModal').hidden = true; });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('#closeModal').hidden) $('#closeModal').hidden = true;
  });

  $('#soundToggle').addEventListener('change', async (e) => {
    const on = e.target.checked;
    await api().set_config({ sound_enabled: on });
    if (S.meta) S.meta.sound_enabled = on;
    const st = $('#soundState');
    if (st) st.textContent = on ? '已开启' : '已关闭';
    if (on) api().test_sound('start_scan');
  });
  $('#btnCancelScan').addEventListener('click', cancelTask);
  $('#btnCancelClean').addEventListener('click', cancelTask);
  $('#closeActionSel').addEventListener('change', async (e) => {
    const r = await api().set_close_action(e.target.value);
    if (!r.ok) { toast(r.error || '保存失败'); return; }
    if (S.meta) S.meta.tray = Object.assign({}, S.meta.tray, { close_action: r.close_action });
    toast('已保存');
  });
  $('#btnToClean').addEventListener('click', () => showView('clean'));
  $('#btnExportCsv').addEventListener('click', exportCsv);
  $('#btnSoftware').addEventListener('click', loadSoftware);
  $('#btnRefreshGroups').addEventListener('click', () => { if (S.result) renderGroups(); });
  $('#btnClean').addEventListener('click', doClean);
  $('#pickAll').addEventListener('change', (e) => {
    S.selected.clear();
    if (e.target.checked) {
      // 按组自动勾选：系统更新残留 + 疑似历史残留 + 低风险（这三组都是"可安全清理"的）。
      // ⚠️ 不要按 tier 过滤（tier<3 会把中风险捞进来，且残留组里的项可能被"索引未统计/近期修改"
      //     等理由提级成中风险导致漏选）—— 按组选就不会漏，中/高风险一律留给用户手动勾选。
      const data = (S.result && S.result.drives[S.drive]) || {};
      const gs = data.groups || {};
      ['cache', 'system_update', 'suspect', 'low'].forEach(key => {
        ((gs[key] || {}).items || []).forEach(it => { if (it.deletable) S.selected.add(it.path); });
      });
    }
    renderGroups();
    toast(e.target.checked ? `已选中 ${S.selected.size} 个可清理项（中/高风险项请手动勾选）` : '已取消全选');
  });

  $('#scanSearch').addEventListener('input', renderScanTable);
  $('#scanSourceFilter').addEventListener('change', renderScanTable);
  $('#scanTierFilter').addEventListener('change', renderScanTable);
  $('#swSearch').addEventListener('input', renderSoftware);

  // 软件清单：分类筛选（全部 / 应用 / 系统组件）
  $$('#swFilter .segbtn').forEach(btn => {
    btn.addEventListener('click', () => {
      S.swFilter = btn.dataset.swFilter;
      $$('#swFilter .segbtn').forEach(b => b.classList.toggle('active', b === btn));
      renderSoftware();
    });
  });
  // 软件清单：表头排序（名称 / 占用 / 安装时间）
  $$('#swTable th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.swSort;
      if (S.swSort.key === k) S.swSort.dir = S.swSort.dir === 'asc' ? 'desc' : 'asc';
      else { S.swSort.key = k; S.swSort.dir = (k === 'name') ? 'asc' : 'desc'; }
      $$('#swTable th.sortable').forEach(x => x.classList.remove('asc', 'desc'));
      th.classList.add(S.swSort.dir);
      renderSoftware();
    });
  });

  $$('#scanTable th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.sort;
      if (S.sortKey === k) S.sortDir = S.sortDir === 'asc' ? 'desc' : 'asc';
      else { S.sortKey = k; S.sortDir = (k === 'name') ? 'asc' : 'desc'; }
      $$('#scanTable th.sortable').forEach(x => x.classList.remove('asc', 'desc'));
      th.classList.add(S.sortDir);
      renderScanTable();
    });
  });

  $('#btnLocate').addEventListener('click', async () => {
    const r = await api().locate_wiztree();
    if (r.ok) { $('#wiztreePath').value = r.path; toast('已找到：' + r.path); }
    else toast('未找到 WizTree，请手动填写路径');
  });
  $('#btnSaveCfg').addEventListener('click', async () => {
    await api().set_config({ wiztree_path: $('#wiztreePath').value.trim() });
    S.meta = await api().get_meta();
    renderMeta();
    toast('已保存');
  });
  $('#btnAddWl').addEventListener('click', async () => {
    const v = $('#wlInput').value.trim();
    if (!v) return;
    const r = await api().add_whitelist(v);
    renderWhitelist(r.items);
    $('#wlInput').value = '';
    toast('已加入白名单');
  });

  document.addEventListener('click', hideCtx);
  document.addEventListener('scroll', hideCtx, true);
  window.addEventListener('resize', hideCtx);
  $('#modalCancel').addEventListener('click', () => { $('#modal').hidden = true; });
}

/* ============================ 进度事件 ============================ */
function onEvent(msg) {
  const p = msg.payload || {};
  if (msg.event === 'progress') {
    $('#progressWrap').hidden = false;
    if (typeof p.pct === 'number') $('#progressBar').style.width = Math.min(100, p.pct) + '%';
    $('#progressText').textContent = p.msg || '';
    const cw = $('#cleanProgressWrap');
    if (cw) {
      cw.hidden = false;
      if (typeof p.pct === 'number') $('#cleanProgressBar').style.width = Math.min(100, p.pct) + '%';
      $('#cleanProgressText').textContent = p.msg || '';
    }
    appendLog(p.msg);
  } else if (msg.event === 'error') {
    toast('出错：' + (p.msg || '未知错误'), 6000);
    $('#progressText').textContent = '出错：' + (p.msg || '');
    appendLog('ERROR ' + (p.msg || ''));
  } else if (msg.event === 'scan_result') {
    onScanResult(p);
  } else if (msg.event === 'software_result') {
    onSoftwareResult(p);
  } else if (msg.event === 'cleanup_result') {
    onCleanupResult(p);
  } else if (msg.event === 'lock_result') {
    showLockDialog((p.paths || [])[0] || S.lockTarget, p);
  } else if (msg.event === 'unlock_result') {
    onUnlockResult(p);
  } else if (msg.event === 'close_request') {
    showCloseConfirm(p);
  } else if (msg.event === 'scan_cancelled') {
    toast('扫描已取消', 4000);
    $('#progressText').textContent = '扫描已取消';
  } else if (msg.event === 'done') {
    finishTask();
    appendLog(p.msg || '');
  }
}

function appendLog(line) {
  const box = $('#logBox');
  if (!box || !line) return;
  box.textContent += line + '\n';
  box.scrollTop = box.scrollHeight;
}

/* ============================ 任务状态（取消按钮） ============================ */
function setTaskRunning(task, running) {
  const isScan = task === '扫描';
  const isClean = task === '清理';
  $('#btnScan').disabled = running;
  $('#btnClean').disabled = running;
  $('#pickAll').disabled = running;
  $('#btnRescanVol').disabled = running;
  const cs = $('#btnCancelScan');
  const cc = $('#btnCancelClean');
  cs.hidden = !(running && isScan);
  cc.hidden = !(running && isClean);
  if (running) { cs.disabled = false; cc.disabled = false; }
  // 任务期间锁住分组内的勾选（避免清理到一半改动选择集）
  const gl = $('#groupList');
  if (gl) gl.classList.toggle('task-lock', running);
}

function finishTask() {
  setTaskRunning('', false);
  $('#btnCancelScan').hidden = true;
  $('#btnCancelClean').hidden = true;
  const cw = $('#cleanProgressWrap');
  if (cw) cw.hidden = true;
}

async function cancelTask(ev) {
  const btn = ev && ev.currentTarget;
  if (btn) btn.disabled = true;
  try {
    const r = await api().cancel_current();
    if (!r.ok) { toast(r.error || '取消失败'); if (btn) btn.disabled = false; return; }
    toast(`已请求中止${r.task || '任务'}，正在收尾…`, 4000);
    $('#progressText').textContent = `正在中止${r.task || '任务'}…`;
  } catch (e) {
    toast('取消请求失败：' + e);
    if (btn) btn.disabled = false;
  }
}

/* ============================ 扫描 ============================ */
async function startScan() {
  const drives = $$('#drivePick input:checked').map(i => i.value);
  if (!drives.length) { toast('请至少选择一个盘'); return; }
  const deep = $('#deepScan').checked;
  if (deep) await api().set_config({ export_maxdepth: 0 });
  else await api().set_config({ export_maxdepth: 1 });

  S.selected.clear();
  $('#scanResultPanel').hidden = true;
  $('#btnScan').disabled = true;
  $('#progressWrap').hidden = false;
  $('#progressBar').style.width = '0%';
  $('#progressText').textContent = '正在启动…';
  const r = await api().start_scan(drives);
  if (!r.ok) {
    toast(r.error || '启动失败');
    $('#btnScan').disabled = false;
    finishTask();
  } else {
    setTaskRunning('扫描', true);
  }
}

function onScanResult(p) {
  S.result = p;
  const keys = Object.keys(p.drives || {});
  if (keys.length && !keys.includes(S.drive)) S.drive = keys[0];
  S.selected.clear();
  S.openGroups.clear();
  $('#progressBar').style.width = '100%';
  unlock('clean', 'software', 'diff');
  renderDriveTabs();
  renderSummary(p);
  renderScanTable();
  renderGroups();
  renderDiff(p.diff);
  $('#scanResultPanel').hidden = false;
  toast('扫描完成，已解锁清理模块');
}

function renderSummary(p) {
  const box = $('#scanSummary');
  box.innerHTML = '';
  Object.entries(p.drives).forEach(([d, data]) => {
    const t = data.totals;
    const items = data.items || [];
    const lowRisk = items.filter(i => i.tier === 1);
    const cand = lowRisk.reduce((a, b) => a + b.size, 0);
    const res = items.filter(i => i.is_residual || i.suspect_residual)
      .reduce((a, b) => a + b.size, 0);
    const cap = t.capacity || 1;
    box.insertAdjacentHTML('beforeend', `
      <div class="card">
        <div class="t">${esc(d)} 盘 · 已用 ${esc(t.used_h)} / ${esc(t.capacity_h)}</div>
        <div class="v">${esc(t.free_h)} <span class="s" style="font-size:12px">可用</span></div>
        <div class="bar"><i style="width:${Math.min(100, t.used / cap * 100).toFixed(1)}%"></i></div>
        <div class="s" style="margin-top:8px">
          顶层母文件夹 ${items.length} 个 ·
          可清理候选（低风险）${lowRisk.length} 个 / ${human(cand)} ·
          残留类 ${human(res)}
        </div>
      </div>`);
  });
  const reg = p.registry || {};
  box.insertAdjacentHTML('beforeend', `
    <div class="card">
      <div class="t">注册表关联</div>
      <div class="v">${reg.total || 0} <span class="s" style="font-size:12px">个已安装程序</span></div>
      <div class="s" style="margin-top:6px">
        ${Object.entries(reg.counts || {}).map(([k, v]) => `${esc(k)} ${v}`).join(' · ')}
      </div>
    </div>`);
}

function setDrive(d) {
  S.drive = d;
  renderDriveTabs();
  renderScanTable();
  renderGroups();
  if (S.result) renderSummary(S.result);
}

function renderDriveTabs() {
  if (!S.result) return;
  const html = Object.keys(S.result.drives || {}).map(d =>
    `<button class="dtab ${d === S.drive ? 'on' : ''}" data-d="${esc(d)}">${esc(d)} 盘</button>`
  ).join('');
  ['#driveTabs', '#driveTabsClean'].forEach(sel => {
    const el = $(sel);
    if (!el) return;
    el.innerHTML = html;
    el.querySelectorAll('.dtab').forEach(b =>
      b.addEventListener('click', () => setDrive(b.dataset.d)));
  });
}

function filteredRows() {
  if (!S.result) return [];
  const data = S.result.drives[S.drive];
  if (!data) return [];
  const q = $('#scanSearch').value.trim().toLowerCase();
  const sf = $('#scanSourceFilter').value;
  const tf = $('#scanTierFilter').value;
  let rows = (data.items || []).slice();
  if (q) rows = rows.filter(r => r.name.toLowerCase().includes(q) || r.path.toLowerCase().includes(q));
  if (sf) rows = rows.filter(r => r.source === sf);
  if (tf) rows = rows.filter(r => String(r.tier) === tf);
  const dir = S.sortDir === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    let x, y;
    if (S.sortKey === 'name') { x = a.name.toLowerCase(); y = b.name.toLowerCase(); return x < y ? -dir : x > y ? dir : 0; }
    if (S.sortKey === 'mtime') { x = a.mtime || 0; y = b.mtime || 0; }
    else if (S.sortKey === 'tier') { x = a.tier; y = b.tier; }
    else { x = a.size; y = b.size; }
    return (x - y) * dir;
  });
  return rows;
}

function renderScanTable() {
  const tb = $('#scanTable tbody');
  if (!S.result) { tb.innerHTML = ''; return; }
  const rows = filteredRows();
  const data = S.result.drives[S.drive];
  const used = (data && data.totals.used) || 1;

  $('#resultTitle').textContent = `${S.drive} 盘顶层母文件夹`;
  $('#resultCount').textContent = `共 ${(data.items || []).length} 个，筛选后 ${rows.length} 个`;

  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="8"><div class="empty">没有匹配的项目</div></td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r, i) => {
    const pct = Math.min(100, r.size / used * 100);
    const reg = r.registry_hit
      ? `<span class="reglink" title="匹配：${esc(r.registry_hit.match)}">✓ ${esc(r.registry_hit.app || '已登记')}</span>`
      : `<span class="regno">— 未关联</span>`;
    return `<tr data-i="${i}" data-path="${esc(r.path)}">
      <td class="namecell">
        <div class="nm">${esc(r.name)}</div>
        <div class="pt">${esc(r.path)}</div>
      </td>
      <td class="num">${esc(r.size_h)}</td>
      <td><div class="minibar"><i style="width:${pct.toFixed(1)}%"></i></div>
          <div class="pt">${pct.toFixed(1)}%</div></td>
      <td>${esc(r.mtime_str)}</td>
      <td><span class="tag" style="color:${srcColor(r.source)};border-color:${srcColor(r.source)}33;background:${srcColor(r.source)}12">${esc(r.source_label)}</span></td>
      <td>${reg}</td>
      <td><span class="risk" style="color:${r.tier_color}">${r.tier_icon} ${esc(r.tier_label)}</span></td>
      <td class="reason">${esc(r.reason_text)}</td>
    </tr>`;
  }).join('');

  tb.querySelectorAll('tr').forEach(tr => {
    tr.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const item = rows[Number(tr.dataset.i)];
      openCtx(e.clientX, e.clientY, item, false);
    });
  });
}

function srcColor(s) {
  return { system: '#2563eb', software: '#7c3aed', user: '#0891b2', residual: '#ea580c', unknown: '#64748b' }[s] || '#64748b';
}

async function exportCsv() {
  const r = await api().export_report('csv');
  if (r.ok) toast('已导出：' + r.path, 5000);
  else toast(r.error || '导出失败');
}

/* ============================ 清理模块 ============================ */
function sortGroupItems(items) {
  const dir = S.groupSort.dir === 'asc' ? 1 : -1;
  const k = S.groupSort.key;
  return items.slice().sort((a, b) => {
    if (k === 'name') return a.name.toLowerCase().localeCompare(b.name.toLowerCase()) * dir;
    if (k === 'tier') return ((a.tier || 0) - (b.tier || 0)) * dir;
    return ((a.size || 0) - (b.size || 0)) * dir;
  });
}

function renderGroups() {
  if (!S.result) return;
  const data = S.result.drives[S.drive];
  if (!data) return;
  const gs = data.groups || {};
  const order = ['cache', 'system_update', 'suspect', 'low', 'medium', 'high', 'forbidden'];
  const box = $('#groupList');
  box.innerHTML = '';
  S.groups = gs;

  order.forEach(key => {
    const g = gs[key];
    if (!g || !g.items.length) return;
    const items = sortGroupItems(g.items);
    const dotColor = {
      cache: '#0ea5e9',
      system_update: '#ea580c', suspect: '#d97706', low: '#16a34a',
      medium: '#d97706', high: '#ea580c', forbidden: '#dc2626',
    }[key];
    // 本组可勾选的项（禁删 / 高风险不可选）
    const cands = items.filter(it => it.deletable && it.tier < 3);
    const el = document.createElement('div');
    el.className = 'group' + (S.openGroups.has(key) ? ' open' : '');
    el.innerHTML = `
      <div class="grouphead">
        <div class="gl">
          <span class="gtri">▶</span>
          <span class="dot" style="background:${dotColor}"></span>
          <div>
            <div class="ttl">${esc(g.title)}</div>
            <div class="dsc">${esc(g.desc)}</div>
          </div>
        </div>
        <div class="row">
          <label class="gsel" title="${cands.length ? '勾选/取消本组全部可清理项' : '本组没有可勾选的项目'}">
            <input type="checkbox" data-gsel="${key}" ${cands.length ? '' : 'disabled'}>
            <span>全选本组</span>
          </label>
          <span class="sum">${g.count} 项 · ${esc(g.size_h)}</span>
        </div>
      </div>
      <div class="groupbody">
        <table class="grid">
          <thead><tr>
            <th style="width:34px"></th>
            <th class="sortable" data-gsort="name">目录</th>
            <th class="sortable num" data-gsort="size">大小</th>
            <th>来源</th>
            <th class="sortable" data-gsort="tier">风险</th>
            <th>依据</th>
          </tr></thead>
          <tbody>${items.map(it => groupRow(it)).join('')}</tbody>
        </table>
      </div>`;

    el.querySelector('.grouphead').addEventListener('click', () => {
      const open = el.classList.toggle('open');
      if (open) S.openGroups.add(key); else S.openGroups.delete(key);
    });

    // 本组全选（不要触发分组的展开/收起）
    const gsel = el.querySelector('[data-gsel]');
    if (gsel) {
      const wrap = gsel.closest('.gsel');
      wrap.addEventListener('click', (e) => e.stopPropagation());
      gsel.checked = cands.length > 0 && cands.every(it => S.selected.has(it.path));
      gsel.indeterminate = !gsel.checked && cands.some(it => S.selected.has(it.path));
      gsel.addEventListener('change', () => {
        cands.forEach(it => {
          if (gsel.checked) S.selected.add(it.path); else S.selected.delete(it.path);
        });
        renderGroups();
      });
    }
    box.appendChild(el);
  });

  // 组内表头排序（点任一组表头 = 全局切换该维度，所有组同步）
  box.querySelectorAll('th[data-gsort]').forEach(th => {
    if (th.dataset.gsort === S.groupSort.key) th.classList.add(S.groupSort.dir);
    th.addEventListener('click', () => {
      const k = th.dataset.gsort;
      if (S.groupSort.key === k) S.groupSort.dir = S.groupSort.dir === 'asc' ? 'desc' : 'asc';
      else { S.groupSort.key = k; S.groupSort.dir = (k === 'name') ? 'asc' : 'desc'; }
      renderGroups();
    });
  });

  // 勾选与右键
  box.querySelectorAll('tbody tr').forEach(tr => {
    const path = tr.dataset.path;
    const cb = tr.querySelector('input[type=checkbox]');
    if (cb && !cb.disabled) {
      cb.addEventListener('change', () => {
        if (cb.checked) S.selected.add(path); else S.selected.delete(path);
        tr.classList.toggle('selected', cb.checked);
        updatePickInfo();
        updatePickAll();
      });
    }
    tr.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const item = findItem(path);
      openCtx(e.clientX, e.clientY, item, true);
    });
  });
  updatePickInfo();
  updatePickAll();
}

function groupRow(it) {
  const dis = it.deletable ? '' : 'disabled';
  const checked = S.selected.has(it.path) ? 'checked' : '';
  const cls = [
    checked ? 'selected' : '',
    it.cleaned ? 'cleaned' : '',
    it.failed ? 'cleanfail' : '',
  ].filter(Boolean).join(' ');
  const failLine = it.failed
    ? `<div class="failerr">⚠️ 清理失败：${esc(it.fail_error)}（可右键「查看谁占用 / 解除占用」后重试）</div>`
    : '';
  return `<tr data-path="${esc(it.path)}" class="${cls}">
    <td><input type="checkbox" ${checked} ${dis} data-path="${esc(it.path)}"></td>
    <td class="namecell"><div class="nm">${esc(it.name)}</div><div class="pt">${esc(it.path)}</div>${failLine}</td>
    <td class="num">${esc(it.size_h)}</td>
    <td><span class="tag" style="color:${srcColor(it.source)};border-color:${srcColor(it.source)}33;background:${srcColor(it.source)}12">${esc(it.source_label)}</span></td>
    <td><span class="risk" style="color:${it.tier_color}">${it.tier_icon} ${esc(it.tier_label)}</span></td>
    <td class="reason">${esc(it.reason_text)}</td>
  </tr>`;
}

function findItem(path) {
  const items = (S.result.drives[S.drive] || {}).items || [];
  return items.find(x => x.path === path) || null;
}

// 分组里的条目与 items 数组是各自独立的副本（JSON 序列化后引用不共享），
// 所以标记清理状态时必须两处都改，否则划线/失败标注不会显示到分组列表。
function markCleanState(path, patch) {
  const apply = (it) => { if (it && it.path === path) Object.assign(it, patch); };
  apply(findItem(path));
  const gs = (S.result.drives[S.drive] || {}).groups || {};
  Object.values(gs).forEach(g => (g.items || []).forEach(apply));
}

function updatePickInfo() {
  let size = 0;
  S.selected.forEach(p => { const it = findItem(p); if (it) size += it.size; });
  $('#pickInfo').textContent = `已选 ${S.selected.size} 项 / ${human(size)}`;
}

// 「全选可清理项」联动：当安全组（缓存/更新残留/疑似残留/低风险）的可删项被全部勾选时，
// 顶部全选自动勾上；只要有一个没勾就取消。手动勾中/高风险项不影响这里的判定。
function updatePickAll() {
  const el = $('#pickAll');
  if (!el || !S.result) return;
  const gs = ((S.result.drives[S.drive] || {}).groups) || {};
  const safeKeys = ['cache', 'system_update', 'suspect', 'low'];
  const allSafe = new Set();
  safeKeys.forEach(k => {
    ((gs[k] || {}).items || []).forEach(it => { if (it.deletable) allSafe.add(it.path); });
  });
  el.checked = allSafe.size > 0 && Array.from(allSafe).every(p => S.selected.has(p));
}

async function doClean() {
  const paths = Array.from(S.selected);
  if (!paths.length) { toast('请先勾选要清理的项目'); return; }
  const items = paths.map(p => findItem(p)).filter(Boolean);
  const total = items.reduce((a, b) => a + b.size, 0);

  showModal('⚠️ 确认清理', `
    <div class="warnbox">⚠️ 此操作非常危险，可能导致不可逆的数据丢失！</div>
    <p>以下 <b>${items.length}</b> 个目录将被<b>移入系统回收站</b>（可在回收站还原），合计释放约
       <b>${human(total)}</b>。</p>
    <ul>${items.slice(0, 30).map(i => `<li>${esc(i.path)} <span class="muted">(${esc(i.size_h)})</span></li>`).join('')}
        ${items.length > 30 ? `<li>… 其余 ${items.length - 30} 项</li>` : ''}</ul>
    <p class="muted small">⛔ 禁删项与白名单项无法勾选，系统关键目录已被硬锁定。</p>
  `, async () => {
    setTaskRunning('清理', true);
    $('#cleanProgressWrap').hidden = false;
    $('#cleanProgressBar').style.width = '0%';
    $('#cleanProgressText').textContent = '正在清理…';
    let r;
    try {
      r = await api().move_to_recycle_bin(items.map(i => ({ path: i.path, tier: i.tier })));
    } finally {
      finishTask();
    }
    if (r.cancelled) {
      toast(`已取消：完成 ${r.ok_count} 项，剩余 ${r.remaining} 项未处理`, 7000);
    } else if (r.failed_count) {
      toast(`清理完成 ${r.ok_count} 项，${r.failed_count} 项失败已跳过（列表中已标注原因，可解除占用后重试）`, 8000);
    } else {
      toast(`已把 ${r.ok_count} 项移入回收站`);
    }
    S.selected.clear();
  });
}

function onCleanupResult(p) {
  // 已清理的项：划线标记（不再可勾选）
  (p.ok || []).forEach(path => {
    markCleanState(path, { cleaned: true, failed: false, size: 0, size_h: '已清理', deletable: false });
    S.selected.delete(path);
  });
  // 清理失败的项：保留在列表里并标注原因（不再自动弹诊断弹窗阻断流程）
  (p.failed || []).forEach(f => {
    markCleanState(f.path, { failed: true, fail_error: f.error || '删除失败' });
    S.selected.delete(f.path);
  });
  if (p.ok_count || p.failed_count) renderGroups();
}

/* ============================ 右键菜单 ============================ */
function openCtx(x, y, item, inClean) {
  if (!item) return;
  S.ctxTarget = item;
  const m = $('#ctxMenu');
  const canWl = item.tier < 4;
  m.innerHTML = `
    <div class="hdr">${esc(item.name)}</div>
    <div class="mi" data-a="reveal">🎯 定位到该目录</div>
    <div class="mi" data-a="copy">📋 复制完整路径</div>
    <div class="sep"></div>
    <div class="mi" data-a="locks">🔍 查看谁占用 / 解除占用</div>
    <div class="mi ${canWl ? '' : 'dis'}" data-a="wl">🛡 加入白名单（永不清理）</div>
    ${inClean && item.deletable ? '<div class="sep"></div><div class="mi" data-a="clean">🗑 移入回收站</div>' : ''}
  `;
  m.hidden = false;
  const w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.min(x, innerWidth - w - 8) + 'px';
  m.style.top = Math.min(y, innerHeight - h - 8) + 'px';
  m.querySelectorAll('.mi').forEach(mi => {
    if (mi.classList.contains('dis')) return;
    mi.addEventListener('click', () => handleCtx(mi.dataset.a, item));
  });
}
function hideCtx() { const m = $('#ctxMenu'); if (m) m.hidden = true; }

async function handleCtx(action, item) {
  hideCtx();
  if (action === 'reveal') {
    const r = await api().reveal_path(item.path);
    if (!r.ok) toast(r.error);
  } else if (action === 'copy') {
    try { await navigator.clipboard.writeText(item.path); toast('已复制路径'); }
    catch (e) { toast('复制失败：' + item.path); }
  } else if (action === 'wl') {
    const r = await api().add_whitelist(item.path);
    renderWhitelist(r.items);
    toast('已加入白名单');
  } else if (action === 'clean') {
    S.selected.clear(); S.selected.add(item.path); updatePickInfo(); doClean();
  } else if (action === 'locks') {
    S.lockTarget = item.path;
    $('#lockPath').textContent = item.path;
    $('#lockBody').innerHTML = '<div class="lockhint">正在分析占用情况…</div>';
    $('#lockModal').hidden = false;
    await api().check_locks(item.path);
  }
}

/* ============================ 已装软件 ============================ */
async function loadSoftware() {
  $('#swInfo').textContent = '读取中…';
  await api().get_software();
}
function onSoftwareResult(p) {
  if (!p.ok) { $('#swInfo').textContent = '失败：' + (p.error || ''); return; }
  S.software = p.apps || [];
  S.orphans = p.orphans || [];
  const total = S.software.reduce((a, b) => a + (b.size || 0), 0);
  $('#swInfo').textContent = `共 ${S.software.length} 个程序，已统计占用合计 ${human(total)}`;
  const op = $('#orphanPanel');
  if (S.orphans.length) {
    op.hidden = false;
    $('#orphanTable tbody').innerHTML = S.orphans.map(o => `<tr>
      <td>${esc(o.app)}</td><td>${esc(o.publisher || '')}</td>
      <td class="namecell"><div class="pt">${esc(o.path)}</div></td>
      <td class="pt">${esc(o.uninstall_string || '')}</td></tr>`).join('');
  } else op.hidden = true;
  renderSoftware();
}
function renderSoftware() {
  const q = ($('#swSearch').value || '').trim().toLowerCase();
  let rows = (S.software || []).slice();
  if (S.swFilter === 'app') rows = rows.filter(r => !r.hidden);
  else if (S.swFilter === 'system') rows = rows.filter(r => r.hidden);
  if (q) rows = rows.filter(r => (r.name + r.publisher).toLowerCase().includes(q));

  const dir = S.swSort.dir === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    if (S.swSort.key === 'name') return a.name.toLowerCase().localeCompare(b.name.toLowerCase()) * dir;
    if (S.swSort.key === 'date') return String(a.install_date || '').localeCompare(String(b.install_date || '')) * dir;
    return ((a.size || 0) - (b.size || 0)) * dir;
  });

  $('#swTable tbody').innerHTML = rows.slice(0, 600).map(r => `<tr>
    <td class="namecell"><div class="nm">${esc(r.name)}${r.hidden ? ' <span class="pill">系统组件</span>' : ''}</div></td>
    <td>${esc(r.version)}</td>
    <td>${esc(r.publisher)}</td>
    <td class="num">${esc(r.size_h)}</td>
    <td>${esc(r.install_date_h || '-')}</td>
    <td class="namecell"><div class="pt">${esc(r.install_location || '—')}</div></td>
    <td><span class="pill">${esc(r.hive)}</span></td>
    <td>
      ${r.exists ? `<button class="btn sm" data-open="${esc(r.install_location)}">打开</button>` : ''}
      ${r.uninstall_string ? `<button class="btn sm" data-un="${esc(r.uninstall_string)}">卸载</button>` : ''}
    </td>
  </tr>`).join('') || '<tr><td colspan="8"><div class="empty">无数据</div></td></tr>';

  $('#swTable tbody').querySelectorAll('[data-open]').forEach(b =>
    b.addEventListener('click', () => api().open_path(b.dataset.open)));
  $('#swTable tbody').querySelectorAll('[data-un]').forEach(b =>
    b.addEventListener('click', () => {
      showModal('确认卸载', `<p>将启动该软件的官方卸载程序：</p>
        <p class="mono small">${esc(b.dataset.un)}</p>
        <p class="muted small">建议优先使用软件自带卸载流程，而不是直接删除目录。</p>`,
        () => api().run_uninstaller(b.dataset.un));
    }));
}

/* ============================ 快照对比 ============================ */
async function loadDiff() {
  const d = await api().get_diff();
  renderDiff(d);
}
function renderDiff(d) {
  const box = $('#diffBody');
  if (!d || !d.available) {
    box.innerHTML = `<div class="panel"><div class="empty">${esc((d && d.reason) || '暂无对比数据')}</div></div>`;
    return;
  }
  let html = `<div class="panel"><div class="row spread">
      <span>对比区间：<b>${esc(d.before_time)}</b> → <b>${esc(d.after_time)}</b>（约 ${d.hours} 小时）</span>
    </div></div>`;
  Object.entries(d.drives || {}).forEach(([drive, dd]) => {
    const pill = dd.delta_total >= 0 ? 'up' : 'down';
    html += `<div class="panel">
      <div class="row spread" style="margin-bottom:10px">
        <strong>${esc(drive)} 盘</strong>
        <span>总占用变化 <span class="pill ${pill}">${esc(dd.delta_total_h)}</span>
          　可用空间变化 <span class="pill ${dd.dry >= 0 ? 'down' : 'up'}">${dd.dry >= 0 ? '+' : ''}${human(dd.dry)}</span></span>
      </div>
      <div class="diffgrid">
        <div>
          <h3 class="sub">📈 增长最快</h3>
          <table class="grid"><thead><tr><th>目录</th><th class="num">变化</th><th class="num">现在</th></tr></thead>
          <tbody>${(dd.grew || []).map(r => `<tr>
            <td class="namecell"><div class="nm">${esc(r.name)}</div><div class="pt">${esc(r.path)}</div></td>
            <td class="num" style="color:#dc2626">${esc(r.delta_h)}${r.pct != null ? ` (${r.pct}%)` : ''}</td>
            <td class="num">${esc(r.new_h)}</td></tr>`).join('') || '<tr><td colspan="3"><div class="empty">无增长</div></td></tr>'}
          </tbody></table>
        </div>
        <div>
          <h3 class="sub">📉 缩小 / 新增 / 消失</h3>
          <table class="grid"><thead><tr><th>目录</th><th class="num">变化</th><th>状态</th></tr></thead>
          <tbody>${[...(dd.shrank || []).map(r => ({ ...r, st: '缩小' })),
                     ...(dd.gone || []).map(r => ({ ...r, st: '已消失' })),
                     ...(dd.new || []).map(r => ({ ...r, st: '新增' }))]
            .map(r => `<tr>
              <td class="namecell"><div class="pt">${esc(r.path)}</div></td>
              <td class="num" style="color:${r.delta < 0 ? '#16a34a' : r.delta > 0 ? '#dc2626' : '#6b7280'}">${esc(r.delta_h)}</td>
              <td><span class="pill">${esc(r.st || r.status)}</span></td></tr>`).join('')
            || '<tr><td colspan="3"><div class="empty">无变化</div></td></tr>'}
          </tbody></table>
        </div>
      </div>
    </div>`;
  });
  box.innerHTML = html;
}

/* ============================ 设置 ============================ */
async function loadSettings() {
  const wl = await api().get_whitelist();
  renderWhitelist(wl.items || []);
  const lg = await api().get_log();
  $('#logBox').textContent = (lg.log || []).join('\n');

  const on = !!(S.meta && S.meta.sound_enabled);
  $('#soundToggle').checked = on;
  const avail = S.meta && S.meta.sound_available;
  $('#soundState').textContent = on
    ? (avail ? '已开启' : '已开启（当前系统可能不支持播放）')
    : '已关闭';

  const tray = (S.meta && S.meta.tray) || {};
  const sel = $('#closeActionSel');
  sel.value = tray.close_action || 'ask';
  $('#trayState').textContent = tray.available
    ? ''
    : ('托盘不可用：' + (tray.error || '缺少托盘组件') + '（选「最小化到托盘」会失败）');

  $$('[data-tone]').forEach(b => {
    b.onclick = async () => {
      const r = await api().test_sound(b.dataset.tone);
      if (!r.ok) toast('播放失败' + (r.available ? '' : '（当前系统不支持音频播放）'));
    };
  });
}

/* ============================ 关闭询问框 ============================ */
function showCloseConfirm(p) {
  const busy = !!p.busy;
  $('#closeBody').innerHTML = `
    ${busy ? `<div class="warnbox">⚠️ 程序正在${esc(p.task || '执行任务')}，现在退出会立即中断它。</div>` : ''}
    <p class="closeq">请问以后操作是否都<b>最小化到托盘</b>？</p>
    <p class="muted small"><b>最小化到托盘</b>：窗口收进系统托盘，程序继续运行，双击托盘图标即可恢复。</p>
    <p class="muted small"><b>直接退出</b>：关闭程序。</p>
    ${busy && p.detail ? `<p class="muted small" style="margin-top:8px">${esc(p.detail)}</p>` : ''}`;
  $('#closeRemember').checked = false;
  $('#closeTray').disabled = false;
  $('#closeQuit').disabled = false;
  $('#closeCancel').disabled = false;
  $('#closeModal').hidden = false;
}

async function closeChoose(action) {
  const remember = $('#closeRemember').checked;
  $('#closeTray').disabled = true;
  $('#closeQuit').disabled = true;
  $('#closeCancel').disabled = true;
  let r;
  try {
    r = await api().close_with(action, remember);
  } catch (e) {
    r = { ok: false, error: String(e) };
  }
  if (!r || !r.ok) {
    toast((r && r.error) || '操作失败', 5000);
    $('#closeTray').disabled = false;
    $('#closeQuit').disabled = false;
    $('#closeCancel').disabled = false;
    return;
  }
  $('#closeModal').hidden = true;
  if (action === 'exit') return;         // 进程即将结束，不用再提示
  toast(r.message || '已最小化到系统托盘', 5000);
}

/* ============================ 关于作者 ============================ */
async function loadAbout() {
  let a;
  try {
    a = await api().get_about();
  } catch (e) {
    $('#aboutBody').innerHTML = `<div class="panel"><div class="empty">读取失败：${esc(String(e))}</div></div>`;
    return;
  }
  const au = a.author || {};
  const initial = String(au.name || au.login || '?').slice(0, 1).toUpperCase();
  const repoLine = a.repo_url
    ? `<a href="${esc(a.repo_url)}" target="_blank" rel="noreferrer">${esc(a.repo_url)}</a>`
    : '<span class="muted">尚未上传（上传后这里会显示仓库地址）</span>';

  $('#aboutBody').innerHTML = `
    <div class="panel">
      <div class="authorwrap">
        <div id="avatarSlot"></div>
        <div class="authorinfo">
          <div class="authorname">${esc(au.name || '')}</div>
          <div class="authorlogin">@${esc(au.login || '')}</div>
          <div class="authorbio">${esc(au.bio || '')}</div>
          <div class="kv">
            <div class="k">GitHub</div>
            <div class="v"><a href="${esc(au.url || '')}" target="_blank" rel="noreferrer">${esc(au.url || '')}</a></div>
            <div class="k">加入时间</div><div class="v">${esc(au.joined || '-')}</div>
            <div class="k">公开仓库</div><div class="v">${au.repos == null ? '-' : au.repos}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3 class="sub">关于本程序</h3>
      <div class="kv">
        <div class="k">名称</div><div class="v">${esc(a.app || '')}</div>
        <div class="k">版本</div><div class="v"><span class="versionbig">v${esc(a.version || '')}</span></div>
        <div class="k">作者</div><div class="v">${esc(au.name || '')}（@${esc(au.login || '')}）</div>
        <div class="k">扫描引擎</div>
        <div class="v">WizTree${a.wiztree ? '（当前：' + esc(a.wiztree) + '）' : '（尚未检测到）'}</div>
        <div class="k">运行方式</div>
        <div class="v">${a.frozen ? '便携版单文件 exe' : '源码运行（开发模式）'}</div>
        <div class="k">运行环境</div><div class="v">Python ${esc(a.python || '')}${a.is_admin ? ' · 管理员权限' : ' · 普通权限'}</div>
        <div class="k">数据目录</div><div class="v mono">${esc(a.data_dir || '')}</div>
        <div class="k">项目仓库</div><div class="v">${repoLine}</div>
      </div>
    </div>

    <div class="panel">
      <h3 class="sub">安全承诺</h3>
      <p class="muted small" style="line-height:1.9;margin:0">
        · 扫描阶段<b>完全只读</b>，不修改、不移动、不删除任何文件<br>
        · 清理只走<b>系统回收站</b>，随时可还原，不提供永久删除<br>
        · 盘符根目录、系统关键目录、白名单目录<b>硬锁定</b>，无法勾选<br>
        · 系统关键进程（lsass / csrss / services 等）<b>禁止结束</b><br>
        · 所有数据只写在程序目录下，<b>不写注册表、不写 AppData</b>，删掉文件夹即彻底卸载
      </p>
    </div>`;

  // 头像：网络不通或加载失败时退回首字母圆形
  const slot = $('#avatarSlot');
  const url = au.avatar;
  if (url) {
    const img = document.createElement('img');
    img.className = 'avatar';
    img.alt = '头像';
    img.referrerPolicy = 'no-referrer';
    img.onerror = () => {
      slot.innerHTML = `<div class="avatar-fallback">${esc(initial)}</div>`;
    };
    img.src = url;
    slot.appendChild(img);
  } else {
    slot.innerHTML = `<div class="avatar-fallback">${esc(initial)}</div>`;
  }
}
function renderWhitelist(items) {
  const box = $('#wlList');
  if (!box) return;
  box.innerHTML = (items || []).map(p => `
    <div class="wlitem">
      <span class="mono small">${esc(p)}</span>
      <button class="btn sm" data-rm="${esc(p)}">移除</button>
    </div>`).join('') || '<div class="muted small">暂无白名单项</div>';
  box.querySelectorAll('[data-rm]').forEach(b => b.addEventListener('click', async () => {
    const r = await api().remove_whitelist(b.dataset.rm);
    renderWhitelist(r.items);
  }));
}

/* ============================ 占用诊断 / 分级解除 ============================ */
function showLockDialog(path, diag) {
  S.lockTarget = path || '';
  $('#lockPath').textContent = path || '';
  const box = $('#lockBody');
  diag = diag || { ok: false, lockers: [], error: '未知结果' };
  const lockers = diag.lockers || [];

  let html = '';
  if (!diag.ok && diag.error) {
    html += `<div class="lockhint bad">诊断失败：${esc(diag.error)}</div>`;
  } else if (!lockers.length) {
    html += `<div class="lockhint ok">✓ 未检测到占用该路径的进程</div>
      <div class="lockhint warn"><b>仍然删不掉？常见其他原因：</b><br>
      ① 需要管理员权限（系统目录 / 服务文件）<br>
      ② 被 <b>杀毒软件</b> 或 <b>Windows 搜索索引</b> 正在扫描<br>
      ③ 路径过长（超过 260 字符）<br>
      ④ 文件带只读 / 系统属性<br>
      ⑤ 磁盘只读或被写保护</div>`;
  } else {
    html += `<div class="lockhint">检测到 <b>${lockers.length}</b> 个进程正在使用该路径：</div>
      <table class="lockers"><thead><tr>
        <th>进程</th><th style="width:70px">PID</th><th style="width:110px">类型</th>
        <th style="width:200px">操作</th>
      </tr></thead><tbody>`;
    lockers.forEach((l, i) => {
      html += `<tr>
        <td><div class="pname">${esc(l.exe_name || l.app)}</div>
            <div class="pex">${esc(l.exe || l.service || '')}</div></td>
        <td>${l.pid}</td>
        <td>${esc(l.type)}${l.protected ? ' <span class="pill up">系统关键</span>' : ''}</td>
        <td>${l.can_kill
          ? `<button class="btn sm" data-restart="${i}">重启进程</button>
             <button class="btn sm danger" data-kill="${i}">结束进程</button>`
          : `<span class="muted small">${l.protected ? '已保护，禁止结束' : '不可操作'}</span>`}</td>
      </tr>`;
    });
    html += `</tbody></table>`;
  }

  html += `<div class="actions">
    <div class="actioncard safe" data-act="delay">
      <div class="at">① 延迟到重启后删除</div>
      <div class="ad">最安全，零数据丢失。重启前文件仍在，重启时由系统删除。系统级登记通常需要管理员权限。</div>
    </div>
    <div class="actioncard mid" data-act="restart_explorer">
      <div class="at">② 重启资源管理器</div>
      <div class="ad">explorer.exe 是最常见的占用者。任务栏会闪一下，属正常。</div>
    </div>
    <div class="actioncard safe" data-act="recheck">
      <div class="at">③ 重新检测</div>
      <div class="ad">再次查询占用情况，确认是否已解除。</div>
    </div>
  </div>`;

  box.innerHTML = html;

  box.querySelectorAll('[data-kill]').forEach(b => b.addEventListener('click', () => {
    confirmKill(S.lockTarget, lockers[Number(b.dataset.kill)], 'kill');
  }));
  box.querySelectorAll('[data-restart]').forEach(b => b.addEventListener('click', () => {
    confirmKill(S.lockTarget, lockers[Number(b.dataset.restart)], 'restart');
  }));
  box.querySelectorAll('[data-act]').forEach(c => c.addEventListener('click', async () => {
    const act = c.dataset.act;
    if (act === 'recheck') {
      $('#lockBody').innerHTML = '<div class="lockhint">正在重新检测…</div>';
      await api().check_locks(S.lockTarget);
      return;
    }
    const r = await api().unlock(S.lockTarget, act);
    toast(r.message || r.error || '已执行', 5000);
  }));

  $('#lockModal').hidden = false;
}

function confirmKill(path, l, mode) {
  const isRestart = mode === 'restart';
  showModal(isRestart ? '⚠️ 确认重启该进程' : '⚠️ 确认结束该进程', `
    <div class="warnbox">⚠️ 此操作非常危险，可能导致不可逆的数据丢失！</div>
    <p>${isRestart ? '将<b>结束并尝试重新启动</b>' : '将<b>强制结束</b>'}以下进程：</p>
    <p><b>${esc(l.exe_name || l.app)}</b>（PID ${l.pid}）<br>
       <span class="mono small">${esc(l.exe || '')}</span></p>
    <p class="muted small">风险：该进程未保存的内容会丢失。
      ${isRestart ? '重新启动不会带上原命令行参数，状态可能不完整。' : ''}
      系统关键进程已被硬性禁止结束。</p>
  `, async () => {
    const r = await api().unlock(path, isRestart ? 'restart' : 'kill',
                                 l.pid, l.exe_name || l.app);
    toast(r.message || r.error || '已执行', 6000);
  });
}

function onUnlockResult(p) {
  toast(p.message || p.error || '已执行', 5000);
  const target = p.path || S.lockTarget;
  if (target && $('#lockModal').hidden === false) {
    setTimeout(() => showLockDialog(target, p.diagnosis || {}), 700);
  }
}

/* ============================ 通用弹窗 ============================ */
let modalCb = null;
function showModal(title, bodyHtml, cb) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = bodyHtml;
  modalCb = cb;
  $('#modal').hidden = false;
}
$('#modalOk').addEventListener('click', async () => {
  $('#modal').hidden = true;
  const cb = modalCb; modalCb = null;
  if (cb) await cb();
});

/* ============================ 启动 ============================ */
if (window.pywebview && window.pywebview.api) init();
else if (window.__MOCK_MODE__ && window.pywebview && window.pywebview.api) init();
else window.addEventListener('pywebviewready', init);
