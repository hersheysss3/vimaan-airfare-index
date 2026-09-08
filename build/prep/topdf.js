const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--font-render-hinting=none', '--disable-gpu']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1240, height: 1754 });

  const htmlPath = path.resolve(__dirname, 'prep.html');
  const url = 'file:///' + htmlPath.split(path.sep).join('/');
  console.log('loading', url);

  await page.goto(url, { waitUntil: 'networkidle0', timeout: 90000 });
  await page.emulateMediaType('print');
  await page.evaluate(() => document.fonts.ready);
  await new Promise(r => setTimeout(r, 4000));

  const out = path.resolve(__dirname, '..', '..', 'VIMAAN_Presentation_Prep.pdf');
  await page.pdf({
    path: out,
    format: 'A4',
    printBackground: true,
    preferCSSPageSize: true,
    tagged: true
  });

  console.log('PDF:', out, (fs.statSync(out).size / 1048576).toFixed(2), 'MB');
  await browser.close();
})();
