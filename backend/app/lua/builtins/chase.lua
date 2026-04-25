NAME = "Chase"
DESCRIPTION = "Moving window of palette color across the selection."

PARAMS = {
  { id = "speed_hz",  label = "Speed",    type = "number", min = 0,    max = 25, default = 1.5, suffix = "Hz" },
  { id = "offset",    label = "Offset",   type = "number", min = 0,    max = 1,  default = 0.15 },
  { id = "size",      label = "Window",   type = "number", min = 0.05, max = 8,  default = 1.5 },
  { id = "softness",  label = "Softness", type = "number", min = 0,    max = 1,  default = 0.6 },
  { id = "direction", label = "Direction", type = "choice", options = { "forward", "reverse", "pingpong" }, default = "forward" },
}

function render(ctx)
  local p = ctx.params
  local speed = p.speed_hz or 0
  local cycles_done = ctx.t * speed
  local per_index = per_index_offset(p.offset or 0, ctx.n)
  local raw = cycles_done + ctx.i * per_index
  local phase = direction.apply(raw, p.direction, cycles_done)
  local size = math.max(0.05, (p.size or 1) / math.max(1, ctx.n) * 2.0)
  local bri = envelope.chase(phase, size, p.softness or 0.5)
  if bri <= 0.0 then
    return { active = false }
  end
  local r, g, b = ctx.palette:smooth(cycles_done * 0.5)
  return { r = r, g = g, b = b, brightness = bri }
end
