import type { LightModel } from "../../api";
import { ROLE_COLORS } from "./types";

/** Small image-or-channel-stripes preview rendered in the Models list and
 * at the top of the editor page. */
export default function ModelThumbnail({
  model,
  size = 64,
}: {
  model: LightModel;
  size?: number;
}) {
  if (model.image_url) {
    return (
      <div
        className="shrink-0 overflow-hidden rounded-md bg-bg-elev ring-1 ring-line"
        style={{ width: size, height: size }}
      >
        <img
          src={model.image_url}
          alt=""
          className="h-full w-full object-cover"
        />
      </div>
    );
  }
  const channels =
    (model.modes.find((x) => x.is_default) ?? model.modes[0])?.channels ??
    model.channels;
  return (
    <div
      className="flex shrink-0 items-center justify-center gap-0.5 rounded-md bg-bg-elev p-1 ring-1 ring-line"
      style={{ width: size, height: size }}
    >
      {channels.slice(0, 6).map((c, i) => (
        <span
          key={i}
          className="w-1.5 rounded-sm"
          style={{
            background: ROLE_COLORS[c] ?? "#8791a7",
            height: Math.max(12, size - 16),
          }}
        />
      ))}
    </div>
  );
}
