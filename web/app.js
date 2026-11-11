const $ = (s) => document.querySelector(s);
const api = async (path, options) => {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
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
  setTimeout(() => t.style.display = 'none', 2500);
};

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
      <span class="bar-label">${d[labelKey] || '未分类'}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${d[valueKey]/max*100}%"></div></div>
      <span class="bar-num">${d[valueKey].toLocaleString()}</span>
    </div>`).join('');
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
      ${g.files.map(f => `<tr><td class="path">${f.path}</td><td>${fmtBytes(f.size)}</td>
        <td>${new Date(f.mtime*1000).toLocaleDateString()}</td></tr>`).join('')}
      </table>
    </div>`).join('') : '<div class="panel hint">暂无重复文件，或尚未运行查重。</div>';
}

async function loadPlans() {
  const plans = await api('/api/plans');
  $('#plan-list').innerHTML = plans.length ? plans.map(p => `
    <div class="panel">
      <h3>#${p.id} ${p.name} <span class="badge">${p.status}</span> · ${p.item_count} 项</h3>
      <div class="btn-row">
        <button onclick="viewPlan(${p.id})">查看明细</button>
        <button onclick="execPlan(${p.id})">执行</button>
      </div>
    </div>`).join('') : '<div class="panel hint">暂无迁移计划。</div>';
}

async function viewPlan(id) {
  const p = await api('/api/plans/' + id);
  const rows = p.items.slice(0, 100).map(i => `<tr>
    <td>${i.id}</td><td class="path">${i.src}</td><td class="path">${i.dst || '-'}</td>
    <td>${i.reason}</td><td><span class="badge">${i.status}</span></td></tr>`).join('');
  toast(`计划 #${id} 共 ${p.items.length} 项，明细见控制台日志`);
  console.table(p.items.slice(0, 50));
}

async function createPlan() {
  const r = await api('/api/plans', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: 'web-plan-' + Date.now(), limit: 100})
  });
  toast(`已生成计划 #${r.id}，共 ${r.item_count} 项`);
  loadPlans();
}

async function execPlan(id) {
  if (!confirm(`确认执行计划 #${id}？此操作会移动文件，可在操作页回滚。`)) return;
  const r = await api(`/api/plans/${id}/execute`, {method: 'POST'});
  toast(`执行完成：成功 ${r.done}，失败 ${r.failed}`);
  loadPlans();
}

async function post(path, label) {
  toast(`${label}中...`);
  try { const r = await api(path, {method: 'POST'}); toast(`${label}完成: ${JSON.stringify(r).slice(0,80)}`); loadStats(); }
  catch (e) { toast(`${label}失败: ${e.message}`); }
}

async function rollback() {
  const limit = parseInt($('#rollback-limit').value) || 10;
  if (!confirm(`确认回滚最近 ${limit} 条操作？`)) return;
  const r = await api('/api/rollback?limit=' + limit, {method: 'POST'});
  toast(`回滚完成：成功 ${r.done}，失败 ${r.failed}`);
}

loadStats();
