NAME = "Rainbow"
DESCRIPTION = "Full-hue HSV sweep. Ignores the palette."

PARAMS = {
  { id = "speed_hz",  label = "Speed",     type = "number", min = 0, max = 25, default = 0.15, suffix = "Hz" },
  { id = "offset",    label = "Offset",    type = "number", min = 0, max = 1,  default = 0.15 },
  { id = "direction", label = "Direction", type = "choice", options = { "forward", "reverse", "pingpong" }, default = "forward" },
}

function render(ctx)
  local p = ctx.params
  local speed = p.speed_hz or 0
  local cycles_done = ctx.t * speed
  local per_index = per_index_offset(p.offset or 0, ctx.n)
  local raw = cycles_done + ctx.i * per_index
  local phase = direction.apply(raw, p.direction, cycles_done)
  local r, g, b = color.hsv(phase, 1.0, 1.0)
  return { r = r, g = g, b = b, brightness = 1.0 }
end
