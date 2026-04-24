import React from "react";
import type { PaletteEntry } from "../api";

type Props = {
  colors: string[];
  /** When supplied, adds small W/A/UV indicator dots on entries whose
   *  aux values are set explicitly. */
  entries?: PaletteEntry[];
  className?: string;
};

function entryHasAux(entry?: PaletteEntry): {
  w: boolean;
  a: boolean;
  uv: boolean;
} {
  if (!entry) return { w: false, a: false, uv: false };
  return {
    w: entry.w != null,
    a: entry.a != null,
    uv: entry.uv != null,
  };
}

export default function PaletteSwatch({
  colors,
  entries,
  className = "",
}: Props) {
  if (!colors.length) {
    return (
      <div className={"h-8 w-full rounded-md bg-bg-elev " + className} />
    );
  }
  return (
    <div
      className={
        "flex h-8 w-full overflow-hidden rounded-md ring-1 ring-line " +
        className
      }
    >
      {colors.map((c, i) => {
        const flags = entryHasAux(entries?.[i]);
        const anyAux = flags.w || flags.a || flags.uv;
        return (
          <div
            key={i}
            className="relative h-full flex-1"
            style={{ background: c }}
            title={c}
          >
            {anyAux && (
              <div className="absolute bottom-0 left-0 right-0 flex h-1.5 items-center justify-center gap-0.5">
                {flags.w && (
                  <span
                    className="h-1 w-1 rounded-full bg-white ring-1 ring-black/50"
                    title="White channel set"
                  />
                )}
                {flags.a && (
                  <span
                    className="h-1 w-1 rounded-full bg-[#FF9F3A] ring-1 ring-black/50"
                    title="Amber channel set"
                  />
                )}
                {flags.uv && (
                  <span
                    className="h-1 w-1 rounded-full bg-[#7C4DFF] ring-1 ring-black/50"
                    title="UV channel set"
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
