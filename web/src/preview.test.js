import { test } from "node:test";
import assert from "node:assert/strict";
import { previewText } from "./preview.js";

test("bold and links are reduced to their text", () => {
  assert.equal(
    previewText("- **Task one** — done. [note](#/notes/abc)\n- **Task two** — dropped."),
    "Task one — done. note · Task two — dropped.",
  );
});

test("plain text passes through", () => {
  assert.equal(previewText("nothing fancy here"), "nothing fancy here");
});
