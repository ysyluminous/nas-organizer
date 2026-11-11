// Token 存在 sessionStorage：关闭浏览器标签即失效，降低公共设备上的泄露风险
const TOKEN_KEY = 'nas_organizer_token';
const getToken = () => sessionStorage.getItem(TOKEN_KEY) || '';
const setToken = (t) => t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY);

const $ = (s) => document.querySelector(s);

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

const fmtBytes = (b) => {
  if (!b) return '0 B';
  const u = ['B','KB','MB','GB','TB'];
  const i = Math.min(Math.floor(Math.log(b) / Math.log(1024)), u.length - 1);
  return (b / Math.pow(1024, i)).toFixed(1) + ' ' + u[i];
};
const toast = (msg) => {
  const t = $('#toast'); t.textContent = msg; t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
};

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
    toast('密码已修改，所有旧会话已失效');
  } catch (e) {
    $('#pwd-error').textContent = e.message || '修改失败';
  }
}

// ---------- 页面交互 ----------

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('nav button').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    $('#tab-' + b.dataset.tab).classList.add('active');
  };
});

function bars(el, data, labelKey, valueKey) {
  const max = Math.max(...data.map(d => d[valueKey]), 1);
  el.innerHTML = data.map(d => `
    <div class="bar-row">
      <span class="bar-label">${escapeHtml(d[labelKey] || '未分类')}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${d[valueKey]/max*100}%"></div></div>
      <span class="bar-num">${d[valueKey].toLocaleString()}</span>
    </div>`).join('');
}

// 后端路径来自本地文件系统，展示时做 HTML 转义，避免路径中的特殊字符注入页面
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadStats() {
  const s = await api('/api/stats');
  $('#stat-cards').innerHTML = `
    <div class="card"><div class="label">文件总数</div><div class="value">${s.total.toLocaleString()}</div></div>
    <div class="card"><div class="label">重复文件</div><div class="value">${s.duplicates.count.toLocaleString()}</div></div>
    <div class="card"><div class="label">浪费空间</div><div class="value">${fmtBytes(s.duplicates.bytes)}</div></div>
    <div class="card"><div class="label">磁盘卷</div><div class="value">${s.by_volume.length}</div></div>`;
  bars($('#by-domain'), s.by_domain, 'domain', 'n');
  bars($('#by-ftype'), s.by_ftype, 'ftype', 'n');
  bars($('#by-temp'), s.by_temp, 'temp', 'n');
  bars($('#by-volume'), s.by_volume, 'volume', 'n');
}

async function loadDuplicates() {
  const list = await api('/api/duplicates');
  $('#dup-list').innerHTML = list.length ? list.map(g => `
    <div class="panel">
      <h3>${g.count} 个重复 · 浪费 ${fmtBytes(g.wasted_bytes)}</h3>
      <table><tr><th>路径</th><th>大小</th><th>修改时间</th></tr>
      ${g.files.map(f => `<tr><td class="path">${escapeHtml(f.path)}</td><td>${fmtBytes(f.size)}</td>
        <td>${new Date(f.mtime*1000).toLocaleDateString()}</td></tr>`).join('')}
      </table>
    </div>`).join('') : '<div class="panel hint">暂无重复文件，或尚未运行查重。</div>';
}

async function loadPlans() {
  const plans = await api('/api/plans');
  $('#plan-list').innerHTML = plans.length ? plans.map(p => `
    <div class="panel">
      <h3>#${p.id} ${escapeHtml(p.name || '')} <span class="badge">${escapeHtml(p.status)}</span> · ${p.item_count} 项</h3>
      <div class="btn-row">
        <button onclick="viewPlan(${p.id})">查看明细</button>
        <button class="danger" onclick="execPlan(${p.id})">执行</button>
      </div>
    </div>`).join('') : '<div class="panel hint">暂无迁移计划。</div>';
}

async function viewPlan(id) {
  const p = await api('/api/plans/' + id);
  const rows = p.items.slice(0, 100).map(i => `<tr>
    <td>${i.id}</td><td class="path">${escapeHtml(i.src)}</td><td class="path">${escapeHtml(i.dst || '-')}</td>
    <td>${escapeHtml(i.reason || '')}</td><td><span class="badge">${escapeHtml(i.status)}</span></td></tr>`).join('');
  $('#plan-list').insertAdjacentHTML('afterbegin',
    `<div class="panel"><h3>计划 #${id} 明细（前 100 项）</h3>
       <div class="table-scroll"><table><tr><th>ID</th><th>源</th><th>目标</th><th>原因</th><th>状态</th></tr>${rows}</table></div></div>`);
}

async function createPlan() {
  const r = await api('/api/plans', {
    method: 'POST', body: { name: 'web-plan-' + Date.now(), limit: 100 }
  });
  toast(`已生成计划 #${r.id}，共 ${r.item_count} 项`);
  loadPlans();
}

async function execPlan(id) {
  if (!confirm(`确认执行计划 #${id}？此操作会真实移动文件，可在操作页回滚。`)) return;
  try {
    const r = await api(`/api/plans/${id}/execute`, { method: 'POST', body: { confirm: true } });
    toast(`执行完成：成功 ${r.done}，失败 ${r.failed}`);
    loadPlans();
  } catch (e) { toast('执行失败：' + e.message); }
}

async function post(path, body, label) {
  toast(`${label}中...`);
  try {
    const r = await api(path, { method: 'POST', body });
    toast(`${label}完成: ${JSON.stringify(r).slice(0,80)}`);
    loadStats();
  } catch (e) { toast(`${label}失败: ${e.message}`); }
}

async function rollback() {
  const limit = parseInt($('#rollback-limit').value) || 10;
  if (!confirm(`确认回滚最近 ${limit} 条操作？`)) return;
  try {
    const r = await api('/api/rollback', { method: 'POST', body: { confirm: true, limit } });
    toast(`回滚完成：成功 ${r.done}，失败 ${r.failed}`);
  } catch (e) { toast('回滚失败：' + e.message); }
}

// ---------- 启动 ----------

async function boot() {
  try {
    const me = await api('/api/auth/me');
    $('#user-label').textContent = '已登录：' + (me.username || 'admin');
    hideLogin();
    await loadStats();
  } catch (e) {
    if (e.message !== '未授权') toast('加载失败：' + e.message);
    showLogin();
  }
}

$('#login-password').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePwdModal(); });
boot();
