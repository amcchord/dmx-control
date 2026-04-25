-- Effect script stdlib. Loaded once per LuaRuntime and re-used as the
-- sandbox env for every compiled effect. Authoritative palette / color /
-- envelope math used by both the live engine (per-slot render) and the
-- preview websocket. Intentionally tiny: each helper is small enough for
-- Claude (and humans) to keep in their head.

local M = {}

-- =========================================================================
-- Math primitives
-- =========================================================================
local function fract(v)
  return v - math.floor(v)
end

local function clamp(v, lo, hi)
  if v < lo then return lo end
  if v > hi then return hi end
  return v
end

local function lerp(a, b, w)
  return a + (b - a) * w
end

M.fract = fract
M.clamp = clamp
M.lerp = lerp

-- =========================================================================
-- Color helpers
-- =========================================================================
local color = {}
M.color = color

local function _hex_pair(s)
  return tonumber(string.sub(s, 1, 2), 16) or 0,
         tonumber(string.sub(s, 3, 4), 16) or 0,
         tonumber(string.sub(s, 5, 6), 16) or 0
end

function color.hex(s)
  if type(s) ~= "string" then return 0, 0, 0 end
  local body = s
  if string.sub(body, 1, 1) == "#" then body = string.sub(body, 2) end
  if #body ~= 6 then return 0, 0, 0 end
  local r, g, b = _hex_pair(body)
  return r, g, b
end

function color.hsv(h, s, v)
  h = fract(h or 0)
  s = clamp(s or 1, 0, 1)
  v = clamp(v or 1, 0, 1)
  local i = math.floor(h * 6.0)
  local f = h * 6.0 - i
  local p = v * (1.0 - s)
  local q = v * (1.0 - f * s)
  local t = v * (1.0 - (1.0 - f) * s)
  local r, g, b
  local k = i % 6
  if k == 0 then r, g, b = v, t, p
  elseif k == 1 then r, g, b = q, v, p
  elseif k == 2 then r, g, b = p, v, t
  elseif k == 3 then r, g, b = p, q, v
  elseif k == 4 then r, g, b = t, p, v
  else r, g, b = v, p, q
  end
  return math.floor(r * 255 + 0.5),
         math.floor(g * 255 + 0.5),
         math.floor(b * 255 + 0.5)
end

function color.mix(r1, g1, b1, r2, g2, b2, w)
  w = clamp(w or 0, 0, 1)
  return math.floor(r1 * (1 - w) + r2 * w + 0.5),
         math.floor(g1 * (1 - w) + g2 * w + 0.5),
         math.floor(b1 * (1 - w) + b2 * w + 0.5)
end

-- =========================================================================
-- Palette sampler factory (called by the host with the current palette)
-- =========================================================================
local palette_mt = {}
palette_mt.__index = palette_mt

function palette_mt:size()
  return self._n
end

function palette_mt:get(i)
  -- 1-indexed wrap-around access.
  if self._n == 0 then return 0, 0, 0 end
  local idx = ((i - 1) % self._n) + 1
  local entry = self._entries[idx]
  return entry[1], entry[2], entry[3]
end

function palette_mt:smooth(phase)
  if self._n == 0 then return 0, 0, 0 end
  if self._n == 1 then
    local e = self._entries[1]
    return e[1], e[2], e[3]
  end
  local p = fract(phase or 0)
  local n = self._n
  local pos = p * n
  local lo = math.floor(pos) % n + 1
  local hi = (lo % n) + 1
  local frac = pos - math.floor(pos)
  local a, b = self._entries[lo], self._entries[hi]
  return math.floor(a[1] + (b[1] - a[1]) * frac + 0.5),
         math.floor(a[2] + (b[2] - a[2]) * frac + 0.5),
         math.floor(a[3] + (b[3] - a[3]) * frac + 0.5)
end

function palette_mt:step(phase)
  if self._n == 0 then return 0, 0, 0 end
  local p = fract(phase or 0)
  local idx = math.floor(p * self._n) % self._n + 1
  local e = self._entries[idx]
  return e[1], e[2], e[3]
end

local palette_lib = {}
M.palette = palette_lib

-- Build a palette object from an array-of-arrays {{r,g,b},...}. The host
-- (Python) calls this when constructing the per-call ctx so user scripts
-- always receive a usable :smooth/:step/:size object.
function palette_lib.new(entries)
  local n = 0
  local arr = {}
  if type(entries) == "table" then
    for _, ent in ipairs(entries) do
      if type(ent) == "table" then
        local r = tonumber(ent[1] or ent.r or 0) or 0
        local g = tonumber(ent[2] or ent.g or 0) or 0
        local b = tonumber(ent[3] or ent.b or 0) or 0
        n = n + 1
        arr[n] = { math.floor(clamp(r, 0, 255)),
                   math.floor(clamp(g, 0, 255)),
                   math.floor(clamp(b, 0, 255)) }
      end
    end
  end
  return setmetatable({ _entries = arr, _n = n }, palette_mt)
end

-- =========================================================================
-- Envelopes
-- =========================================================================
local envelope = {}
M.envelope = envelope

function envelope.pulse(phase)
  return 0.5 + 0.5 * math.cos(2.0 * math.pi * fract(phase or 0))
end

function envelope.wave(phase)
  return 0.5 + 0.5 * math.sin(2.0 * math.pi * fract(phase or 0))
end

function envelope.strobe(phase, duty)
  duty = clamp(duty or 0.5, 0.02, 0.98)
  return (fract(phase or 0) < duty) and 1.0 or 0.0
end

-- Triangular window of width ~size centered on phase==0 (mod 1).
-- ``size`` is the proportional width [0..1]; ``softness`` controls the
-- ramp steepness on the edges.
function envelope.chase(phase, size, softness)
  size = math.max(0.001, size or 0.5)
  softness = clamp(softness or 0.5, 0, 1)
  local p = fract(phase or 0)
  local d = math.min(p, 1.0 - p)
  local width = 0.5 * size
  if d >= width then return 0.0 end
  local t = 1.0 - (d / width)
  if softness <= 0.0 then
    if t > 0.0 then return 1.0 else return 0.0 end
  end
  local edge = softness
  if t >= 1.0 - edge then return 1.0 end
  return t / math.max(0.001, 1.0 - edge)
end

-- =========================================================================
-- Direction + spread helpers
-- =========================================================================
local direction = {}
M.direction = direction

-- Apply ``forward|reverse|pingpong`` to a raw phase.
-- ``cycles_done`` should be the unwrapped cycle count (e.g. t * speed_hz)
-- so pingpong can flip every full cycle.
function direction.apply(phase, dir, cycles_done)
  local p = fract(phase or 0)
  if dir == "reverse" then return fract(1.0 - p) end
  if dir == "pingpong" then
    if math.floor(cycles_done or 0) % 2 == 1 then return fract(1.0 - p) end
  end
  return p
end

-- Map a slider in [0, 1] to a per-index phase step. ``offset == 1.0``
-- lands at "perfect chase" (each index lags 1/n of a full cycle).
function M.per_index_offset(offset, n)
  offset = offset or 0
  if offset <= 1.0 then return offset / math.max(1, n) end
  return offset
end

-- =========================================================================
-- Easing
-- =========================================================================
local easing = {}
M.easing = easing

function easing.linear(t) return clamp(t or 0, 0, 1) end
function easing.quad_in(t) t = clamp(t or 0, 0, 1); return t * t end
function easing.quad_out(t) t = clamp(t or 0, 0, 1); return 1 - (1 - t) * (1 - t) end
function easing.quad_inout(t)
  t = clamp(t or 0, 0, 1)
  if t < 0.5 then return 2 * t * t end
  return 1 - 2 * (1 - t) * (1 - t)
end
function easing.cosine(t)
  return 0.5 - 0.5 * math.cos(math.pi * clamp(t or 0, 0, 1))
end

-- =========================================================================
-- Deterministic hash / noise helpers (used by sparkle, randomized chases)
-- =========================================================================
local noise = {}
M.noise = noise

local function _mix32(x)
  x = (x ~ (x >> 16)) * 0x85ebca6b & 0xffffffff
  x = (x ~ (x >> 13)) * 0xc2b2ae35 & 0xffffffff
  x = (x ~ (x >> 16)) & 0xffffffff
  return x
end

-- Hash any tuple of (number-or-string) args to a deterministic float in [0, 1).
function noise.hash(...)
  local n = select("#", ...)
  local h = 0x811c9dc5
  for i = 1, n do
    local arg = select(i, ...)
    local s = tostring(arg)
    for c = 1, #s do
      h = (h ~ string.byte(s, c)) * 16777619 & 0xffffffff
    end
    h = _mix32(h)
  end
  return (h & 0xffffffff) / 0x100000000
end

-- Small value-noise approximation good enough for "rolling" effects.
function noise.simplex(x, y)
  y = y or 0
  local xi, yi = math.floor(x), math.floor(y)
  local xf, yf = x - xi, y - yi
  local u = xf * xf * (3 - 2 * xf)
  local v = yf * yf * (3 - 2 * yf)
  local function corner(ix, iy)
    return noise.hash(ix, iy) * 2 - 1
  end
  local a = corner(xi,     yi)
  local b = corner(xi + 1, yi)
  local c = corner(xi,     yi + 1)
  local d = corner(xi + 1, yi + 1)
  local ab = a + (b - a) * u
  local cd = c + (d - c) * u
  return (ab + (cd - ab) * v) * 0.5 + 0.5
end

-- =========================================================================
-- Tiny stateful PRNG factory (does NOT touch math.random's global state).
-- =========================================================================
function M.random(seed)
  local state = (seed or 1) & 0xffffffff
  if state == 0 then state = 0xdeadbeef end
  local rng = {}
  function rng:next()
    state = (state * 1103515245 + 12345) & 0x7fffffff
    return state / 0x7fffffff
  end
  function rng:int(lo, hi)
    return lo + math.floor(self:next() * (hi - lo + 1))
  end
  return rng
end

return M
