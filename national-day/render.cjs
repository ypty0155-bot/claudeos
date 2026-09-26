#!/usr/bin/env node
/* Renders index.html to an MP4.
   The page draws every frame itself (index.html?render exposes window.film), the soundtrack is
   rendered with an OfflineAudioContext inside the page, and ffmpeg encodes the result.

   node render.cjs [out.mp4] [--fps 30] [--workers 3] [--pcm audio.pcm] [--fonts-via-curl]

   Needs Playwright (with Chromium) and an ffmpeg that has libx264; set FFMPEG to its path if it
   is not on PATH. --fonts-via-curl fetches Google Fonts with curl instead of the browser, for
   machines behind a TLS-inspecting proxy that Chromium does not trust. */
'use strict';
const fs = require('fs'), os = require('os'), path = require('path'), crypto = require('crypto');
const { spawn, spawnSync, execFileSync } = require('child_process');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');

const args = process.argv.slice(2);
const flag = (name, def) => { const i = args.indexOf(name); if (i < 0) return def; const v = args[i + 1]; args.splice(i, 2); return v; };
const bool = name => { const i = args.indexOf(name); if (i < 0) return false; args.splice(i, 1); return true; };
const FPS = Number(flag('--fps', 30)), WORKERS = Number(flag('--workers', 3)), PCM_IN = flag('--pcm', null), VIA_CURL = bool('--fonts-via-curl');
const OUT = path.resolve(args[0] || path.join(__dirname, 'national-day-2026.mp4'));
const FFMPEG = process.env.FFMPEG || 'ffmpeg';
const PAGE = 'file://' + path.join(__dirname, 'index.html') + '?render';
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'national-day-'));

function run(cmd, argv, input) {
  return new Promise((res, rej) => {
    const p = spawn(cmd, argv, { stdio: [input ? 'pipe' : 'ignore', 'ignore', 'inherit'] });
    p.on('error', rej); p.on('close', c => c === 0 ? res() : rej(new Error(`${cmd} exited ${c}`)));
    if (input) input(p.stdin);
  });
}
async function routeFontsViaCurl(page) {
  const cache = path.join(TMP, 'fonts'); fs.mkdirSync(cache, { recursive: true });
  await page.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//, async route => {
    const url = route.request().url(), ua = route.request().headers()['user-agent'] || '';
    const f = path.join(cache, crypto.createHash('sha1').update(url + ua).digest('hex'));
    if (!fs.existsSync(f)) fs.writeFileSync(f + '.type', execFileSync('curl', ['-sS', '-f', '--retry', '3', '-A', ua, '-o', f, '-w', '%{content_type}', url]));
    await route.fulfill({ status: 200, contentType: fs.readFileSync(f + '.type', 'utf8'), body: fs.readFileSync(f), headers: { 'access-control-allow-origin': '*' } });
  });
}
async function openFilm(browser) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  if (VIA_CURL) await routeFontsViaCurl(page);
  page.on('pageerror', e => { console.error('page error:', e.message); process.exitCode = 1; });
  await page.goto(PAGE);
  await page.waitForFunction(() => window.film && window.film.ready, null, { timeout: 120000 });
  return page;
}

(async () => {
  const launch = process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY } } : {};
  const browser = await chromium.launch(launch);
  const pages = await Promise.all(Array.from({ length: WORKERS }, () => openFilm(browser)));
  const { DUR } = await pages[0].evaluate(() => ({ DUR: window.film.DUR }));
  const total = Math.round(DUR * FPS);

  // soundtrack
  let pcm = PCM_IN;
  if (!pcm) {
    console.log('rendering soundtrack…');
    const info = await pages[0].evaluate(() => window.film.audio());
    pcm = path.join(TMP, 'audio.pcm');
    const fd = fs.openSync(pcm, 'w'), CH = 4 << 20;
    for (let off = 0; off < info.bytes; off += CH) fs.writeSync(fd, Buffer.from(await pages[0].evaluate(([o, n]) => window.film.pcmChunk(o, n), [off, CH]), 'base64'));
    fs.closeSync(fd);
    console.log(`soundtrack: ${(info.frames / info.sr).toFixed(1)} s, peak ${info.peak.toFixed(3)}`);
  }

  // frames, split across workers into H.264 segments
  const per = Math.ceil(total / WORKERS), t0 = Date.now(); let done = 0;
  const segs = await Promise.all(pages.map(async (page, k) => {
    const a = k * per, b = Math.min(total, a + per), seg = path.join(TMP, `seg${k}.mp4`);
    await run(FFMPEG, ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
      '-c:v', 'libx264', '-preset', 'slow', '-crf', '19', '-pix_fmt', 'yuv420p', '-r', String(FPS), seg], async stdin => {
      for (let f = a; f < b; f++) {
        const b64 = await page.evaluate(t => { window.film.frame(t); return window.film.canvas.toDataURL('image/jpeg', .95).slice(23); }, f / FPS);
        if (!stdin.write(Buffer.from(b64, 'base64'))) await new Promise(r => stdin.once('drain', r));
        if (++done % 60 === 0) { const el = (Date.now() - t0) / 1000; console.log(`frames ${done}/${total}  ${el.toFixed(0)} s elapsed, ~${(el / done * (total - done)).toFixed(0)} s left`); }
      }
      stdin.end();
    });
    return seg;
  }));
  await browser.close();

  // one fixed gain to about -15 LUFS (keeps the score's quiet-to-loud arc), peaks held under -1 dBFS
  const probe = spawnSync(FFMPEG, ['-hide_banner', '-nostats', '-f', 's16le', '-ar', '48000', '-ac', '2', '-i', pcm, '-af', 'ebur128', '-f', 'null', '-'], { encoding: 'utf8' });
  const lufs = Number((probe.stderr.match(/Summary:[\s\S]*?I:\s*(-?[\d.]+) LUFS/) || [])[1]);
  const gain = Number.isFinite(lufs) ? Math.max(-12, Math.min(12, -15 - lufs)) : 0;
  console.log(`soundtrack loudness ${lufs} LUFS, gain ${gain.toFixed(1)} dB`);

  // join the segments, add the soundtrack and finish
  const list = path.join(TMP, 'list.txt');
  fs.writeFileSync(list, segs.map(s => `file '${s}'`).join('\n'));
  await run(FFMPEG, ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list, '-f', 's16le', '-ar', '48000', '-ac', '2', '-i', pcm,
    '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-af', `volume=${gain.toFixed(2)}dB,alimiter=limit=0.89:level=false`, '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
    '-shortest', '-movflags', '+faststart', '-metadata', 'title=盛世华诞', OUT]);
  fs.rmSync(TMP, { recursive: true, force: true });
  console.log('wrote', OUT);
})().catch(e => { console.error(e); process.exit(1); });
