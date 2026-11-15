import test from 'node:test';
import assert from 'node:assert/strict';
import { POPULATED_FIXTURES, xeniaFixtures } from './support/fixtures.mjs';
import { DIST, distIsBuilt, startServer, launchBrowser, openRoute } from './support/render-harness.mjs';

let chromium;
try { ({ chromium } = await import('playwright')); }
catch { console.warn('[xenia-core.render] playwright not installed — skipping render tests'); }
const SKIP = !chromium ? 'playwright is not installed'
  : !distIsBuilt() ? `no build at ${DIST} — run npm run build` : false;

test('Xbox core selection persists and a native build clears it', { skip: SKIP }, async (t) => {
  const srv = await startServer({ fixtures: xeniaFixtures() });
  const browser = await launchBrowser({ chromium });
  t.after(async () => { await browser.close(); await srv.close(); });
  const r = await openRoute(browser, srv.origin, '/romm-sync-cores');
  try {
    assert.match((await r.probe()).text, /Xenia runs this platform/);
    await r.activateText('Xbox 360');
    assert.match((await r.probe()).text, /does not support RetroArch save states or save sync/);
    assert.doesNotMatch((await r.probe()).text, /Show all installed cores/);
    await r.activateText('xenia_edge');
    await r.page.reload({ waitUntil: 'networkidle' });
    await r.page.getByText('Xenia Edge via RetroArch', { exact: false }).waitFor();
    assert.ok(srv.requests.some((x) => x.method === 'set_core_override' && x.args[1] === 'xenia_edge'));
  } finally { await r.close(); }
  const e = await openRoute(browser, srv.origin, '/romm-sync-emulators');
  try {
    assert.match((await e.probe()).text, /Native preference inactive/);
    await e.activateText('Xenia');
    await e.page.getByText('Xenia Edge', { exact: true }).last().click();
    await e.page.waitForTimeout(500);
    assert.match((await e.probe()).text, /chosen by you/);
    await e.page.reload({ waitUntil: 'networkidle' });
    assert.doesNotMatch((await e.probe()).text, /Native preference inactive/);
  } finally { await e.close(); }
});

test('core-only Xbox exposes missing core download and Auto', { skip: SKIP }, async (t) => {
  const srv = await startServer({ fixtures: xeniaFixtures({ native: false, core: false }) });
  const browser = await launchBrowser({ chromium });
  t.after(async () => { await browser.close(); await srv.close(); });
  const r = await openRoute(browser, srv.origin, '/romm-sync-cores');
  try {
    assert.match((await r.probe()).text, /Xenia Edge core not installed/);
    await r.activateText('Xbox 360');
    assert.match((await r.probe()).text, /xenia_edge.*download/);
    await r.activateText('xenia_edge');
    assert.ok(srv.requests.some((x) => x.method === 'download_core'));
    assert.ok(srv.requests.some((x) => x.method === 'set_core_override' && x.args[1] === 'xenia_edge'));
    await r.activateText('Xbox 360');
    await r.page.getByText(/^Auto\s*·/).last().click();
    await r.page.waitForTimeout(500);
    await r.page.reload({ waitUntil: 'networkidle' });
    assert.match((await r.probe()).text, /Xenia Edge via RetroArch/);
  } finally { await r.close(); }
});

test('explicit missing core remains selected even with native Xenia installed', { skip: SKIP }, async (t) => {
  const srv = await startServer({ fixtures: xeniaFixtures({ native: true, core: false, pinned: true }) });
  const browser = await launchBrowser({ chromium });
  t.after(async () => { await browser.close(); await srv.close(); });
  const r = await openRoute(browser, srv.origin, '/romm-sync-cores');
  try {
    assert.match((await r.probe()).text, /Xenia Edge core not installed/);
    await r.activateText('Xbox 360');
    assert.match((await r.probe()).text, /xenia_edge.*download/);
  } finally { await r.close(); }
});

test('Play installs and pins the recommended Xbox core before retrying', { skip: SKIP }, async (t) => {
  const fixtures = xeniaFixtures({ native: false, core: false });
  const game = { ...POPULATED_FIXTURES.get_library_games.games[0], platform: 'Xbox 360', platform_slug: 'xbox360' };
  const launches = [];
  fixtures.get_library_games = { success: true, games: [game] };
  fixtures.launch_game = (_args, state) => {
    launches.push({ pin: state.xeniaCore, installed: state.xeniaInstalled });
    return state.xeniaInstalled && state.xeniaCore === 'xenia_edge'
      ? { success: true }
      : { success: false, needs_core: true, platform_name: 'Xbox 360', platform_slug: 'xbox360',
          candidates: ['xenia_edge'], can_download: true };
  };
  const srv = await startServer({ fixtures });
  const browser = await launchBrowser({ chromium });
  t.after(async () => { await browser.close(); await srv.close(); });
  const r = await openRoute(browser, srv.origin, '/romm-sync-library');
  try {
    await r.activateText('Platforms');
    await r.activateText('Test Platform');
    await r.activateText('Harness Test Game');
    assert.match((await r.probe()).text, /Xbox 360 needs a core/);
    await r.activateText('recommended');
    assert.match((await r.probe()).text, /Core installed/);
    const relevant = srv.requests.filter((x) => ['download_core', 'set_core_override', 'get_emulator_status'].includes(x.method));
    const download = relevant.findIndex((x) => x.method === 'download_core');
    assert.deepEqual(relevant[download + 1], { method: 'set_core_override', args: ['xbox360', 'xenia_edge'] });
    assert.equal(relevant[download + 2]?.method, 'get_emulator_status');
    await r.activateText('Play now');
    assert.deepEqual(launches, [{ pin: undefined, installed: undefined }, { pin: 'xenia_edge', installed: true }]);
    assert.deepEqual(r.errors, []);
  } finally { await r.close(); }
});
