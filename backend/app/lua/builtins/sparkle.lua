NAME = "Sparkle"
DESCRIPTION = "Random per-target flashes that decay quickly."

PARAMS = {
  { id = "speed_hz",  label = "Speed",     type = "number", min = 0, max = 25, default = 2.0, suffix = "Hz" },
  { id = "offset",    label = "Offset",    type = "number", min = 0, max = 1,  default = 0.0 },
}

local PROBABILITY = 96 / 256  -- ~38% chance per slot per bucket

function render(ctx)
  local p = ctx.params
  local rate = math.max(0.5, (p.speed_hz or 1.0) * 4.0)
  local bucket = math.floor(ctx.t * rate)

  -- Per-slot deterministic seed including the parent effect's seed so
  -- concurrent sparkles do not all flash on the same beats.
  local seed = ctx.seed or 0
  local lid = ctx.slot and ctx.slot.light_id or ctx.i
  local zid = (ctx.slot and ctx.slot.zone_id) or "_"
  local h = noise.hash(seed, lid, zid, bucket)
  if h >= PROBABILITY then
    return { active = false }
  end
  local flash_phase = (ctx.t * rate) - bucket
  local decay = math.max(0.0, 1.0 - flash_phase)
  -- Random palette color per bucket.
  local pick = math.floor(noise.hash(seed, lid, zid, bucket, "color") * ctx.palette:size()) + 1
  local r, g, b = ctx.palette:get(pick)
  return { r = r, g = g, b = b, brightness = decay }
end
