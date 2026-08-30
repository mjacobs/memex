// Plain-text preview of a markdown summary, for list cards.
//
// The runs list shows each run's summary inside a clickable card, so it can't
// render real markdown there: links inside the card would fight the card's
// own click. The detail view renders the full markdown; the card gets the
// text with the syntax peeled off — bold markers dropped, links reduced to
// their labels, leading bullets folded into one line.
export function previewText(markdown) {
  return markdown
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1") // [label](url) -> label
    .replace(/\*\*([^*]+)\*\*/g, "$1") // **bold** -> bold
    .replace(/(^|\n)\s*[-*]\s+/g, (m, p1) => (p1 ? " · " : "")) // bullets -> separators
    .replace(/\s+/g, " ")
    .trim();
}
