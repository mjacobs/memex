import assert from "node:assert/strict";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { PlainText } from "./linkify.js";

test("plain note URLs are safe clickable links", () => {
  const html = renderToStaticMarkup(
    PlainText({ text: "Save https://example.com/article for later" }),
  );

  assert.match(html, /^<p class="body-text">Save /);
  assert.match(html, /href="https:\/\/example\.com\/article"/);
  assert.match(html, /target="_blank"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, />example\.com…<\/a> for later<\/p>$/);
});

test("non-web schemes remain plain text", () => {
  const html = renderToStaticMarkup(
    PlainText({ text: "Do not open javascript:alert(1)" }),
  );

  assert.doesNotMatch(html, /<a /);
  assert.match(html, /javascript:alert\(1\)/);
});

test("a trailing period is not part of the link", () => {
  const html = renderToStaticMarkup(
    PlainText({ text: "See https://example.com/article." }),
  );

  assert.match(html, /href="https:\/\/example\.com\/article"/);
  assert.match(html, />example\.com…<\/a>\.<\/p>$/);
});
