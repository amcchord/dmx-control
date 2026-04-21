import React, { useEffect, useState } from "react";
import { HexColorPicker } from "react-colorful";

type Props = {
  value: string; // hex like #RRGGBB
  onChange: (hex: string) => void;
  onCommit?: (hex: string) => void;
};

export default function ColorPicker({ value, onChange, onCommit }: Props) {
  const [local, setLocal] = useState(value);

  useEffect(() => {
    setLocal(value);
  }, [value]);

  const commit = (hex: string) => {
    setLocal(hex);
    onChange(hex);
  };

  return (
    <div className="flex flex-col gap-3">
      <HexColorPicker
        color={local}
        onChange={commit}
        onMouseUp={() => onCommit?.(local)}
        onTouchEnd={() => onCommit?.(local)}
      />
      <div className="flex items-center gap-2">
        <div
          className="h-9 w-9 rounded-lg ring-1 ring-line"
          style={{ background: local }}
        />
        <input
          className="input font-mono uppercase"
          value={local}
          onChange={(e) => {
            const v = e.target.value;
            setLocal(v);
            if (/^#[0-9a-fA-F]{6}$/.test(v)) {
              onChange(v);
              onCommit?.(v);
            }
          }}
          spellCheck={false}
        />
      </div>
    </div>
  );
}
