/* NAS Organizer Service Worker（D7 PWA）
 * 只缓存静态壳资源（HTML/CSS/JS/图标），**不缓存任何 /api/ 响应**——
 * 避免把带认证的文件索引/统计缓存到客户端造成信息泄露。
 * 网络优先：在线时总拿最新的静态资源，离线时用缓存壳。
 */
const CACHE = 'nas-organizer-shell-v1';
const SHELL = ['/', '/static/style.css', '/static/app.js', '/static/icon.svg', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  // 绝不缓存 API：认证态与数据必须每次实时获取
  if (url.pathname.startsWith('/api/')) return;
  if (req.method !== 'GET') return;
  event.respondWith(
    fetch(req).then((resp) => {
      const copy = resp.clone();
      caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      return resp;
    }).catch(() => caches.match(req).then((hit) => hit || caches.match('/')))
  );
});
