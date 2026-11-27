// 離線策略：
//  - App 外殼（html/圖示）：先回快取、背景更新（離線一定打得開）
//  - 資料 JSON：網路優先（5 秒逾時），失敗回退到最後一次快取；快取鍵忽略 ?t= 參數
//  - 預快取逐檔加入：個別檔案缺漏不會讓整個安裝失敗
// 改動任何靜態檔案或此策略時，請把 VER +1。
const VER = "tw-radar-v3";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg", "./icon-192.png", "./icon-512.png", "./apple-touch-icon.png", "./signals.json", "./stocks.json"];
const DATA_RE = /\/(signals|stocks)\.json$/;

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(VER)
      .then((c) => Promise.allSettled(SHELL.map((u) => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== VER).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (DATA_RE.test(url.pathname)) e.respondWith(networkFirstData(req, url));
  else if (req.mode === "navigate") e.respondWith(navigate(req));
  else e.respondWith(staleWhileRevalidate(req));
});

async function networkFirstData(req, url) {
  const cache = await caches.open(VER);
  const key = new Request(url.origin + url.pathname); // 去掉 ?t=時間戳，否則離線永遠對不到
  try {
    const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 5000));
    const res = await Promise.race([fetch(req), timeout]);
    if (!res.ok) throw new Error("bad status");
    cache.put(key, res.clone());
    return res;
  } catch (_) {
    const hit = await cache.match(key);
    return hit || new Response("null", { status: 503, headers: { "Content-Type": "application/json" } });
  }
}

async function navigate(req) {
  const cache = await caches.open(VER);
  const hit = (await cache.match("./index.html")) || (await cache.match("./"));
  const net = fetch(req).then((res) => {
    if (res.ok) cache.put("./index.html", res.clone());
    return res;
  }).catch(() => null);
  return hit || (await net) || new Response("離線中且尚未快取，請先連線開啟一次。", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(VER);
  const hit = await cache.match(req, { ignoreSearch: true });
  const net = fetch(req).then((res) => {
    if (res.ok) cache.put(req, res.clone());
    return res;
  }).catch(() => null);
  return hit || (await net) || Response.error();
}
