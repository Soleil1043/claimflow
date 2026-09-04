// T063 chatui 交互冒烟：真实浏览器驱动（发消息 → 等回复 → 展开富数据 → 截图）
// 用法: node chat_smoke_cdp.cjs
'use strict';
const { spawn } = require('child_process');
const os = require('os');
const fs = require('fs');
const path = require('path');

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const APP_URL = 'http://localhost:3000';
const CDP_PORT = 9225;
const ROOT = 'D:/Code/PythonProjects/claimflow';
const QUESTION = '保单 POL-2025-0001 住院花了15800元能赔多少？';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = os.tmpdir() + '/cf-cdp-profile-' + Date.now();
  const chrome = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${CDP_PORT}`,
    '--window-size=1440,900',
    '--user-data-dir=' + profile,
    '--no-first-run', '--disable-gpu', '--disable-extensions',
    'about:blank',
  ], { stdio: 'ignore' });

  try {
    let page = null;
    for (let i = 0; i < 60; i++) {
      await sleep(500);
      try {
        const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
        const targets = await res.json();
        page = targets.find((t) => t.type === 'page');
        if (page) break;
      } catch {}
    }
    if (!page) throw new Error('devtools page target not found');

    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws error')); });

    let mid = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++mid;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });

    const evalAsync = async (expression) => {
      const r = await send('Runtime.evaluate', {
        expression: `(async () => { ${expression} })()`,
        awaitPromise: true, returnByValue: true,
      });
      if (r.result?.exceptionDetails) throw new Error('page eval: ' + JSON.stringify(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails).slice(0, 500));
      return r.result?.result?.value;
    };

    const shot = async (file) => {
      const r = await send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(file, Buffer.from(r.result.data, 'base64'));
      console.log('shot:', file);
    };

    await send('Page.enable');
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: APP_URL });

    // 等 React 挂载（textarea 出现 + 健康 pill 完成探测）
    await evalAsync(`
      await new Promise((resolve, reject) => {
        const t0 = Date.now();
        const timer = setInterval(() => {
          const ta = document.querySelector('textarea');
          const pill = document.querySelector('.cf-pill');
          if (ta && pill && !/检测中/.test(pill.textContent)) return resolve();
          if (Date.now() - t0 > 30000) return reject(new Error('hydrate timeout'));
        }, 300);
      });
    `);
    const health = await evalAsync(`return document.querySelector('.cf-pill').textContent.trim();`);
    console.log('health pill:', health);
    await shot(path.join(ROOT, 'docs/diagrams/t063_chat_next_home.png'));

    // 填入问题 → 点发送（LLM 上游偶发 500 时自动重试一次）
    let replyInfo = null;
    for (let attempt = 1; attempt <= 2; attempt++) {
      await evalAsync(`
        const ta = document.querySelector('textarea');
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
        setter.call(ta, ${JSON.stringify(QUESTION)});
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        await new Promise(r => setTimeout(r, 200));
        const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '发送');
        btn.click();
      `);
      // 等回复（typing 消失 + bot 气泡 >= 2）
      replyInfo = await evalAsync(`
        return await new Promise((resolve) => {
          const t0 = Date.now();
          const timer = setInterval(() => {
            const bots = document.querySelectorAll('.cf-bubble.bot');
            const typing = document.querySelector('.cf-typing');
            const last = bots[bots.length - 1];
            if (bots.length >= 2 && !typing) {
              return resolve({ count: bots.length, text: last.innerText.slice(0, 400), isError: /⚠️/.test(last.innerText) });
            }
            if (Date.now() - t0 > 150000) {
              return resolve({ count: bots.length, text: 'TIMEOUT', isError: true });
            }
          }, 500);
        });
      `);
      console.log(`attempt ${attempt}:`, JSON.stringify({ isError: replyInfo.isError, text: replyInfo.text.slice(0, 80) }));
      if (!replyInfo.isError) break;
      if (attempt === 1) {
        await evalAsync(`
          const reset = [...document.querySelectorAll('button')].find(b => b.textContent.includes('新会话'));
          reset.click();
          await new Promise(r => setTimeout(r, 400));
        `);
      }
    }
    console.log('reply:', JSON.stringify({ count: replyInfo.count, isError: replyInfo.isError, text: replyInfo.text.slice(0, 120) }));

    // 展开处理过程 / 工具调用折叠区
    await evalAsync(`
      for (const t of [...document.querySelectorAll('.cf-toggle')]) { t.click(); await new Promise(r => setTimeout(r, 150)); }
      await new Promise(r => setTimeout(r, 400));
    `);
    await shot(path.join(ROOT, 'docs/diagrams/t063_chat_next_conversation.png'));

    const toggles = await evalAsync(`
      return [...document.querySelectorAll('.cf-toggle')].map(t => t.textContent.trim())
    `);
    const pills = await evalAsync(`
      return [...document.querySelectorAll('.cf-bubble .cf-pill')].map(p => p.textContent.trim())
    `);
    console.log('result:', JSON.stringify({ toggles, pills, isError: replyInfo.isError }, null, 2));
  } finally {
    try { chrome.kill(); } catch {}
  }
}

main().then(
  () => process.exit(0),
  (e) => { console.error('FAIL:', e.message); process.exit(1); }
);
