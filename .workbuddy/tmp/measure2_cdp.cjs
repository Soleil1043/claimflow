// CDP 量测 v2：真 420 视口(Emulation) + CSSOM 全量映射 + 截图
// 用法: node measure2_cdp.cjs <url> <width> <emulate:0|1> [shot.png]
'use strict';
const { spawn } = require('child_process');
const os = require('os');
const fs = require('fs');

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const URL_ARG = process.argv[2] || 'http://127.0.0.1:7860';
const WIDTH = parseInt(process.argv[3] || '420', 10);
const EMULATE = process.argv[4] !== '0';
const SHOT = process.argv[5] || '';
const CDP_PORT = 9224;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const profile = os.tmpdir() + '/cf-cdp-profile-' + Date.now();
  const chrome = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${CDP_PORT}`,
    '--window-size=1024,900',
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

    await send('Page.enable');
    if (EMULATE) {
      await send('Emulation.setDeviceMetricsOverride', {
        width: WIDTH, height: 900, deviceScaleFactor: 1, mobile: true,
      });
    }
    await send('Page.navigate', { url: URL_ARG });
    await sleep(8000);

    const expr = `(() => {
      const W = ${WIDTH};
      const out = {};
      out.viewport = { innerWidth, docScrollW: document.documentElement.scrollWidth, docClientW: document.documentElement.clientWidth };
      const sub = document.querySelector('.cf-subtitle');
      out.canarySubtitleDisplay = sub ? getComputedStyle(sub).display : 'no-el';
      // 内联样式表（APP_CSS 处理后）全量选择器映射
      out.inlineRules = [];
      for (const sheet of document.styleSheets) {
        if (sheet.href) continue;
        const walk = (rules, media) => {
          for (const r of rules) {
            if (r.media) { walk(r.cssRules, r.media.mediaText); continue; }
            if (r.selectorText) out.inlineRules.push({ sel: r.selectorText, media: media || '' });
          }
        };
        try { walk(sheet.cssRules, ''); } catch {}
      }
      // 关键计算样式
      const pick = (sel, props) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const cs = getComputedStyle(el);
        const o = { found: true };
        for (const p of props) o[p] = cs[p];
        return o;
      };
      const root = document.querySelector('.gradio-container');
      out.checks = {
        rootBgImage: root ? getComputedStyle(root).backgroundImage.slice(0, 80) : 'no-root',
        rootBgColor: root ? getComputedStyle(root).backgroundColor : '',
        rootFont: root ? getComputedStyle(root).fontFamily.slice(0, 60) : '',
        chipBtn: pick('.cf-chips button', ['borderRadius', 'padding', 'background']),
        chipGallery: pick('.cf-chips .gallery-item', ['borderRadius']),
        composerRow: pick('.cf-composer .row', ['alignItems', 'flexWrap']),
        nestedGradioContainerInContain: document.querySelectorAll('.contain .gradio-container').length,
      };
      // header 子元素宽度（找出 min-content 元凶）
      const hdr = document.querySelector('.cf-header');
      if (hdr) {
        const hr = hdr.getBoundingClientRect();
        out.header = {
          w: Math.round(hr.width), sw: hdr.scrollWidth,
          kids: [...hdr.querySelectorAll('*')].filter(e => e.children.length === 0).map(e => {
            const r = e.getBoundingClientRect();
            return { cls: (e.className || '').toString().slice(0, 40), tag: e.tagName, w: Math.round(r.width), right: Math.round(r.right) };
          }),
        };
      }
      // 溢出元素（叶子优先）
      const offenders = [];
      const depth = (el) => { let d = 0, n = el; while (n.parentElement) { d++; n = n.parentElement; } return d; };
      document.querySelectorAll('body *').forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.right > W + 1 && r.width > 0) {
          offenders.push({ depth: depth(el), tag: el.tagName, cls: ((el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || '').toString().slice(0, 70), w: Math.round(r.width), right: Math.round(r.right) });
        }
      });
      offenders.sort((a, b) => b.depth - a.depth || b.w - a.w);
      out.offenders = offenders.slice(0, 20);
      // rows 概览
      out.rows = [];
      document.querySelectorAll('.row').forEach((row) => {
        const rr = row.getBoundingClientRect();
        if (rr.width === 0) return;
        const cs = getComputedStyle(row);
        out.rows.push({ cls: (row.className || '').toString().slice(0, 50), w: Math.round(rr.width), h: Math.round(rr.height), wrap: cs.flexWrap, kids: [...row.children].filter(k => getComputedStyle(k).display !== 'none').map(k => Math.round(k.getBoundingClientRect().width)) });
      });
      return JSON.stringify(out);
    })()`;

    const resp = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    if (resp.result && resp.result.exceptionDetails) {
      console.error('evaluate exception:', JSON.stringify(resp.result.exceptionDetails).slice(0, 1500));
    } else if (resp.result && resp.result.result && resp.result.result.value) {
      console.log(JSON.stringify(JSON.parse(resp.result.result.value), null, 1));
    } else {
      console.error('unexpected:', JSON.stringify(resp).slice(0, 1000));
    }

    if (SHOT) {
      const shot = await send('Page.captureScreenshot', { format: 'png' });
      if (shot.result && shot.result.data) {
        fs.writeFileSync(SHOT, Buffer.from(shot.result.data, 'base64'));
        console.error('shot saved: ' + SHOT);
      }
    }
    ws.close();
  } finally {
    chrome.kill();
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
  }
}

main().catch((e) => { console.error(e && e.message ? e.message : e); process.exit(1); });
