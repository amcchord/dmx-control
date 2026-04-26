import { useEffect, useState } from "react";

/** Viewport breakpoint matching Tailwind's `md` (768px).
 *
 * Drives the responsive shell: ``< md`` renders the mobile chrome
 * (bottom tab bar + sticky Now Playing pill), ``>= md`` renders the
 * desktop chrome (side nav + Live rail). Page components stay agnostic
 * — they read ``isMobile`` and adapt where it matters (e.g. hide the
 * Lua editor on phones). */
const QUERY = "(min-width: 768px)";

export type Viewport = {
  isMobile: boolean;
  isDesktop: boolean;
};

export function useViewport(): Viewport {
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    return window.matchMedia(QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia(QUERY);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    setMatches(mql.matches);
    if (mql.addEventListener) {
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    }
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, []);

  return { isMobile: !matches, isDesktop: matches };
}
