// Token 存在 sessionStorage：关闭浏览器标签即失效，降低公共设备上的泄露风险
const TOKEN_KEY = 'nas_organizer_token';
const THEME_KEY = 'nas_organizer_theme';
const getToken = () => sessionStorage.getItem(TOKEN_KEY) || '';
const setToken = (t) => t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY);

const $ = (s) => document.querySelector(s);
/** 以编程方式切换页面（首启引导等场景使用），复用 hash 路由 */
function goTab(name) {
  const target = '#/' + name;
  if (location.hash === target) activatePage(name);
  else location.hash = target;
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

// ---------- 多页面路由（hash 路由；刷新/前进后退都能还原当前页面） ----------

const loadedTabs = new Set();
const TAB_LOADERS = {
  overview: loadStats, files: loadFiles, search: loadSearchStats, duplicates: loadDuplicates,
  views: loadViews, plans: loadPlans, cleanup: loadCleanupKinds,
  recycle: loadRecycle, review: loadReview, mounts: loadMounts,
  media: loadMedia, tasks: loadTasks,
};
const VALID_PAGES = new Set(Object.keys(TAB_LOADERS));

/** 切换到指定页面：更新导航态、面包屑、标题，并懒加载首次进入的页面 */
function activatePage(name) {
  if (!VALID_PAGES.has(name)) name = 'overview';
  const btn = document.querySelector(`nav button[data-tab="${name}"]`);
  document.querySelectorAll('nav button').forEach(x => {
    x.classList.remove('active');
    x.setAttribute('aria-selected', 'false');
  });
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  if (btn) {
    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
    $('#page-title').textContent = btn.dataset.title || name;
    const group = btn.closest('.nav-group');
    $('#page-group').textContent = group?.querySelector('.nav-group-title')?.textContent || 'NAS Organizer';
  }
  const section = $('#tab-' + name);
  if (section) section.classList.add('active');
  if (!loadedTabs.has(name)) {
    loadedTabs.add(name);
    const loader = TAB_LOADERS[name];
    if (loader) Promise.resolve(loader()).catch(e => toast(e.message, 'error'));
  }
  // 切换页面后回到内容顶部
  $('.main')?.scrollTo?.({ top: 0, behavior: 'auto' });
  window.scrollTo({ top: 0, behavior: 'auto' });
}

function currentPageFromHash() {
  const raw = (location.hash || '').replace(/^#\/?/, '').split('/')[0];
  return VALID_PAGES.has(raw) ? raw : 'overview';
}

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => { location.hash = '#/' + b.dataset.tab; };
});
// 浏览器前进/后退、地址栏直接输入 #/xxx 都会触发
window.addEventListener('hashchange', () => activatePage(currentPageFromHash()));

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

// ---------- 文件浏览（资源管理器式：文件树 + 面包屑 + 双视图） ----------

const FILE_VIEW_KEY = 'nas_organizer_file_view';
let fileView = localStorage.getItem(FILE_VIEW_KEY) || 'grid';
let currentDir = '';
let treeRoots = [];
let treeExpanded = new Set();

const FILE_EXT_ICON = {
  mp4:'🎬', mkv:'🎬', avi:'🎬', mov:'🎬', srt:'📄',
  mp3:'🎵', flac:'🎵', wav:'🎵', m4a:'🎵',
  jpg:'🖼️', jpeg:'🖼️', png:'🖼️', gif:'🖼️', webp:'🖼️',
  pdf:'📕', doc:'📘', docx:'📘', xls:'📗', xlsx:'📗', ppt:'📙', pptx:'📙', txt:'📄', md:'📄',
  epub:'📚', mobi:'📚', azw3:'📚', zip:'🗜️', rar:'🗜️', '7z':'🗜️',
};
function fileIcon(item) {
  if (item.is_dir) return '📁';
  return FILE_EXT_ICON[(item.ext || '').toLowerCase()] || '📄';
}

/** 加载文件树根节点（各扫描卷）并渲染 */
async function loadExplorerRoots() {
  const el = $('#tree-root');
  setLoading(el, '加载目录树…');
  try {
    const r = await api('/api/tree/children?path=&limit=1000');
    treeRoots = r.items || [];
    treeExpanded.clear();
    renderTreeNodeList(el, treeRoots, 0);
  } catch (e) { setError(el, e.message); }
}

/** 渲染一层树节点（可逐级展开） */
function renderTreeNodeList(container, items, depth) {
  if (!items.length) { setEmpty(container, '无目录'); return; }
  container.innerHTML = items.map((it, i) => {
    const key = it.path;
    const expanded = treeExpanded.has(key);
    return `<div class="tree-node-wrap">
      <div class="tree-node" data-key="${escapeHtml(key)}" style="padding-left:${8 + depth * 16}px">
        <button class="tree-arrow ${it.is_dir ? '' : 'leaf'}" onclick="toggleTreeNode(${jsArg(key)}, ${i}, this)" ${it.is_dir ? '' : 'disabled'}>${expanded ? '▾' : '▸'}</button>
        <span class="tree-icon">${fileIcon(it)}</span>
        <span class="tree-name" onclick="openTreeNode(${jsArg(key)}, ${jsArg(it.name)}, ${it.is_dir})" title="${escapeHtml(it.path)}">${escapeHtml(it.name)}</span>
      </div>
      <div class="tree-children" data-children="${escapeHtml(key)}" ${expanded ? '' : 'hidden'}></div>
    </div>`;
  }).join('');
}

/** 展开/收起目录节点：一级一级加载（懒加载） */
async function toggleTreeNode(key, index, btn) {
  const childEl = document.querySelector(`[data-children="${CSS.escape(key)}"]`);
  if (!childEl) return;
  if (treeExpanded.has(key)) {
    treeExpanded.delete(key);
    childEl.hidden = true;
    childEl.innerHTML = '';
    btn.textContent = '▸';
    return;
  }
  treeExpanded.add(key);
  childEl.hidden = false;
  btn.textContent = '▾';
  if (childEl.dataset.loaded === '1') return;
  setLoading(childEl, '展开中…');
  try {
    const r = await api('/api/tree/children?path=' + encodeURIComponent(key) + '&limit=1000');
    const dirs = (r.items || []).filter(x => x.is_dir);
    childEl.dataset.loaded = '1';
    renderTreeNodeList(childEl, dirs, (btn.closest('.tree-node')?.style.paddingLeft?.replace(/\D/g, '') / 16) || 1);
  } catch (e) {
    childEl.dataset.loaded = '';
    setError(childEl, e.message);
  }
}

/** 点击树节点名称：进入该目录（打开新的一级页面） */
function openTreeNode(path, name, isDir) {
  if (!isDir) { toast('这是文件，请在右侧目录内容中查看', 'info'); return; }
  browseDir(path);
}

/** 右侧面包屑导航 */
async function browseDir(path) {
  currentDir = path || '';
  setLoading($('#explorer-content'), '打开目录…');
  try {
    const [anc, listing] = await Promise.all([
      api('/api/tree/ancestors?path=' + encodeURIComponent(currentDir)),
      api('/api/tree/children?path=' + encodeURIComponent(currentDir) + '&limit=1000'),
    ]);
    renderCrumbs(anc.items || []);
    $('#explorer-title').textContent = path ? (listing.name || path.split('/').pop()) : '所有文件夹';
    if (listing.denied) { setError($('#explorer-content'), '目录不在允许的扫描范围内'); return; }
    $('#explorer-summary').textContent =
      `${listing.dir_count || 0} 个子目录 · ${listing.file_count || 0} 个文件` +
      (listing.total > 1000 ? `（显示前 1000 / ${listing.total}）` : '');
    renderExplorerItems(listing.items || []);
    // 同步展开左侧树到当前目录
    expandTreeToCurrent(path, anc.items || []);
  } catch (e) { setError($('#explorer-content'), e.message); }
}

/** 渲染面包屑（点任一级可跳回） */
function renderCrumbs(items) {
  const root = `<button class="crumb-link" onclick="browseDir('')">🏠 根</button>`;
  const crumbs = items.map((a, i) =>
    `<span class="crumb-sep">/</span><button class="crumb-link ${i === items.length - 1 ? 'current' : ''}" onclick="browseDir(${jsArg(a.path)})">${escapeHtml(a.name)}</button>`
  ).join('');
  $('#explorer-crumbs').innerHTML = root + crumbs;
}

/** 同步展开左侧树到当前目录（逐级展开祖先） */
async function expandTreeToCurrent(path, ancestors) {
  if (!path || !ancestors.length) return;
  let parentEl = $('#tree-root');
  for (const a of ancestors) {
    const node = parentEl.querySelector(`[data-key="${CSS.escape(a.path)}"]`);
    if (!node) break;
    const arrow = node.querySelector('.tree-arrow');
    if (arrow && !treeExpanded.has(a.path)) {
      await toggleTreeNode(a.path, 0, arrow);
    }
    parentEl = parentEl.querySelector(`[data-children="${CSS.escape(a.path)}"]`) || parentEl;
  }
}

/** 渲染右侧内容：网格 / 列表 两套显示逻辑 */
function renderExplorerItems(items) {
  const el = $('#explorer-content');
  if (!items.length) { setEmpty(el, '此目录为空（索引中无文件）。'); return; }
  if (fileView === 'grid') {
    el.innerHTML = `<div class="file-grid">${items.map(it => `
      <div class="file-tile ${it.is_dir ? 'is-dir' : ''}" onclick="${it.is_dir ? `browseDir(${jsArg(it.path)})` : `toast(${jsArg('文件：' + it.name)}, 'info')`}" title="${escapeHtml(it.path)}">
        <div class="tile-icon">${fileIcon(it)}</div>
        <div class="tile-name">${escapeHtml(it.name)}</div>
        <div class="tile-meta">${it.is_dir ? `${it.file_count || 0} 项` : fmtBytes(it.size)}</div>
      </div>`).join('')}</div>`;
  } else {
    el.innerHTML = `<div class="panel"><div class="table-scroll"><table>
      <tr><th>名称</th><th>类型</th><th>大小</th><th>修改时间</th></tr>
      ${items.map(it => `<tr class="clickable-row" onclick="${it.is_dir ? `browseDir(${jsArg(it.path)})` : `toast(${jsArg('文件：' + it.name)}, 'info')`}">
        <td><span class="row-icon">${fileIcon(it)}</span> ${escapeHtml(it.name)}</td>
        <td>${it.is_dir ? '文件夹' : escapeHtml((it.ext || '文件').toUpperCase())}</td>
        <td>${it.is_dir ? '—' : fmtBytes(it.size)}</td>
        <td>${it.is_dir ? '—' : fmtTime(it.mtime)}</td></tr>`).join('')}
      </table></div></div>`;
  }
}

/** 切换显示逻辑：网格 / 列表 */
function setExplorerView(view) {
  fileView = view === 'list' ? 'list' : 'grid';
  localStorage.setItem(FILE_VIEW_KEY, fileView);
  $('#view-btn-grid').classList.toggle('active', fileView === 'grid');
  $('#view-btn-list').classList.toggle('active', fileView === 'list');
  // 用当前目录数据重绘，避免重复请求
  browseDir(currentDir);
}

/** 左侧目录树关键字筛选（前端按已加载节点名称过滤） */
function filterTree(kw) {
  const q = (kw || '').trim().toLowerCase();
  document.querySelectorAll('#tree-root .tree-node').forEach(n => {
    const name = (n.querySelector('.tree-name')?.textContent || '').toLowerCase();
    n.style.display = (!q || name.includes(q)) ? '' : 'none';
  });
}

async function loadFiles() {
  // 兼容首启引导等旧调用：进入文件浏览页即加载目录树与根目录
  await loadExplorerRoots();
  await browseDir(currentDir);
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

// ---------- 磁盘挂载 / 扫描卷绑定 ----------

const fmtPct = (used, total) => total > 0 ? ((used / total) * 100).toFixed(0) + '%' : '-';

async function loadMounts() {
  const disksEl = $('#mount-disk-list');
  const volsEl = $('#mount-volume-list');
  setLoading(disksEl, '探测挂载点中…');
  setLoading(volsEl);
  try {
    const r = await api('/api/mounts');
    const roots = r.allow_roots || [];
    $('#mount-roots').textContent = '允许绑定的根目录：' + (roots.length ? roots.join('、') : '（未配置，请先设置扫描卷）');

    // 容器内已挂载磁盘
    if (!r.mounts.length) {
      setEmpty(disksEl, '未探测到允许根内的挂载点（Windows 本机或非 Linux 环境会降级显示配置卷）。');
    } else {
      disksEl.innerHTML = `<div class="panel"><div class="table-scroll"><table>
        <tr><th>挂载点</th><th>设备</th><th>文件系统</th><th>容量</th><th>已用</th><th>使用率</th><th>绑定状态</th></tr>
        ${r.mounts.map(m => `<tr>
          <td class="path">${escapeHtml(m.mount_point)}</td>
          <td class="path">${escapeHtml(m.device || '-')}</td>
          <td>${escapeHtml(m.fstype || '-')}</td>
          <td>${fmtBytes(m.total_bytes)}</td><td>${fmtBytes(m.used_bytes)}</td>
          <td>${fmtPct(m.used_bytes, m.total_bytes)}</td>
          <td>${m.bound_name ? `<span class="badge badge-done">已绑定 · ${escapeHtml(m.bound_name)}</span>`
                              : '<span class="badge">未绑定</span>'}</td></tr>`).join('')}
      </table></div></div>`;
    }

    // 当前扫描卷（含解绑按钮）
    if (!r.volumes.length) {
      setEmpty(volsEl, '暂无扫描卷，请在上方绑定一个容器内目录。');
    } else {
      volsEl.innerHTML = `<div class="panel"><div class="table-scroll"><table>
        <tr><th>名称</th><th>路径</th><th>角色</th><th>来源</th><th>状态</th><th>操作</th></tr>
        ${r.volumes.map(v => `<tr>
          <td><b>${escapeHtml(v.name)}</b></td>
          <td class="path">${escapeHtml(v.path)}</td>
          <td>${escapeHtml(v.role)}</td>
          <td><span class="badge">${v.source === 'static' ? '静态配置' : '动态绑定'}</span></td>
          <td>${v.exists
                ? (v.enabled ? '<span class="badge badge-done">生效中</span>' : '<span class="badge badge-failed">已停用</span>')
                : '<span class="badge badge-failed">目录不存在</span>'}</td>
          <td>${v.enabled
                ? `<button class="danger" onclick="unbindMount(${jsArg(v.name)})">解绑</button>`
                : `<button onclick="rebindMount(${jsArg(v.name)}, ${jsArg(v.path)}, ${jsArg(v.role)})">重新绑定</button>`}</td></tr>`).join('')}
      </table></div></div>`;
    }
    $('#mounts-updated').textContent = '共 ' + r.mounts.length + ' 个挂载点 · ' + r.volumes.length + ' 个扫描卷';
  } catch (e) { setError(disksEl, e.message); setError(volsEl, e.message); }
}

async function bindMount() {
  const name = $('#mount-name').value.trim();
  const path = $('#mount-path').value.trim();
  const role = $('#mount-role').value;
  if (!name || !path) { toast('请填写名称与容器内路径', 'error'); return; }
  if (!confirm(`确认绑定扫描卷「${name}」→ ${path}？\n只登记为扫描源，不会移动或修改任何文件。`)) return;
  try {
    await api('/api/mounts', { method: 'POST', body: { name, path, role, confirm: true } });
    toast(`已绑定扫描卷「${name}」，重新运行扫描即可生效`, 'success');
    $('#mount-name').value = ''; $('#mount-path').value = '';
    loadedTabs.delete('mounts');
    loadMounts();
  } catch (e) { toast('绑定失败：' + e.message, 'error'); }
}

async function unbindMount(name) {
  if (!confirm(`确认解绑扫描卷「${name}」？\n不会删除磁盘上的任何文件，仅从扫描源移除。`)) return;
  try {
    await api('/api/mounts/' + encodeURIComponent(name) + '?confirm=true', { method: 'DELETE' });
    toast(`已解绑扫描卷「${name}」`, 'success');
    loadedTabs.delete('mounts');
    loadMounts();
  } catch (e) { toast('解绑失败：' + e.message, 'error'); }
}

async function rebindMount(name, path, role) {
  if (!confirm(`确认重新启用扫描卷「${name}」？`)) return;
  try {
    await api('/api/mounts', { method: 'POST', body: { name, path, role, confirm: true } });
    toast(`已重新启用扫描卷「${name}」`, 'success');
    loadedTabs.delete('mounts');
    loadMounts();
  } catch (e) { toast('重新绑定失败：' + e.message, 'error'); }
}

// ---------- 扫描进度（实时轮询） / 定期增量 ----------

let scanPollTimer = null;

function renderScanProgress(s) {
  const slots = document.querySelectorAll('.scan-progress-slot');
  if (!slots.length) return;
  let html = '';
  if (s.status === 'idle') {
    html = `<div class="scan-idle">当前没有扫描任务。点击「增量扫描」或「全量扫描」开始。</div>`;
  } else {
    const discovering = s.phase === 'discovering' && s.status === 'running';
    const pct = discovering || s.total <= 0 ? 0 : Math.min(100, (s.done / s.total) * 100);
    const pctStr = discovering ? '遍历中' : pct.toFixed(1) + '%';
    const eta = s.status === 'running' && s.eta_sec && !discovering
      ? ` · 预计剩余 ${s.eta_sec >= 60 ? (s.eta_sec / 60).toFixed(1) + ' 分' : s.eta_sec + ' 秒'}` : '';
    const statusLabel = discovering ? '遍历目录' : { running: '扫描中', done: '已完成', error: '失败' }[s.status] || s.status;
    const statusCls = s.status === 'done' ? 'badge-done' : s.status === 'error' ? 'badge-failed' : 'badge-running';
    const barWidth = discovering ? 8 : pct;
    html = `
      <div class="scan-status">
        <span class="badge ${statusCls}">${statusLabel}</span>
        <span class="hint">${s.incremental ? '增量' : '全量'}${s.volume ? ' · 当前卷：' + escapeHtml(s.volume) : ''}</span>
      </div>
      <div class="scan-bar"><div class="scan-bar-fill ${discovering ? 'indeterminate' : ''}" style="width:${barWidth}%"></div></div>
      <div class="scan-nums">
        <span>${discovering ? `正在遍历目录 · 已发现 ${s.done.toLocaleString()} 个文件` : `${s.done.toLocaleString()} / ${s.total.toLocaleString()} 个文件 (${pctStr})`}</span>
        <span>${discovering ? '大盘目录遍历可能需要一会儿…' : `本次入库 ${(s.found || 0).toLocaleString()} · 速度 ${s.speed || 0} 个/秒${eta}`}</span>
      </div>
      ${s.error ? `<p class="error">${escapeHtml(s.error)}</p>` : ''}
      ${Object.keys(s.volumes || {}).length
        ? `<div class="scan-vols">${Object.entries(s.volumes).map(([k, v]) => `<span class="badge">${escapeHtml(k)}: ${Number(v).toLocaleString()}</span>`).join('')}</div>`
        : ''}`;
  }
  slots.forEach(el => { el.innerHTML = html; });
}

async function pollScanProgress() {
  try {
    const s = await api('/api/scan/progress');
    renderScanProgress(s);
    if (s.status === 'running' && !scanPollTimer) {
      scanPollTimer = setInterval(pollScanProgress, 1500);
    } else if (s.status !== 'running' && scanPollTimer) {
      clearInterval(scanPollTimer);
      scanPollTimer = null;
      // 扫描完成后刷新概览统计
      loadedTabs.delete('overview');
      if (currentPageFromHash() === 'overview') loadStats();
    }
  } catch (_) { /* 轮询失败不打断用户 */ }
}

async function startScan(incremental) {
  const label = incremental ? '增量扫描' : '全量扫描';
  if (!confirm(`确认开始${label}？只更新索引，不会移动或修改任何文件。`)) return;
  try {
    const r = await api('/api/scan/start', { method: 'POST', body: { incremental } });
    if (!r.started) { toast(r.reason || '无法启动扫描', 'error'); return; }
    toast(`${label}已在后台启动`, 'success');
    if (!scanPollTimer) scanPollTimer = setInterval(pollScanProgress, 1500);
    pollScanProgress();
  } catch (e) { toast('启动扫描失败：' + e.message, 'error'); }
}

async function loadScanSettings() {
  try {
    const s = await api('/api/scan/settings');
    $('#auto-inc-enable').checked = !!s.auto_incremental;
    $('#auto-inc-interval').value = String(s.interval_minutes || 30);
    $('#auto-inc-status').textContent = s.auto_incremental
      ? `已开启 · 每 ${s.interval_minutes} 分钟自动增量`
      : '未开启';
  } catch (_) {}
}

async function saveScanSettings() {
  const body = {
    auto_incremental: $('#auto-inc-enable').checked,
    interval_minutes: parseInt($('#auto-inc-interval').value, 10) || 30,
    confirm: true,
  };
  try {
    await api('/api/scan/settings', { method: 'POST', body });
    toast('定期增量设置已保存', 'success');
    loadScanSettings();
  } catch (e) { toast('保存设置失败：' + e.message, 'error'); }
}

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
    // 按当前 hash 还原页面（无 hash 时默认概览），刷新/前进后退都落在原页面
    const page = currentPageFromHash();
    activatePage(page);
    if (!location.hash) location.replace('#/' + page);
    // 扫描进度与定期增量设置：后台轮询会在 running 时自动启动
    pollScanProgress();
    loadScanSettings();
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
