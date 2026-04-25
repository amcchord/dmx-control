NAME = "Cycle"
DESCRIPTION = "Step through palette colors with no interpolation."

PARAMS = {
  { id = "speed_hz",  label = "Speed",     type = "number", min = 0,    max = 25, default = 0.5, suffix = "Hz" },
  { id = "offset",    label = "Offset",    type = "number", min = 0,    max = 1,  default = 0.0 },
  { id = "direction", label = "Direction", type = "choice", options = { "forward", "reverse", "pingpong" }, default = "forward" },
}

function render(ctx)
  local p = ctx.params
  local speed = p.speed_hz or 0
  local cycles_done = ctx.t * speed
  local per_index = per_index_offset(p.offset or 0, ctx.n)
  local raw = cycles_done + ctx.i * per_index
  local phase = direction.apply(raw, p.direction, cycles_done)
  local r, g, b = ctx.palette:step(phase)
  return { r = r, g = g, b = b, brightness = 1.0 }
end
