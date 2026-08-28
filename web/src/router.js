// Tiny hash router: "#/notes/abc" -> ["notes", "abc"].
// A trailing "?..." is treated as a query string, not a route segment:
// "#/?tags=a,b" -> route [] with query "tags=a,b".

import { useEffect, useState } from "react";

function rawHash() {
  return window.location.hash.replace(/^#\/?/, "");
}

function parse() {
  const [path] = rawHash().split("?");
  return path ? path.split("/").map(decodeURIComponent) : [];
}

function parseQuery() {
  const [, qs = ""] = rawHash().split(/\?(.*)/s);
  return new URLSearchParams(qs);
}

export function useRoute() {
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const onChange = () => setRoute(parse());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

/** Query params from the current hash, e.g. useQuery().get("tags"). Reactive
 * on hashchange, but consumers that only want the value at mount (to seed
 * initial state) can read it once and ignore updates. */
export function useQuery() {
  const [query, setQuery] = useState(parseQuery);
  useEffect(() => {
    const onChange = () => setQuery(parseQuery());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return query;
}

export function navigate(path) {
  window.location.hash = path.startsWith("#") ? path : `#/${path.replace(/^\//, "")}`;
}
