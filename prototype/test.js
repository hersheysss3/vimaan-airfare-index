const puppeteer = require('puppeteer');
const path = require('path');

(async () => {
  const outDir = process.argv[2];
  const scheme = process.argv[3] || 'dark';
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox','--disable-setuid-sandbox','--font-render-hinting=none','--use-gl=swiftshader','--enable-unsafe-swiftshader']
  });
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text()); });

  await page.setViewport({ width: 1560, height: 980, deviceScaleFactor: 1.25 });
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: scheme }]);
  const f = path.resolve(__dirname, 'vimaan-console.html');
  await page.goto('file:///' + f.split(path.sep).join('/'), { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.evaluate(() => document.fonts.ready);
  await new Promise(r => setTimeout(r, 1500));

  const shot = async n => { await page.screenshot({ path: path.join(outDir, `${scheme}-${n}.png`), fullPage: true }); };

  for (const v of ['v-index', 'v-surface', 'v-method', 'v-quality']) {
    await page.evaluate(id => document.querySelector(`.tab[data-v="${id}"]`).click(), v);
    await new Promise(r => setTimeout(r, 800));
    await shot(v);
  }

  const probe = await page.evaluate(() => {
    const ids = ['chIndex','chWaterfall','chSurface','chLead','chMethod','chCover'];
    const o = {};
    ids.forEach(i => { const n = document.getElementById(i);
      o[i] = n ? (n.querySelector('svg') ? n.querySelectorAll('svg *').length : 'NOSVG') : 'MISSING'; });
    o._overflow = document.body.scrollWidth > window.innerWidth ? 'YES' : 'no';
    o._glossTerms = document.querySelectorAll('.gl').length;
    o._webgl = (typeof HAS_GL !== 'undefined') ? HAS_GL : 'undef';
    o._globeCanvas = !!document.querySelector('#globeWrap canvas');
    o._three = (typeof THREE !== 'undefined');
    return o;
  });
  console.log(`[${scheme}] charts`, JSON.stringify(probe));

  if (scheme === 'dark') {
    // ---- story mode walk
    await page.evaluate(() => document.getElementById('storyStart').click());
    await new Promise(r => setTimeout(r, 700));
    for (let i = 0; i < 7; i++) {
      const st = await page.evaluate(() => ({
        step: document.getElementById('storyStep').textContent,
        view: [...document.querySelectorAll('.view')].find(v => !v.hidden).id,
        spot: document.querySelector('.spot') ? document.querySelector('.spot').id : 'NONE',
        txt: document.getElementById('storyTxt').textContent.slice(0, 52)
      }));
      console.log(`  ${st.step.padEnd(13)} ${st.view.padEnd(11)} spot=${st.spot.padEnd(12)} ${st.txt}…`);
      if (i === 3) await page.screenshot({ path: path.join(outDir, 'story-onecell.png'), fullPage: false });
      if (i < 6) { await page.evaluate(() => document.getElementById('storyNext').click()); await new Promise(r => setTimeout(r, 700)); }
    }
    await page.screenshot({ path: path.join(outDir, 'story-last.png'), fullPage: false });
    await page.evaluate(() => document.getElementById('storyExit').click());
    await new Promise(r => setTimeout(r, 300));
    const closed = await page.evaluate(() => ({
      bar: document.getElementById('storyBar').classList.contains('on'),
      spots: document.querySelectorAll('.spot').length
    }));
    console.log('  exit ->', JSON.stringify(closed));

    await page.evaluate(() => document.querySelector('.tab[data-v="v-index"]').click());
    await new Promise(r => setTimeout(r, 400));

    // ---- glossary
    await page.evaluate(() => document.querySelector('.gl').dispatchEvent(new MouseEvent('mouseover', { bubbles: true })));
    await new Promise(r => setTimeout(r, 200));
    const gl = await page.evaluate(() => {
      const p = document.getElementById('glossPop');
      return { on: p.classList.contains('on'), txt: p.textContent.slice(0, 40) };
    });
    console.log('  glossary:', JSON.stringify(gl));

    // ---- mobile
    await page.setViewport({ width: 375, height: 780, deviceScaleFactor: 2 });
    await new Promise(r => setTimeout(r, 900));
    const mob = await page.evaluate(() => ({ overflow: document.body.scrollWidth > 375 ? 'YES ' + document.body.scrollWidth : 'no' }));
    console.log('  mobile 375px overflow:', mob.overflow);
    await page.screenshot({ path: path.join(outDir, 'mobile.png'), fullPage: false });
  }

  console.log(errs.length ? 'ERRORS: ' + errs.slice(0, 6).join(' | ') : 'no JS errors');
  await browser.close();
})();
