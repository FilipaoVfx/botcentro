import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

function current(): { path: string; search: URLSearchParams } {
  return { path: window.location.pathname, search: new URLSearchParams(window.location.search) };
}

export function navigate(to: string): void {
  if (to === window.location.pathname + window.location.search) return;
  window.history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo({ top: 0 });
}

export function useRoute() {
  const [route, setRoute] = useState(current);
  useEffect(() => {
    const onPop = () => setRoute(current());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return route;
}

/** Enlace SPA: conserva el comportamiento nativo con modificadores (nueva pestaña). */
export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(href);
  };
  return <a href={href} onClick={handle} {...rest} />;
}
