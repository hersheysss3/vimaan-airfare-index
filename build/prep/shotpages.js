const puppeteer = require('puppeteer');
const path = require('path');

(async () => {
  const outDir = process.argv[2];
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--font-render-hinting=none', '--disable-gpu']
  });
  const page = await browser.newPage();
  // A4 at 96dpi = 794 x 1123 css px
  await page.setViewport({ width: 703, height: 1024, deviceScaleFactor: 1.7 });

  const htmlPath = path.resolve(__dirname, 'prep.html');
  const url = 'file:///' + htmlPath.split(path.sep).join('/');
  await page.goto(url, { waitUntil: 'networkidle0', timeout: 90000 });
  await page.emulateMediaType('print');
  await page.evaluate(() => document.fonts.ready);
  await new Promise(r => setTimeout(r, 3000));

  // report each page section's rendered height vs A4 printable height
  const info = await page.evaluate(() => {
    const mm = 3.7795;
    const printable = (297 - 26) * mm; // A4 minus 12mm top + 14mm bottom
    return Array.from(document.querySelectorAll('.page')).map((el, i) => ({
      i: i + 1,
      h: Math.round(el.getBoundingClientRect().height),
      limit: Math.round(printable),
      over: el.getBoundingClientRect().height > printable
    }));
  });
  console.log(JSON.stringify(info, null, 1));

  const els = await page.$$('.page');
  for (let i = 0; i < els.length; i++) {
    await els[i].screenshot({ path: path.join(outDir, 'p' + (i + 1) + '.png') });
  }
  console.log('shot', els.length, 'pages');
  await browser.close();
})();
