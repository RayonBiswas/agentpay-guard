// Films the live app scene by scene: JPEG frames (q92) + timestamps -> concat list per scene.
// 1) DB_PATH=/tmp/v.db PAYPAL_MODE=sim AUTOPAY=off uvicorn app.main:app --port 8000
// 2) node scripts/video/record.js            (frames land in /tmp/video/frames/<scene>/list.txt)
// 3) per scene: ffmpeg -f concat -safe 0 -i list.txt -vf "scale=1920:1080,format=yuv420p,fps=30" \
//      -c:v libx264 -crf 18 -preset veryfast -pix_fmt yuv420p -r 30 <scene>.mp4
// 4) cards: screenshot scripts/video/cards/*.html at 1920x1080, then
//      ffmpeg -framerate 30 -i c1.jpg -vf format=yuv420p,loop=-1:1,trim=duration=6 <same encoder> c1.mp4
// 5) join with the concat demuxer and -c copy, add a silent track (anullsrc) with -c:v copy
const { chromium } = require('playwright');
const fs = require('fs');
const OUT = '/tmp/video/frames';
const ONLY = process.argv[2]; // optional: record a single scene, or "preview" for stills only

const CAPTION_CSS = `#vcap{position:fixed;left:0;right:0;bottom:0;height:92px;z-index:9999;display:flex;align-items:center;justify-content:center;
  background:#14213D;color:#fff;font:600 23px/1.35 "Public Sans",system-ui;padding:0 60px;text-align:center;
  border-top:3px solid #1F7A4D;transition:color .2s}
  #vcap.off{color:transparent}
  html{scroll-padding-bottom:110px} main{padding-bottom:120px}`;

async function setup(page) {
  await page.addStyleTag({ content: CAPTION_CSS });
  await page.evaluate(() => { const d = document.createElement('div'); d.id = 'vcap'; d.className = 'off'; document.body.appendChild(d); });
}
const cap = (page, text) => page.evaluate((t) => { const d = document.getElementById('vcap'); if (t) d.textContent = t; d.className = t ? '' : 'off'; }, text);

async function film(page, name, actions) {
  const dir = `${OUT}/${name}`; fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
  const frames = []; let rec = true; let i = 0;
  const loop = (async () => {
    while (rec) {
      const t = Date.now(); const f = `${dir}/${String(i++).padStart(5, '0')}.jpg`;
      await page.screenshot({ path: f, type: 'jpeg', quality: 92 });
      frames.push({ f, t });
    }
  })();
  await actions();
  rec = false; await loop;
  // concat list: each frame lasts until the next frame's timestamp
  let list = '';
  for (let k = 0; k < frames.length; k++) {
    const dur = k + 1 < frames.length ? (frames[k + 1].t - frames[k].t) / 1000 : 0.2;
    list += `file '${frames[k].f}'\nduration ${dur.toFixed(3)}\n`;
  }
  list += `file '${frames[frames.length - 1].f}'\n`;
  fs.writeFileSync(`${dir}/list.txt`, list);
  const total = (frames[frames.length - 1].t - frames[0].t) / 1000;
  console.log(`${name}: ${frames.length} frames, ${total.toFixed(1)}s, ${(frames.length / total).toFixed(1)} fps`);
}

(async () => {
  const b = await chromium.launch();
  const page = await b.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1.2 });
  await page.goto('http://localhost:8000/'); await page.waitForTimeout(800); await setup(page);
  const wait = (ms) => page.waitForTimeout(ms);
  const typeChat = async (t) => { await page.click('#chat-input'); await page.keyboard.type(t, { delay: 45 }); await wait(300); await page.keyboard.press('Enter'); };
  const show = (sel) => page.evaluate((q) => document.querySelector(q).scrollIntoView({ behavior: 'smooth', block: 'nearest' }), sel);
  const payDialog = async (slipSel) => { await show(`${slipSel} .paypal-slot button`); await wait(700); await page.click(`${slipSel} .paypal-slot button`); await wait(1600); await page.click('#sim-pay'); };

  const scenes = {
    rules: async () => {
      await page.click('#tab-rules'); await wait(400);
      await cap(page, 'I write my spending rules in plain English.');
      await page.fill('#rules-text', ''); await page.click('#rules-text');
      await page.keyboard.type('Max $50 per item and $100 per order. Weekly budget $150. Ask me before anything over $20. No gaming or gift cards. No purchases between 11pm and 7am.', { delay: 22 });
      await wait(700); await page.click('#compile-btn'); await wait(900);
      await cap(page, 'The AI turns them into exact limits. Changes are highlighted, and nothing is saved until I confirm.');
      await wait(1800); await show('#save-btn'); await wait(3400);
      await page.click('#save-btn'); await wait(1600);
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await wait(500);
      await cap(page, ''); await wait(400);
    },
    approve: async () => {
      await page.click('#tab-agent'); await page.evaluate(() => window.scrollTo(0, 0)); await wait(500);
      await cap(page, 'Now my shopping agent takes a request…');
      await typeChat('Buy me a USB-C cable and a notebook'); await wait(1500);
      await cap(page, '…and plain code, not the AI, checks every rule. Prices come from the catalogue, never from the AI.');
      await wait(5000);
      await cap(page, 'Approved purchases are paid through PayPal.');
      await payDialog('#slip-stage'); await wait(3200);
      await cap(page, ''); await wait(300);
    },
    block: async () => {
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await wait(400);
      await cap(page, 'What if the agent tries to overspend?');
      await typeChat('Buy noise cancelling headphones'); await wait(1600);
      await cap(page, 'Blocked. Every broken rule is listed, and the AI can’t argue its way past them.');
      await wait(5600); await cap(page, ''); await wait(300);
    },
    hold: async () => {
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' })); await wait(400);
      await cap(page, 'Above my ask-me limit, the purchase waits for me.');
      await typeChat('Get a desk lamp'); await wait(3800);
      await page.click('#tab-approvals'); await wait(900);
      await cap(page, 'Approving re-checks my budget first, so nothing can sneak past it.');
      await wait(1800);
      await page.click('#approvals [data-act="approve"]'); await wait(2200);
      await page.click('#tab-agent'); await page.evaluate(() => window.scrollTo(0, 0)); await wait(700);
      await cap(page, 'Then one tap in PayPal and it’s paid.');
      await payDialog('#slip-stage'); await wait(3000);
      await cap(page, ''); await wait(300);
    },
    ledger: async () => {
      await page.click('#tab-ledger'); await wait(700);
      await cap(page, 'Every decision is logged with its reason and PayPal order ID. The budget meter updates live.');
      await wait(4800);
      await cap(page, 'Refunds go back through PayPal and free up the budget.');
      await page.click('[data-refund]'); await wait(3200);
      await cap(page, ''); await wait(300);
    },
  };

  if (ONLY === 'preview') { await b.close(); return; }
  for (const [name, fn] of Object.entries(scenes)) {
    if (ONLY && ONLY !== name) { continue; }
    await film(page, name, fn);
  }
  await b.close();
})();
