const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.argv[2]);
(async () => {
  const browser = await chromium.launch({ executablePath: process.argv[3], headless: true, args: ['--no-sandbox'] });
  const results = {};
  try {
    for (const width of [1280, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const page = await context.newPage();
      const errors = [], externalRequests = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const url = route.request().url();
        if (/^https?:/.test(url) && !url.startsWith('http://127.0.0.1:')) {
          externalRequests.push(url); return route.abort();
        }
        return route.continue();
      });
      await page.goto(process.argv[4]);
      await page.locator('[data-animation-player] video').waitFor();
      const host = page.locator('[data-animation-player]').first();
      await host.locator('[data-action="play"]').click();
      await page.waitForFunction(() => {
        const video = document.querySelector('[data-animation-player] video');
        return video.readyState >= 2 && video.currentTime > .2;
      });
      const result = await host.locator('video').evaluate(async video => {
        const advanced = video.currentTime > .2;
        video.pause();
        const target = Math.min(5, video.duration / 2);
        video.currentTime = target;
        await new Promise((resolve, reject) => {
          if (!video.seeking) return resolve();
          video.addEventListener('seeked', resolve, { once: true });
          setTimeout(() => reject(new Error('seek timeout')), 5000);
        });
        for (const track of video.textTracks) track.mode = 'showing';
        await new Promise(resolve => setTimeout(resolve, 200));
        const data = window.__DATA__;
        const payload = Object.values(data.animations).flat()[0];
        return { advanced, seekError: Math.abs(video.currentTime - target), videoWidth: video.videoWidth,
          renderedWidth: video.getBoundingClientRect().width,
          cues: Array.from(video.textTracks).reduce((sum, track) => sum + (track.cues ? track.cues.length : 0), 0),
          schema: payload.schema_version, sourceStatus: payload.status,
          previewOnly: data.preview_only && payload.preview_only,
          productionAccepted: data.production_accepted || payload.production_accepted,
          keyframes: payload.resource_manifest.filter(row => row.kind === 'keyframe').length,
          watermark: document.querySelector('.diagnostic-banner').textContent,
          noOverflow: document.documentElement.scrollWidth <= innerWidth + 1 };
      });
      assert.equal(result.schema, 'DiagnosticAnimationV1');
      assert.equal(result.sourceStatus, 'needs_review');
      assert.equal(result.previewOnly, true); assert.equal(result.productionAccepted, false);
      assert(result.advanced && result.seekError < .2 && result.cues > 0 && result.videoWidth > 0, JSON.stringify(result));
      assert.equal(result.keyframes, 6);
      assert.equal(result.noOverflow, true);
      assert.deepEqual(errors, []); assert.deepEqual(externalRequests, []);
      await page.screenshot({ path: process.argv[5] + '/diagnostic-animation-' + width + '.png', fullPage: true });
      results[width] = { ...result, errors, externalRequests };
      await context.close();
    }
  } finally { await browser.close(); }
  fs.writeFileSync(process.argv[5] + '/R5-animation-browser.json', JSON.stringify(results, null, 2));
  process.stdout.write(JSON.stringify(results));
})().catch(error => { console.error(error); process.exitCode = 1; });
