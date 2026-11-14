import test from "node:test";
import assert from "node:assert/strict";

import { POPULATED_FIXTURES } from "./support/fixtures.mjs";
import { startServer } from "./support/render-harness.mjs";

async function rpc(srv, method, ...args) {
  const response = await fetch(`${srv.origin}/api/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ args }),
  });
  assert.equal(response.status, 200);
  return (await response.json()).result;
}

test("emulator fixtures list each standalone's own builds and platforms", async (t) => {
  const srv = await startServer({ fixtures: POPULATED_FIXTURES });
  t.after(() => srv.close());
  const status = await rpc(srv, "get_emulator_status");
  for (const [key, name, platform, paths] of [
    ["eden", "Eden", "switch", ["/home/t/AppImages/eden.AppImage"]],
    ["cemu", "Cemu", "wii u", ["flatpak:info.cemu.Cemu"]],
    ["xenia", "Xenia", "xbox360", ["/home/t/AppImages/xenia_canary_linux.AppImage", "/home/t/AppImages/xenia_edge_linux.AppImage"]],
  ]) {
    const row = status.standalone.find((e) => e.key === key);
    assert.equal(row.name, name);
    assert.equal(row.installed, true);
    assert.deepEqual(row.platforms, [platform]);
    const reply = await rpc(srv, "list_emulator_builds", key);
    assert.equal(reply.success, true);
    assert.equal(reply.key, key);
    assert.equal(reply.name, name);
    assert.equal(reply.selected, "");
    assert.deepEqual(reply.builds.map((b) => b.path), paths);
    assert.equal(reply.builds.find((b) => b.current).path, row.executable);
  }
  const xenia = await rpc(srv, "list_emulator_builds", "xenia");
  assert.deepEqual(xenia.builds.map((b) => b.label), ["Xenia Canary", "Xenia Edge"]);
  assert.ok(xenia.builds.every((b) => b.kind === "appimage"));
});

test("set_emulator_build persists Edge by key, resets to auto, and isolates servers", async (t) => {
  const srv = await startServer({ fixtures: POPULATED_FIXTURES });
  const other = await startServer({ fixtures: POPULATED_FIXTURES });
  t.after(() => Promise.all([srv.close(), other.close()]));
  const edge = "/home/t/AppImages/xenia_edge_linux.AppImage";
  assert.deepEqual(await rpc(srv, "set_emulator_build", "xenia", edge), { success: true });
  assert.deepEqual(srv.requests.at(-1), { method: "set_emulator_build", args: ["xenia", edge] });
  assert.equal((await rpc(srv, "list_emulator_builds", "xenia")).selected, edge);
  for (const key of ["eden", "cemu"]) {
    assert.equal((await rpc(srv, "list_emulator_builds", key)).selected, "");
  }
  assert.equal((await rpc(other, "list_emulator_builds", "xenia")).selected, "");
  assert.deepEqual(await rpc(srv, "set_emulator_build", "xenia", ""), { success: true });
  assert.equal((await rpc(srv, "list_emulator_builds", "xenia")).selected, "");
  assert.equal((await rpc(srv, "list_emulator_builds", "unknown")).success, false);
  assert.equal((await rpc(srv, "set_emulator_build", "unknown", edge)).success, false);
});

test("fixture server rejects malformed RPC arguments", async (t) => {
  const srv = await startServer({ fixtures: POPULATED_FIXTURES });
  t.after(() => srv.close());
  for (const body of ["{", JSON.stringify({ args: "xenia" })]) {
    const response = await fetch(`${srv.origin}/api/list_emulator_builds`, { method: "POST", body });
    assert.equal(response.status, 400);
  }
});
