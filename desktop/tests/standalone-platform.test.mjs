import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

import { POPULATED_FIXTURES } from "./support/fixtures.mjs";

// Bundle the real matching function without loading unrelated UI dependencies.
const bundled = await build({
  stdin: {
    contents: 'export { standaloneFor } from "./ui/app/emulator.tsx";',
    resolveDir: fileURLToPath(new URL("../..", import.meta.url)),
  },
  bundle: true,
  format: "esm",
  write: false,
  plugins: [{
    name: "unused-ui-imports",
    setup(b) {
      b.onResolve({ filter: /.*/ }, ({ path, importer }) =>
        importer.endsWith("/emulator.tsx") ? { path, external: true, sideEffects: false } : null);
    },
  }],
});
const { standaloneFor } = await import("data:text/javascript;base64," +
  Buffer.from(bundled.outputFiles[0].text).toString("base64"));
const status = POPULATED_FIXTURES.get_emulator_status;

for (const [name, slug, expected] of [
  ["Any display name", "xbox360", "xenia"],
  ["xbox360", undefined, "xenia"],
  ["Xbox 360", undefined, null],
  ["Microsoft Xbox 360", undefined, null],
  ["Microsoft - Xbox 360", undefined, null],
  ["xbox360", "xbox", null],
  ["xbox360", "", null],
  ["Nintendo Switch", undefined, "eden"],
  ["Nintendo - Wii U", undefined, "cemu"],
  ["Any display name", "switch", "eden"],
  ["Any display name", "wii u", "cemu"],
  ...["xbox-360", "xbox_360", "microsoft-xbox-360", "xbox3600", "prefix-xbox360"]
    .flatMap((alias) => [["xbox360", alias, null], [alias, undefined, null]]),
]) {
  test(`standalone mapping ${JSON.stringify([name, slug])}`, () => {
    assert.equal(standaloneFor(status, name, slug)?.key ?? null, expected);
  });
}
