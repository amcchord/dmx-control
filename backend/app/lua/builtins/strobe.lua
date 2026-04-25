NAME = "Strobe"
DESCRIPTION = "Hard on/off flashes on the palette color."

PARAMS = {
  { id = "speed_hz",  label = "Speed",     type = "number", min = 0,    max = 25,   default = 6.0, suffix = "Hz" },
  { id = "offset",    label = "Offset",    type = "number", min = 0,    max = 1,    default = 0.0 },
  { id = "size",      label = "Duty",      type = "number", min = 0.02, max = 0.95, default = 0.4 },
  { id = "direction", label = "Direction", type = "choice", options = { "forward", "reverse", "pingpong" }, default = "forward" },
}

function render(ctx)
  local p = ctx.params
  local speed = p.speed_hz or 0
  local cycles_done = ctx.t * speed
  local per_index = per_index_offset(p.offset or 0, ctx.n)
  local raw = cycles_done + ctx.i * per_index
  local phase = direction.apply(raw, p.direction, cycles_done)
  local bri = envelope.strobe(phase, p.size or 0.4)
  if bri <= 0.0 then
    return { active = false }
  end
  local r, g, b = ctx.palette:smooth(cycles_done * 0.1)
  return { r = r, g = g, b = b, brightness = bri }
end
