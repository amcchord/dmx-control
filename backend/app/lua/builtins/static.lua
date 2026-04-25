NAME = "Static"
DESCRIPTION = "Distribute palette across targets once. No motion."

PARAMS = {}

function render(ctx)
  local pick
  if ctx.n <= 1 then
    pick = 0.0
  else
    pick = ctx.i / ctx.n
  end
  local r, g, b = ctx.palette:smooth(pick)
  return { r = r, g = g, b = b, brightness = 1.0 }
end
