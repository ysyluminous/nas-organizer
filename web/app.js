// Token 存在 sessionStorage：关闭浏览器标签即失效，降低公共设备上的泄露风险
const TOKEN_KEY = 'nas_organizer_token';
const THEME_KEY = 'nas_organizer_theme';
const getToken = () => sessionStorage.getItem(TOKEN_KEY) || '';
const setToken = (t) => t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY);

const $ = (s) => document.querySelector(s);
/** 以编程方式切换 Tab（首启引导等场景使用），复用导航点击逻辑 */
function goTab(name) {
  const btn = document.querySelector(`nav button[data-tab="${name}"]`);
  if (btn) btn.click();
}

// 主题必须在首屏前应用，避免闪烁
(function applyInitialTheme() {
  let t = localStorage.getItem(THEME_KEY);
  if (!t) t = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = t;
})();
function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem(THEME_KEY, next);
}

// ---------- 通用 UI 组件（统一 loading / empty / error） ----------

function setLoading(el, msg = '加载中…') {
  el.innerHTML = `<div class="state"><span class="spinner"></span>${escapeHtml(msg)}</div>`;
}
function setEmpty(el, msg = '暂无数据') {
  el.innerHTML = `<div class="state empty">${escapeHtml(msg)}</div>`;
}
function setError(el, msg) {
  el.innerHTML = `<div class="state error-state">${escapeHtml(msg || '加载失败')}</div>`;
}
/** 后端路径来自本地文件系统，展示时做 HTML 转义，避免路径中的特殊字符注入页面 */
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
/** 把值安全嵌入 onclick 的单引号参数中：先 JSON 序列化，再 HTML 转义 */
const jsArg = (v) => escapeHtml(JSON.stringify(v));

let toastTimer = null;
function toast(msg, type = 'info') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast-' + type;
  t.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.style.display = 'none'; }, type === 'error' ? 5000 : 3000);
}

const fmtBytes = (b) => {
  if (!b) return '0 B';
  const u = ['B','KB','MB','GB','TB'];
  const i = Math.min(Math.floor(Math.log(b) / Math.log(1024)), u.length - 1);
  return (b / Math.pow(1024, i)).toFixed(1) + ' ' + u[i];
};
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString() : '-';

/** 统一 API 封装：自动附带 Bearer Token；401 时跳转登录。 */
const api = async (path, options = {}) => {
  const headers = { ...(options.headers || {}) };
  if (getToken()) headers['Authorization'] = 'Bearer ' + getToken();
  if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    options = { ...options, body: JSON.stringify(options.body) };
  }
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    setToken('');
    showLogin('登录已失效，请重新登录');
    throw new Error('未授权');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
};

function showLogin(msg) {
  if (msg) $('#login-error').textContent = msg;
  $('#login-overlay').style.display = 'flex';
  $('.app').style.visibility = 'hidden';
  setTimeout(() => $('#login-password').focus(), 50);
}
function hideLogin() {
  $('#login-overlay').style.display = 'none';
  $('.app').style.visibility = 'visible';
  $('#login-error').textContent = '';
}

// ---------- 登录 / 登出 / 改密 ----------

async function doLogin() {
  const pwd = $('#login-password').value;
  if (!pwd) { $('#login-error').textContent = '请输入密码'; return; }
  $('#login-btn').disabled = true;
  $('#login-error').textContent = '';
  try {
    const r = await api('/api/auth/login', { method: 'POST', body: { password: pwd } });
    setToken(r.token);
    $('#login-password').value = '';
    hideLogin();
    loadedTabs.clear();
    await boot();
  } catch (e) {
    $('#login-error').textContent = e.message || '登录失败';
  } finally {
    $('#login-btn').disabled = false;
  }
}

async function doLogout() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) {}
  setToken('');
  loadedTabs.clear();
  showLogin();
}

function openPwdModal() { $('#pwd-modal').style.display = 'flex'; $('#pwd-error').textContent = ''; }
function closePwdModal() { $('#pwd-modal').style.display = 'none'; }

async function doChangePassword() {
  const oldPwd = $('#old-pwd').value;
  const newPwd = $('#new-pwd').value;
  const newPwd2 = $('#new-pwd2').value;
  if (newPwd !== newPwd2) { $('#pwd-error').textContent = '两次输入的新密码不一致'; return; }
  try {
    const r = await api('/api/auth/password', {
      method: 'POST', body: { old_password: oldPwd, new_password: newPwd }
    });
    setToken(r.token);
    $('#old-pwd').value = ''; $('#new-pwd').value = ''; $('#new-pwd2').value = '';
    closePwdModal();
    toast('密码已修改，所有旧会话已失效', 'success');
  } catch (e) {
    $('#pwd-error').textContent = e.message || '修改失败';
  }
}

// ---------- Tab 切换（懒加载，首次进入才请求） ----------

const loadedTabs = new Set();
const TAB_LOADERS = {
  overview: loadStats, files: loadFiles, search: loadSearchStats, duplicates: loadDuplicates,
  views: loadViews, plans: loadPlans, cleanup: loadCleanupKinds,
  recycle: loadRecycle, review: loadReview, media: loadMedia, tasks: loadTasks,
};

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('nav button').forEach(x => { x.classList.remove('active'); x.setAttribute('aria-selected', 'false'); });
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    b.setAttribute('aria-selected', 'true');
    const name = b.dataset.tab;
    $('#tab-' + name).classList.add('active');
    if (!loadedTabs.has(name)) {
      loadedTabs.add(name);
      const loader = TAB_LOADERS[name];
      if (loader) Promise.resolve(loader()).catch(e => toast(e.message, 'error'));
    }
  };
});

function bars(el, data, labelKey, valueKey) {
  if (!Array.isArray(data) || !data.length) { setEmpty(el, '暂无数据'); return; }
  const max = Math.max(...data.map(d => Number(d[valueKey]) || 0), 1);
  el.innerHTML = data.map(d => `
    <div class="bar-row">
      <span class="bar-label">${escapeHtml(d[labelKey] || '未分类')}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${((Number(d[valueKey]) || 0) / max * 100).toFixed(1)}%"></div></div>
      <span class="bar-num">${(Number(d[valueKey]) || 0).toLocaleString()}</span>
    </div>`).join('');
}

// ---------- 概览 ----------

async function loadStats() {
  const el = $('#stat-cards');
  setLoading(el);
  try {
    const s = await api('/api/stats');
    el.innerHTML = `
      <div class="card"><div class="label">文件总数</div><div class="value">${s.total.toLocaleString()}</div></div>
      <div class="card"><div class="label">重复文件</div><div class="value">${s.duplicates.count.toLocaleString()}</div></div>
      <div class="card accent"><div class="label">浪费空间</div><div class="value">${fmtBytes(s.duplicates.bytes)}</div></div>
      <div class="card"><div class="label">磁盘卷</div><div class="value">${s.by_volume.length}</div></div>`;
    bars($('#by-domain'), s.by_domain, 'domain', 'n');
    bars($('#by-ftype'), s.by_ftype, 'ftype', 'n');
    bars($('#by-temp'), s.by_temp, 'temp', 'n');
    bars($('#by-volume'), s.by_volume, 'volume', 'n');
    // 首启引导：索引为空时给出明确的下一步，避免新用户面对空白页
    const oldGuide = document.getElementById('onboarding');
    if (oldGuide) oldGuide.remove();
    if (s.total === 0) {
      el.insertAdjacentHTML('afterend', `<div class="panel onboarding" id="onboarding">
        <h3>欢迎使用 NAS Organizer，三步开始</h3>
        <ol>
          <li>确认 <code>config/config.yaml</code> 中的挂载路径指向你的 NAS 目录（只读挂载即可）。</li>
          <li>到「操作」页点击 <b>扫描建索引</b>，让系统读取文件元数据。</li>
          <li>运行 <b>重新分类</b> 与 <b>重复检测</b>，然后到「迁移计划」生成并确认整理方案。</li>
        </ol>
        <p class="hint">所有整理默认只生成计划、不直接移动文件；执行需二次确认，且可随时回滚。</p>
        <div class="btn-row"><button class="primary" onclick="goTab('actions')">前往操作页 →</button></div>
      </div>`);
    }
  } catch (e) { setError(el, e.message); }
}

// ---------- 文件浏览（筛选 + 分页） ----------

const FILE_PAGE_SIZE = 50;
let fileOffset = 0;
async function loadFiles(page = 1) {
  const el = $('#file-list');
  fileOffset = (page - 1) * FILE_PAGE_SIZE;
  if (page === 1) {
    // 首次加载：用当前索引的统计结果填充筛选下拉，避免硬编码分类值
    try {
      const s = await api('/api/stats');
      const fill = (sel, items, key) => {
        const cur = $(sel).value;
        $(sel).innerHTML = `<option value="">全部</option>` +
          items.map(d => `<option value="${escapeHtml(d[key] || '')}">${escapeHtml(d[key] || '未分类')} (${d.n})</option>`).join('');
        $(sel).value = cur;
      };
      fill('#file-domain', s.by_domain, 'domain');
      fill('#file-ftype', s.by_ftype, 'ftype');
      fill('#file-temp', s.by_temp, 'temp');
    } catch (_) { /* 下拉填充失败不影响主列表 */ }
  }
  setLoading(el, '加载文件索引…');
  try {
    const params = new URLSearchParams({ limit: String(FILE_PAGE_SIZE), offset: String(fileOffset) });
    for (const [k, id] of [['domain','#file-domain'], ['ftype','#file-ftype'], ['temp','#file-temp']]) {
      const v = $(id).value; if (v) params.set(k, v);
    }
    const rows = await api('/api/files?' + params.toString());
    $('#file-page-info').textContent = `第 ${page} 页 · 本页 ${rows.length} 条`;
    if (!rows.length) { setEmpty(el, page > 1 ? '没有更多文件了。' : '索引为空，先在「操作」页运行扫描。'); return; }
    el.innerHTML = `<div class="panel"><div class="table-scroll"><table>
      <tr><th>路径</th><th>类型</th><th>归属域</th><th>温度</th><th>大小</th><th>置信度</th><th>判定方式</th></tr>
      ${rows.map(f => `<tr><td class="path">${escapeHtml(f.path)}</td><td>${escapeHtml(f.ftype || '-')}</td>
        <td>${escapeHtml(f.domain || '-')}</td><td>${escapeHtml(f.temp || '-')}</td><td>${fmtBytes(f.size)}</td>
        <td>${f.confidence != null ? Number(f.confidence).toFixed(2) : '-'}</td>
        <td>${escapeHtml(f.classified_by || '-')}${f.is_duplicate ? ' · 重复' : ''}</td></tr>`).join('')}
      </table></div>
      <div class="btn-row">
        <button onclick="loadFiles(${Math.max(1, page - 1)})" ${page <= 1 ? 'disabled' : ''}>上一页</button>
        <button onclick="loadFiles(${page + 1})" ${rows.length < FILE_PAGE_SIZE ? 'disabled' : ''}>下一页</button>
      </div></div>`;
  } catch (e) { setError(el, e.message); }
}

// ---------- 搜索 ----------

let searchOffset = 0;
async function loadSearchStats() {
  try {
    const s = await api('/api/search/stats');
    $('#search-stats').textContent = s.fts5
      ? `FTS5 索引已就绪，已索引 ${s.indexed} 条。`
      : '当前环境无 FTS5，将降级为 LIKE 模糊匹配（先运行扫描建索引）。';
  } catch (e) { $('#search-stats').textContent = '索引状态获取失败：' + e.message; }
}

async function loadSearch(reset = true) {
  const q = $('#search-q').value.trim();
  if (!q) { setEmpty($('#search-list'), '请输入文件名关键词后搜索'); return; }
  if (reset) searchOffset = 0;
  setLoading($('#search-list'), '搜索中…');
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(q)}&limit=50&offset=${searchOffset}`);
    if (!r.results.length && searchOffset === 0) {
      setEmpty($('#search-list'), `未找到与「${q}」匹配的文件（引擎 ${r.engine}）`);
      return;
    }
    const rows = r.results.map(f => `<tr>
      <td class="path">${escapeHtml(f.path)}</td>
      <td>${escapeHtml(f.ftype || '-')}</td><td>${escapeHtml(f.domain || '-')}</td>
      <td>${escapeHtml(f.temp || '-')}</td><td>${f.score != null ? Number(f.score).toFixed(2) : '-'}</td></tr>`).join('');
    const more = r.count >= 50
      ? `<div class="btn-row"><button onclick="loadSearch(false)">加载更多</button></div>` : '';
    $('#search-list').innerHTML = `<div class="panel"><h3>命中 ${r.count} 项 · 引擎 ${escapeHtml(r.engine)}</h3>
      <div class="table-scroll"><table><tr><th>路径</th><th>类型</th><th>归属域</th><th>温度</th><th>相关度</th></tr>${rows}</table></div>${more}</div>`;
    if (!reset) searchOffset += r.count;
  } catch (e) { setError($('#search-list'), e.message); }
}

async function reindex() {
  if (!confirm('确认重建全文索引？只更新索引，不移动或修改任何文件。')) return;
  try {
    const r = await api('/api/search/reindex', { method: 'POST', body: { confirm: true } });
    toast(`索引重建完成：${r.indexed} 条`, 'success');
    loadSearchStats();
  } catch (e) { toast('重建失败：' + e.message, 'error'); }
}

// ---------- 重复文件 ----------

async function loadDuplicates() {
  const el = $('#dup-list');
  setLoading(el);
  try {
    const list = await api('/api/duplicates');
    el.innerHTML = list.length ? list.map(g => `
      <div class="panel">
        <h3>${g.count} 个重复 · 浪费 ${fmtBytes(g.wasted_bytes)}</h3>
        <div class="table-scroll"><table><tr><th>路径</th><th>大小</th><th>修改时间</th></tr>
        ${g.files.map(f => `<tr><td class="path">${escapeHtml(f.path)}</td><td>${fmtBytes(f.size)}</td>
          <td>${fmtTime(f.mtime)}</td></tr>`).join('')}
        </table></div>
      </div>`).join('') : '<div class="panel state empty">暂无重复文件，或尚未运行查重。</div>';
  } catch (e) { setError(el, e.message); }
}

// ---------- 虚拟目录 ----------

async function loadViews() {
  const listEl = $('#view-list');
  setLoading(listEl);
  try {
    const [stats, data] = await Promise.all([api('/api/views/stats'), api('/api/views')]);
    $('#view-stats').textContent = `${stats.total_tags} 个标签视图 · ${stats.total_assignments} 次标注 · 源文件零改动`;
    const groups = Object.entries(data.groups || {});
    listEl.innerHTML = groups.length ? groups.map(([prefix, tags]) => `
      <div class="panel"><h3>${escapeHtml(prefix)} (${tags.length})</h3>
        <div class="chip-row">${tags.map(t => `
          <button class="chip" onclick="loadView(${jsArg(t.name)})">${escapeHtml(t.name)} <span class="chip-num">${t.file_count}</span></button>
        `).join('')}</div>
        <div id="view-detail-${escapeHtml(prefix)}"></div>
      </div>`).join('') : '<div class="state empty">暂无标签。运行「自动打标」或由 Agent 调用 view_tag 生成。</div>';
  } catch (e) { setError(listEl, e.message); }
}

async function loadView(tag) {
  try {
    const r = await api('/api/views/' + encodeURIComponent(tag) + '?limit=100');
    const id = 'view-detail-' + (tag.split(':')[0] || '自定义');
    const el = document.getElementById(id);
    if (!el) { toast('无法定位视图详情容器', 'error'); return; }
    el.innerHTML = r.files.length
      ? `<div class="table-scroll"><table><tr><th>路径</th><th>类型</th><th>归属域</th></tr>
         ${r.files.map(f => `<tr><td class="path">${escapeHtml(f.path)}</td><td>${escapeHtml(f.ftype || '-')}</td><td>${escapeHtml(f.domain || '-')}</td></tr>`).join('')}
         </table></div>`
      : `<p class="hint">视图「${escapeHtml(tag)}」暂无文件。</p>`;
  } catch (e) { toast('视图加载失败：' + e.message, 'error'); }
}

async function autoTag() {
  if (!confirm('确认按 域/类型/温度/年份 自动打标？只写标签索引，不移动文件。')) return;
  try {
    const r = await api('/api/views/auto-tag', { method: 'POST', body: { confirm: true } });
    toast(`自动打标完成：${r.files} 个文件，${r.tag_assignments} 次标注`, 'success');
    loadViews();
  } catch (e) { toast('打标失败：' + e.message, 'error'); }
}

// ---------- 迁移计划 ----------

async function loadPlans() {
  const el = $('#plan-list');
  setLoading(el);
  try {
    const plans = await api('/api/plans');
    el.innerHTML = plans.length ? plans.map(p => `
      <div class="panel">
        <h3>#${p.id} ${escapeHtml(p.name || '')} <span class="badge badge-${escapeHtml(p.status)}">${escapeHtml(p.status)}</span> · ${p.item_count} 项</h3>
        <div class="btn-row">
          <button onclick="viewPlan(${p.id})">查看明细</button>
          <button class="danger" onclick="execPlan(${p.id})">执行</button>
        </div>
        <div id="plan-detail-${p.id}"></div>
      </div>`).join('') : '<div class="state empty">暂无迁移计划。</div>';
  } catch (e) { setError(el, e.message); }
}

async function viewPlan(id) {
  const el = document.getElementById('plan-detail-' + id);
  if (!el) return;
  setLoading(el, '加载明细…');
  try {
    const p = await api('/api/plans/' + id);
    const rows = p.items.slice(0, 100).map(i => `<tr>
      <td>${i.id}</td><td class="path">${escapeHtml(i.src)}</td><td class="path">${escapeHtml(i.dst || '-')}</td>
      <td>${escapeHtml(i.reason || '')}</td><td><span class="badge">${escapeHtml(i.status)}</span></td></tr>`).join('');
    el.innerHTML = `<div class="table-scroll"><table><tr><th>ID</th><th>源</th><th>目标</th><th>原因</th><th>状态</th></tr>${rows}</table></div>
      ${p.items.length > 100 ? `<p class="hint">仅显示前 100 / ${p.items.length} 项，完整清单请用 CLI。</p>` : ''}`;
  } catch (e) { setError(el, e.message); }
}

async function createPlan() {
  try {
    const r = await api('/api/plans', { method: 'POST', body: { name: 'web-plan-' + Date.now(), limit: 100 } });
    toast(`已生成计划 #${r.id}，共 ${r.item_count} 项`, 'success');
    loadPlans();
  } catch (e) { toast('生成失败：' + e.message, 'error'); }
}

async function execPlan(id) {
  if (!confirm(`确认执行计划 #${id}？此操作会真实移动文件，可在操作页回滚。`)) return;
  try {
    const r = await api(`/api/plans/${id}/execute`, { method: 'POST', body: { confirm: true } });
    toast(`执行完成：成功 ${r.done}，失败 ${r.failed}`, 'success');
    loadPlans();
  } catch (e) { toast('执行失败：' + e.message, 'error'); }
}

// ---------- 清理发现 ----------

async function loadCleanupKinds() {
  try {
    const r = await api('/api/cleanup/kinds');
    $('#cleanup-kind').innerHTML = '<option value="">全部类型</option>' +
      r.kinds.map(k => `<option value="${escapeHtml(k)}">${escapeHtml(k)}</option>`).join('');
  } catch (e) { toast('清理类型加载失败：' + e.message, 'error'); }
}

async function loadCleanup() {
  const el = $('#cleanup-list');
  const kind = $('#cleanup-kind').value;
  setLoading(el, '扫描中（只读）…');
  try {
    const r = await api('/api/cleanup?limit=500' + (kind ? '&kinds=' + encodeURIComponent(kind) : ''));
    $('#cleanup-summary').textContent = `扫描 ${r.scanned_dirs} 个目录 · ${r.total_count} 个候选 · 可释放 ${fmtBytes(r.total_bytes)}`;
    if (!r.candidates.length) { setEmpty(el, '未发现清理候选。'); return; }
    el.innerHTML = `<div class="panel"><div class="table-scroll"><table>
      <tr><th>类型</th><th>路径</th><th>大小</th><th>修改时间</th><th>原因</th></tr>
      ${r.candidates.map(c => `<tr><td><span class="badge">${escapeHtml(c.kind)}</span></td>
        <td class="path">${escapeHtml(c.path)}</td><td>${fmtBytes(c.size)}</td>
        <td>${fmtTime(c.mtime)}</td><td>${escapeHtml(c.reason)}</td></tr>`).join('')}
      </table></div>${r.total_count > 500 ? `<p class="hint">仅显示前 500 项。</p>` : ''}</div>`;
  } catch (e) { setError(el, e.message); }
}

// ---------- 回收站 ----------

async function loadRecycle() {
  const el = $('#recycle-list');
  setLoading(el);
  try {
    const stage = $('#recycle-stage').value;
    const [stats, items] = await Promise.all([
      api('/api/recycle/stats'),
      api('/api/recycle?limit=200' + (stage ? '&stage=' + stage : '')),
    ]);
    $('#recycle-stats').textContent = `隔离 ${stats.stage1.count} · 待清除 ${stats.stage2.count} · 占用 ${fmtBytes(stats.stage1.bytes + stats.stage2.bytes)}`;
    el.innerHTML = items.length ? `<div class="panel"><div class="table-scroll"><table>
      <tr><th>阶段</th><th>原路径</th><th>大小</th><th>移除时间</th><th>原因</th></tr>
      ${items.map(i => `<tr><td><span class="badge badge-stage${i.stage}">${i.stage === 1 ? '隔离' : '待清除'}</span></td>
        <td class="path">${escapeHtml(i.original_path)}</td><td>${fmtBytes(i.size)}</td>
        <td>${fmtTime(i.removed_at)}</td><td>${escapeHtml(i.reason || '')}</td></tr>`).join('')}
      </table></div></div>` : '<div class="state empty">回收站为空。</div>';
  } catch (e) { setError(el, e.message); }
}

// ---------- 复核台 ----------

async function loadReview() {
  const el = $('#review-list');
  setLoading(el);
  try {
    const [stats, queue, history] = await Promise.all([
      api('/api/review/stats'), api('/api/review/queue?limit=100'), api('/api/review/history?limit=50'),
    ]);
    $('#review-stat').textContent =
      `低置信度 ${stats.low_confidence} · 冲突 ${stats.plan_conflicts} · 已裁决 ${stats.decisions_made}（阈值 ${stats.threshold}）`;
    const items = [
      ...(queue.low_confidence || []).map(it => `
        <div class="panel">
          <h3>[文件 #${it.id}] ${escapeHtml(it.ftype || '?')} / ${escapeHtml(it.domain || '?')} · 置信度 ${it.confidence}</h3>
          <p class="path">${escapeHtml(it.path)}</p>
          <div class="btn-row">
            <select id="dom-${it.id}" aria-label="归属域"><option>个人</option><option>工作</option><option>生活</option><option>资源</option></select>
            <button onclick="submitReview(${it.id}, 'domain', $('#dom-${it.id}').value)">修正归属域</button>
            <select id="typ-${it.id}" aria-label="类型"><option>文档</option><option>视频</option><option>音乐</option><option>图片</option><option>图书</option><option>其他</option></select>
            <button onclick="submitReview(${it.id}, 'ftype', $('#typ-${it.id}').value)">修正类型</button>
          </div>
        </div>`),
      ...(queue.plan_conflicts || []).map(it => `
        <div class="panel"><h3>[计划项 #${it.plan_item_id}] 执行失败</h3>
        <p class="path">${escapeHtml(it.path)}</p><p class="error">${escapeHtml(it.error || '')}</p></div>`),
    ];
    el.innerHTML = items.length ? items.join('') : '<div class="state empty">暂无待复核项。运行「重新分类」后低置信度文件会出现在这里。</div>';
    $('#review-history').innerHTML = (history || []).length
      ? `<div class="table-scroll"><table><tr><th>时间</th><th>文件</th><th>字段</th><th>变更</th></tr>
         ${history.map(h => `<tr><td>${fmtTime(h.decided_at)}</td><td class="path">${escapeHtml(h.path)}</td>
           <td>${escapeHtml(h.field)}</td><td>${escapeHtml(h.old_value || '-')} → ${escapeHtml(h.new_value)}</td></tr>`).join('')}</table></div>`
      : '<p class="hint">暂无裁决历史。</p>';
  } catch (e) { setError(el, e.message); }
}

async function submitReview(fileId, field, value) {
  if (!confirm(`确认把文件 #${fileId} 的 ${field} 改为「${value}」？会写入反馈供同类文件归类。`)) return;
  try {
    const r = await api('/api/review/decide', { method: 'POST', body: { file_id: fileId, field, value } });
    toast(`裁决完成：学习 ${r.keywords_learned} 个关键词`, 'success');
    loadReview(); loadStats();
  } catch (e) { toast('裁决失败：' + e.message, 'error'); }
}

// ---------- 媒体与能力 ----------

const CAPABILITY_LABELS = {
  exif: 'EXIF 照片日期', id3: 'ID3 音乐标签', phash: 'pHash 相似图片',
  ocr: 'OCR 文字提取', ffmpeg: 'FFmpeg 视频关键帧', rclone: 'rclone 远端索引', ai: '本地 AI 兜底',
};

async function loadMedia() {
  const el = $('#capability-grid');
  setLoading(el, '探测能力中…');
  try {
    const caps = await api('/api/plugins/capability');
    const flat = {
      exif: caps.media?.exif, id3: caps.media?.id3, phash: caps.phash?.phash,
      ocr: caps.plugins?.ocr, ffmpeg: caps.external?.ffmpeg, rclone: caps.external?.rclone,
      ai: caps.ai?.enabled,
    };
    el.innerHTML = Object.entries(flat).map(([k, v]) => `
      <div class="cap-item ${v ? 'on' : 'off'}">
        <span class="cap-dot"></span><span>${CAPABILITY_LABELS[k] || k}</span>
        <span class="cap-state">${v ? '可用' : '未启用'}</span>
      </div>`).join('');
  } catch (e) { setError(el, e.message); }
}

async function loadSimilar(kind) {
  const el = $('#similar-list');
  setLoading(el, '分析中（只读）…');
  try {
    const r = await api(kind === 'phash' ? '/api/phash/similar?limit=50' : '/api/video/similar?limit=50');
    const groups = r.groups || [];
    if (!groups.length) {
      setEmpty(el, kind === 'phash' ? '未发现相似图片（或 Pillow 未安装）。' : '未发现相似视频（或 FFmpeg 未安装）。');
      return;
    }
    el.innerHTML = groups.map(g => `<div class="panel"><h3>距离 ${g.max_distance} · ${g.paths.length} 个文件</h3>
      ${g.paths.map(p => `<p class="path">${escapeHtml(p)}</p>`).join('')}</div>`).join('');
  } catch (e) { setError(el, e.message); }
}

// ---------- 任务与调度 ----------

async function loadTasks() {
  const el = $('#task-list');
  setLoading(el);
  try {
    const r = await api('/api/tasks?limit=50');
    const rows = (r.history || []).map(t => `<tr>
      <td>${escapeHtml(t.name || '-')}</td><td><span class="badge">${escapeHtml(t.status || '-')}</span></td>
      <td>${fmtTime(t.started_at)}</td><td>${t.duration_sec != null ? Number(t.duration_sec).toFixed(1) + 's' : '-'}</td>
      <td>${escapeHtml(t.source || '-')}</td></tr>`).join('');
    el.innerHTML = `<div class="panel"><h3>运行中 ${r.running_count || 0}</h3>
      ${rows ? `<div class="table-scroll"><table><tr><th>任务</th><th>状态</th><th>开始</th><th>耗时</th><th>来源</th></tr>${rows}</table></div>`
             : '<p class="hint">暂无历史任务。</p>'}</div>`;
    const sched = await api('/api/schedule');
    const jobs = sched.jobs || [];
    $('#schedule-list').innerHTML = jobs.length
      ? `<div class="panel"><div class="table-scroll"><table><tr><th>配置</th></tr>
         ${jobs.map(j => `<tr><td><code>${escapeHtml(JSON.stringify(j))}</code></td></tr>`).join('')}</table></div></div>`
      : '<p class="hint">未配置定时任务。</p>';
    // 操作审计时间线
    const ops = await api('/api/audit/operations?limit=100');
    $('#op-timeline').innerHTML = ops.operations.length
      ? `<div class="panel"><p class="hint">共 ${ops.total} 条操作记录（显示最近 ${ops.operations.length} 条）</p>
         <div class="table-scroll"><table><tr><th>时间</th><th>计划</th><th>动作</th><th>源 → 目标</th><th>状态</th></tr>
         ${ops.operations.map(o => `<tr><td>${fmtTime(o.executed_at)}</td><td>${escapeHtml(o.plan || '-')}</td>
           <td>${escapeHtml(o.action)}</td><td class="path">${escapeHtml(o.src)} → ${escapeHtml(o.dst)}</td>
           <td><span class="badge ${o.rolled_back ? 'badge-failed' : 'badge-done'}">${o.rolled_back ? '已回滚' : '已执行'}</span></td></tr>`).join('')}
         </table></div></div>`
      : '<p class="hint">暂无文件操作记录。</p>';
  } catch (e) { setError(el, e.message); }
}

// ---------- 维护 / 危险操作 ----------

async function post(path, body, label) {
  toast(`${label}中...`, 'info');
  try {
    const r = await api(path, { method: 'POST', body });
    toast(`${label}完成: ${JSON.stringify(r).slice(0, 80)}`, 'success');
    loadedTabs.delete('overview');
    loadStats();
  } catch (e) { toast(`${label}失败: ${e.message}`, 'error'); }
}

async function rollback() {
  const limit = parseInt($('#rollback-limit').value) || 10;
  if (!confirm(`确认回滚最近 ${limit} 条操作？`)) return;
  try {
    const r = await api('/api/rollback', { method: 'POST', body: { confirm: true, limit } });
    toast(`回滚完成：成功 ${r.done}，失败 ${r.failed}`, 'success');
  } catch (e) { toast('回滚失败：' + e.message, 'error'); }
}

// ---------- 启动 ----------

async function boot() {
  try {
    const me = await api('/api/auth/me');
    $('#user-label').textContent = '已登录：' + (me.username || 'admin');
    hideLogin();
    await loadStats();
    loadedTabs.add('overview');
  } catch (e) {
    if (e.message !== '未授权') toast('加载失败：' + e.message, 'error');
    showLogin();
  }
}

// PWA：注册 Service Worker（失败不影响主应用；不缓存任何 /api/ 数据）
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
}

$('#login-password').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePwdModal(); });
boot();
