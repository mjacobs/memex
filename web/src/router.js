// Tiny hash router: "#/notes/abc" -> ["notes", "abc"].

import { useEffect, useState } from "react";

function parse() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  return hash ? hash.split("/").map(decodeURIComponent) : [];
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

export function navigate(path) {
  window.location.hash = path.startsWith("#") ? path : `#/${path.replace(/^\//, "")}`;
}
