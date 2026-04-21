import React from "react";

type Props = {
  colors: string[];
  className?: string;
};

export default function PaletteSwatch({ colors, className = "" }: Props) {
  if (!colors.length) {
    return (
      <div className={"h-8 w-full rounded-md bg-bg-elev " + className} />
    );
  }
  return (
    <div className={"flex h-8 w-full overflow-hidden rounded-md ring-1 ring-line " + className}>
      {colors.map((c, i) => (
        <div
          key={i}
          className="h-full flex-1"
          style={{ background: c }}
          title={c}
        />
      ))}
    </div>
  );
}
