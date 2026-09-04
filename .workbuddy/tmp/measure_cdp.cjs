// 零依赖 CDP 量测：Node 22 原生 WebSocket + 系统 Chrome headless
// 用法: node measure_cdp.cjs <url> [width]
'use strict';
const { spawn } = require('child_process');
const os = require('os');
const fs = require('fs');

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const URL_ARG = process.argv[2] || 'http://127.0.0.1:7860';
const WIDTH = parseInt(process.argv[3] || '420', 10);
const CDP_PORT = 9223;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  if (!fs.existsSync(CHROME)) {
    console.error('chrome not found: ' + CHROME);
    process.exit(2);
  }
  const profile = os.tmpdir() + '/cf-cdp-profile-' + Date.now();
  const chrome = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${CDP_PORT}`,
    `--window-size=${WIDTH},900`,
    '--user-data-dir=' + profile,
    '--no-first-run',
    '--disable-gpu',
    '--disable-extensions',
    'about:blank',
  ], { stdio: 'ignore' });

  try {
    // 等 devtools 端口就绪
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
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = (e) => rej(new Error('ws error')); });

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

    await send('Page.enable');
    await send('Page.navigate', { url: URL_ARG });
    await sleep(8000); // 等 Gradio 渲染

    const expr = `(() => {
      const W = ${WIDTH};
      const out = {};
      out.url = location.href;
      out.viewport = {
        innerWidth, innerHeight,
        docScrollW: document.documentElement.scrollWidth,
        docClientW: document.documentElement.clientWidth,
        bodyScrollW: document.body.scrollWidth,
      };
      // 媒体查询生效金丝雀：副标题应 display:none
      const sub = document.querySelector('.cf-subtitle');
      out.canarySubtitleDisplay = sub ? getComputedStyle(sub).display : 'no-subtitle-el';
      // CSSOM：检查 640px 媒体块内规则是否解析成功（字符串存在不代表语法有效）
      out.media640Rules = [];
      for (const sheet of document.styleSheets) {
        let rules; try { rules = sheet.cssRules; } catch { continue; }
        for (const r of rules) {
          if (r.media && (r.media.mediaText || '').includes('640')) {
            const inner = [];
            for (const ir of r.cssRules) inner.push(ir.selectorText || ir.cssText.slice(0, 60));
            out.media640Rules.push({ href: (sheet.href || 'inline').slice(-40), inner });
          }
        }
      }
      // 溢出元素：按 DOM 深度降序（叶子元凶优先）
      const offenders = [];
      const depth = (el) => { let d = 0, n = el; while (n.parentElement) { d++; n = n.parentElement; } return d; };
      document.querySelectorAll('body *').forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.right > W + 1 && r.width > 0) {
          offenders.push({
            depth: depth(el), tag: el.tagName,
            cls: ((el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || '').toString().slice(0, 90),
            w: Math.round(r.width), left: Math.round(r.left), right: Math.round(r.right),
            sw: el.scrollWidth,
            text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 50),
          });
        }
      });
      offenders.sort((a, b) => b.depth - a.depth || b.w - a.w);
      out.offenders = offenders.slice(0, 40);
      // 所有 .row 的布局状态
      out.rows = [];
      document.querySelectorAll('.row').forEach((row) => {
        const cs = getComputedStyle(row);
        const rr = row.getBoundingClientRect();
        if (rr.width === 0 && rr.height === 0) return;
        out.rows.push({
          cls: (row.className || '').toString().slice(0, 90),
          w: Math.round(rr.width), h: Math.round(rr.height),
          display: cs.display, flexWrap: cs.flexWrap, overflow: cs.overflowX,
          kids: [...row.children].map((k) => {
            const kc = getComputedStyle(k); const kr = k.getBoundingClientRect();
            return { cls: (k.className || '').toString().slice(0, 60), w: Math.round(kr.width), h: Math.round(kr.height), minW: kc.minWidth, display: kc.display, inlineMinW: k.getAttribute('style') || '' };
          }),
        });
      });
      return JSON.stringify(out);
    })()`;

    const resp = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    if (resp.result && resp.result.exceptionDetails) {
      console.error('evaluate exception:', JSON.stringify(resp.result.exceptionDetails, null, 1));
    } else if (resp.result && resp.result.result && resp.result.result.value) {
      console.log(JSON.stringify(JSON.parse(resp.result.result.value), null, 1));
    } else {
      console.error('unexpected response:', JSON.stringify(resp).slice(0, 2000));
    }
    ws.close();
  } finally {
    chrome.kill();
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
  }
}

main().catch((e) => { console.error(e && e.message ? e.message : e); process.exit(1); });
