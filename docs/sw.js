const C='nhl-cesi-v2';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim());});
self.addEventListener('fetch',e=>{
  const r=e.request;
  if(r.method!=='GET'||new URL(r.url).origin!==location.origin)return;
  e.respondWith(fetch(r,{cache:'no-store'}).then(x=>{const c=x.clone();caches.open(C).then(y=>y.put(r,c));return x;}).catch(()=>caches.match(r).then(x=>x||Response.error())));
});
