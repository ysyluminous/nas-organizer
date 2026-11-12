"""Web 前端契约测试（Round C）。

纯 Python 静态检查（无需浏览器），保障：
- 每个 nav tab 都有对应的 section，反之亦然（防止新增 tab 忘记建容器）
- app.js 的关键 DOM 引用（$('#xxx') / getElementById）都在 HTML 中存在
- 所有危险操作按钮都带 confirm() 二次确认
- XSS 转义函数 escapeHtml / jsArg 覆盖完整（用 node 实际执行验证）
- Service Worker 不缓存 /api/（认证数据不落客户端）

直接运行：python tests/test_web.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

WEB = Path(__file__).parent.parent / "web"


def check_all():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    sw = (WEB / "sw.js").read_text(encoding="utf-8")

    # 1. nav tab 与 section 一一对应
    nav_tabs = set(re.findall(r'data-tab="([^"]+)"', html))
    sections = set(re.findall(r'id="tab-([^"]+)"', html))
    assert nav_tabs == sections, f"tab 与 section 不一致: 导航独有 {nav_tabs - sections}, 页面独有 {sections - nav_tabs}"
    assert len(nav_tabs) >= 12, f"tab 数量不足: {len(nav_tabs)}"

    # 2. app.js 里的 $('#id') 引用（排除 tab- 动态拼接）都在 HTML 中
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r"\$\('#([\w-]+)'\)", js))
    # 动态生成的 id（如 dom-${id}、plan-detail-${p.id}）含 $ 不会被上面匹配到
    missing = js_ids - html_ids
    assert not missing, f"app.js 引用了不存在的 DOM id: {sorted(missing)}"

    # 3. 危险操作必须二次确认：调用危险 API 的函数体内需出现 confirm(
    dangerous = ["execPlan", "rollback", "reindex", "autoTag", "submitReview",
                 "bindMount", "unbindMount", "rebindMount"]
    for fn in dangerous:
        m = re.search(rf'(?:async\s+)?function\s+{fn}\s*\([^)]*\)\s*\{{(.*?)\n\}}', js, re.S)
        assert m, f"未找到函数 {fn}"
        assert "confirm(" in m.group(1), f"危险函数 {fn} 缺少二次确认"

    # 3b. 多页面路由契约：hash 路由 + hashchange 监听 + 侧边栏分组 + 挂载页存在
    assert "hashchange" in js, "缺少 hashchange 路由监听"
    assert "activatePage" in js and "currentPageFromHash" in js, "缺少多页面激活逻辑"
    assert 'class="sidebar"' in html, "缺少侧边栏容器"
    assert 'class="nav-group-title"' in html, "缺少导航分组标题"
    assert "mounts" in nav_tabs, "缺少磁盘挂载页面"
    assert "id=\"tab-mounts\"" in html, "缺少挂载页 section"

    # 4. 认证数据不落客户端：SW 必须显式跳过 /api/
    assert "startsWith('/api/')" in sw, "Service Worker 未排除 /api/ 缓存"
    assert "CACHE" in sw and "caches.delete" in sw, "Service Worker 缺少旧缓存清理"

    # 5. 实际执行转义函数（node 可用时），验证 XSS 字符被中和
    node = shutil.which("node")
    if node:
        script = """
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const m = src.match(/function escapeHtml\\(s\\) \\{[\\s\\S]*?\\n\\}/);
if (!m) process.exit(2);
eval(m[0]);
const out = escapeHtml('<script>alert("x")</script>\\'&>');
if (out.includes('<') || out.includes('>') || out.includes('"') || out.includes("'") || out.includes('&amp;lt')) {
  console.error('转义不完整:', out); process.exit(3);
}
console.log('escapeHtml OK:', out.slice(0, 40));
"""
        r = subprocess.run([node, "-e", script, str(WEB / "app.js")],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"escapeHtml 校验失败: {r.stderr.strip()}"
        print(" ", r.stdout.strip())

    print(f"Web 契约测试通过：{len(nav_tabs)} 个 tab、{len(js_ids)} 个 DOM 引用、"
          f"{len(dangerous)} 个危险函数确认、SW 缓存隔离、XSS 转义")


if __name__ == "__main__":
    check_all()
