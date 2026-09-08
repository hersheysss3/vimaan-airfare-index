const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

(async () => {
  const outDir = process.argv[2];
  const pdf = path.resolve(__dirname, '..', '..', 'VIMAAN_Presentation_Prep.pdf');
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 900, height: 1280, deviceScaleFactor: 1.6 });

  const b64 = fs.readFileSync(pdf).toString('base64');
  await page.setContent('<style>html,body{margin:0}canvas{display:block;width:100%}</style><div id="o"></div>');
  await page.addScriptTag({ url: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js' });

  const n = await page.evaluate(async (data) => {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
    const raw = atob(data);
    const arr = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    const doc = await pdfjsLib.getDocument({ data: arr }).promise;
    const host = document.getElementById('o');
    for (let i = 1; i <= doc.numPages; i++) {
      const p = await doc.getPage(i);
      const vp = p.getViewport({ scale: 1.5 });
      const c = document.createElement('canvas');
      c.id = 'pg' + i;
      c.width = vp.width; c.height = vp.height;
      host.appendChild(c);
      await p.render({ canvasContext: c.getContext('2d'), viewport: vp }).promise;
    }
    return doc.numPages;
  }, b64);

  console.log('pdf pages:', n);
  for (let i = 1; i <= n; i++) {
    const el = await page.$('#pg' + i);
    await el.screenshot({ path: path.join(outDir, 'pdf' + String(i).padStart(2, '0') + '.png') });
  }
  await browser.close();
})();
