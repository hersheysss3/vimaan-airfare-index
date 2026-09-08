const puppeteer = require('puppeteer');
const path = require('path');

(async () => {
  const outDir = process.argv[2];
  const scheme = process.argv[3] || 'dark';
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu', '--font-render-hinting=none']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1560, height: 980, deviceScaleFactor: 1.25 });
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: scheme }]);

  const f = path.resolve(__dirname, 'vimaan-console.html');
  await page.goto('file:///' + f.split(path.sep).join('/'), { waitUntil: 'networkidle0', timeout: 60000 });
  await page.evaluate(() => document.fonts.ready);
  await new Promise(r => setTimeout(r, 1800));

  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });

  const views = ['v-index', 'v-surface', 'v-method', 'v-quality'];
  for (const v of views) {
    await page.evaluate(id => {
      document.querySelector(`.tab[data-v="${id}"]`).click();
    }, v);
    await new Promise(r => setTimeout(r, 900));
    await page.screenshot({ path: path.join(outDir, `${scheme}-${v}.png`), fullPage: true });
  }

  // sanity probe: did every chart actually draw?
  const probe = await page.evaluate(() => {
    const ids = ['chIndex','chWaterfall','chSurface','chLead','chMethod','chCover','chDiv'];
    const out = {};
    ids.forEach(i => {
      const n = document.getElementById(i);
      out[i] = n ? (n.querySelector('svg') ? n.querySelectorAll('svg *').length : 'NO SVG') : 'MISSING';
    });
    out._logRows = document.querySelectorAll('#log div').length;
    out._routeRows = document.querySelectorAll('#routeTbl tbody tr').length;
    out._bodyScrollW = document.body.scrollWidth;
    out._winW = window.innerWidth;
    return out;
  });
  console.log(scheme, JSON.stringify(probe));
  if (errs.length) console.log('JS ERRORS:', errs.slice(0, 6));
  await browser.close();
})();
