import { createElement } from "react";

const URL_RE = /(https?:\/\/[^\s<>"')]+)/g;

function shortenUrl(url) {
  try {
    const u = new URL(url);
    return `${u.host}…`;
  } catch {
    return url.length > 40 ? `${url.slice(0, 40)}…` : url;
  }
}

/** Split text on bare http(s) URLs and render them as truncated, clickable links.
 * Returns an array of strings/elements suitable as React children — no HTML injection. */
export function linkifyText(text) {
  if (!text) return text;
  const parts = String(text).split(URL_RE);
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      return createElement(
        "a",
        {
          key: i,
          href: part,
          target: "_blank",
          rel: "noopener noreferrer",
          className: "error-link",
          title: part,
        },
        shortenUrl(part),
      );
    }
    return part;
  });
}

/** Preserve user-authored text exactly while making bare web URLs clickable. */
export function PlainText({ text }) {
  if (!text) return null;
  return createElement("p", { className: "body-text" }, linkifyText(text));
}
