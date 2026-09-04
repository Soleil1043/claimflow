// 定时探测：页面上下文 raw fetch 发中文理赔问题，打印状态/耗时/响应体
'use strict';
const { spawn } = require('child_process');
const os = require('os');

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CDP_PORT = 9226;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = os.tmpdir() + '/cf-cdp-profile-' + Date.now();
  const chrome = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${CDP_PORT}`,
    '--window-size=800,600', '--user-data-dir=' + profile,
    '--no-first-run', '--disable-gpu', 'about:blank',
  ], { stdio: 'ignore' });
  try {
    let page = null;
    for (let i = 0; i < 60; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        page = (await res.json()).find((t) => t.type === 'page');
        if (page) break;
      } catch {}
    }
    if (!page) throw new Error('no page target');
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws')); });
    let mid = 0; const pending = new Map();
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
    const send = (method, params = {}) => new Promise((res) => { const id = ++mid; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    const evalAsync = async (expression) => {
      const r = await send('Runtime.evaluate', { expression: `(async () => { ${expression} })()`, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails).slice(0, 400));
      return r.result?.result?.value;
    };
    await send('Page.enable');
    await send('Page.navigate', { url: 'http://localhost:3000' });
    await evalAsync(`await new Promise(r => setTimeout(r, 3000)); return true;`);
    const out = await evalAsync(`
      const t0 = Date.now();
      const cid = await (await fetch('/api/v1/conversations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: 'probe-cdp' }) })).json();
      const resp = await fetch('/api/v1/conversations/' + cid.conversation_id + '/messages', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: '保单 POL-2025-0001 住院花了15800元能赔多少？' }),
      });
      const text = await resp.text();
      return JSON.stringify({ status: resp.status, elapsed: ((Date.now() - t0) / 1000).toFixed(1) + 's', body: text.slice(0, 300) });
    `);
    console.log(out);
  } finally { try { chrome.kill(); } catch {} }
}
main().then(() => process.exit(0), (e) => { console.error('FAIL:', e.message); process.exit(1); });
