"""Sandboxed Lua runtime for effect scripts.

Each :class:`LuaScript` owns its own :class:`lupa.LuaRuntime`, compiles
the user's source inside a restricted env, and exposes either a
``render(ctx)`` per-slot pure function or a ``tick(t, ctx)`` whole-frame
function. Globals like ``NAME``, ``DESCRIPTION``, ``PARAMS`` are read
back from the script's env after the compile pass.

Sandbox notes:

* No ``io``, ``os``, ``package``, ``debug``, ``require``, ``dofile``,
  ``loadfile``, ``load``, ``loadstring``, ``arg``, ``collectgarbage``.
  Only ``math``, ``string``, ``table``, plus a curated stdlib.
* The Lua runtime is created with ``register_eval=False`` and
  ``register_builtins=False`` so Python objects are not exposed to user
  scripts.
* A Lua debug-hook counts instructions and aborts a runaway script
  after ``MAX_LUA_INSTRUCTIONS`` operations per call.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import lupa.lua54 as _lua

log = logging.getLogger(__name__)

LuaError = _lua.LuaError
LuaSyntaxError = _lua.LuaSyntaxError

# Per-call instruction budget (Lua VM ops). 30 Hz * ~256 slots * a few
# hundred ops per slot leaves headroom for users who want noise/random.
MAX_LUA_INSTRUCTIONS = 200_000
# Hard ceiling on user-source size (bytes).
MAX_SOURCE_BYTES = 64 * 1024
# Caps for the per-script cached schema.
MAX_PARAM_ENTRIES = 32

_STDLIB_SOURCE: Optional[str] = None


def _load_stdlib_source() -> str:
    global _STDLIB_SOURCE
    if _STDLIB_SOURCE is None:
        path = Path(__file__).with_name("stdlib.lua")
        _STDLIB_SOURCE = path.read_text(encoding="utf-8")
    return _STDLIB_SOURCE


class ScriptError(Exception):
    """Raised on compile or runtime errors. Carries an optional line."""

    def __init__(self, message: str, line: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"message": self.message}
        if self.line is not None:
            out["line"] = self.line
        return out


@dataclass
class LuaScriptMeta:
    """User-facing metadata extracted from a compiled script."""

    name: str = ""
    description: str = ""
    param_schema: list[dict[str, Any]] = field(default_factory=list)


# Param-schema validation -------------------------------------------------

_VALID_PARAM_TYPES = {"number", "slider", "color", "bool", "choice"}


def _coerce_schema(raw: Any) -> list[dict[str, Any]]:
    """Normalize a Lua-side ``PARAMS`` table into a JSON-friendly list."""
    if raw is None:
        return []
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    items: list[Any] = []
    if hasattr(raw, "values"):
        try:
            items = list(raw.values())
        except Exception:
            items = []
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    for item in items[:MAX_PARAM_ENTRIES]:
        entry = _coerce_schema_entry(item)
        if entry is None:
            continue
        if entry["id"] in seen_ids:
            continue
        seen_ids.add(entry["id"])
        out.append(entry)
    return out


def _coerce_schema_entry(item: Any) -> Optional[dict[str, Any]]:
    d: dict[str, Any]
    if isinstance(item, dict):
        d = dict(item)
    elif hasattr(item, "items"):
        try:
            d = {k: v for k, v in item.items()}
        except Exception:
            return None
    else:
        return None
    raw_id = d.get("id")
    if not isinstance(raw_id, str):
        return None
    pid = raw_id.strip()[:48]
    if not pid:
        return None
    raw_type = d.get("type")
    if isinstance(raw_type, str) and raw_type in _VALID_PARAM_TYPES:
        ptype = raw_type
    else:
        ptype = "number"
    out: dict[str, Any] = {"id": pid, "type": ptype}
    label = d.get("label")
    if isinstance(label, str) and label.strip():
        out["label"] = label.strip()[:64]
    else:
        out["label"] = pid
    suffix = d.get("suffix")
    if isinstance(suffix, str):
        out["suffix"] = suffix.strip()[:16]
    if ptype in ("number", "slider"):
        out["min"] = float(d.get("min", 0))
        out["max"] = float(d.get("max", 1))
        if out["max"] <= out["min"]:
            out["max"] = out["min"] + 1
        step = d.get("step")
        if isinstance(step, (int, float)) and step > 0:
            out["step"] = float(step)
        out["default"] = float(d.get("default", out["min"]))
    elif ptype == "bool":
        out["default"] = bool(d.get("default", False))
    elif ptype == "color":
        default = d.get("default", "#FFFFFF")
        out["default"] = str(default) if isinstance(default, str) else "#FFFFFF"
    elif ptype == "choice":
        opts_raw = d.get("options")
        choices: list[str] = []
        if hasattr(opts_raw, "values"):
            try:
                choices = [str(v) for v in opts_raw.values()][:32]
            except Exception:
                choices = []
        elif isinstance(opts_raw, (list, tuple)):
            choices = [str(v) for v in opts_raw][:32]
        if not choices:
            return None
        out["options"] = choices
        default = d.get("default")
        out["default"] = (
            default
            if isinstance(default, str) and default in choices
            else choices[0]
        )
    return out


def merge_with_schema(
    schema: list[dict[str, Any]], params: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Take a user-provided ``params`` dict and clamp/fill against schema."""
    out: dict[str, Any] = {}
    src = dict(params or {})
    for entry in schema:
        pid = entry["id"]
        ptype = entry["type"]
        v = src.get(pid, entry.get("default"))
        if ptype in ("number", "slider"):
            try:
                fv = float(v)
            except (TypeError, ValueError):
                fv = float(entry.get("default", entry.get("min", 0)))
            lo = float(entry.get("min", 0))
            hi = float(entry.get("max", 1))
            if fv < lo:
                fv = lo
            if fv > hi:
                fv = hi
            out[pid] = fv
        elif ptype == "bool":
            out[pid] = bool(v)
        elif ptype == "color":
            out[pid] = str(v) if isinstance(v, str) else "#FFFFFF"
        elif ptype == "choice":
            choices = entry.get("options") or []
            if isinstance(v, str) and v in choices:
                out[pid] = v
            else:
                out[pid] = entry.get("default", choices[0] if choices else "")
    # Preserve unknown extras so a script that adds a param after save still
    # sees the user's old value when first loaded.
    for k, v in src.items():
        if k not in out:
            out[k] = v
    return out


# Sandbox bootstrap -------------------------------------------------------

_BOOTSTRAP = r"""
local stdlib_source, stdlib_chunkname = ...
local SAFE_NAMES = {
  "ipairs","pairs","next","select","tostring","tonumber",
  "type","error","assert","pcall","xpcall","unpack","setmetatable",
  "getmetatable","rawget","rawset","rawlen","rawequal","print",
}

local function build_safe_globals(stdlib)
  local env = {
    math = math,
    string = string,
    table = table,
  }
  for _, name in ipairs(SAFE_NAMES) do
    env[name] = _G[name]
  end
  if stdlib ~= nil then
    for k, v in pairs(stdlib) do env[k] = v end
  end
  -- Provide a no-op print that drops to stderr via the host's logger.
  env.print = function() end
  return env
end

-- Compile and exec the stdlib in its own minimal env.
local stdlib_env = { math = math, string = string, table = table,
  ipairs = ipairs, pairs = pairs, next = next, select = select,
  tostring = tostring, tonumber = tonumber, type = type,
  setmetatable = setmetatable, getmetatable = getmetatable,
  error = error, assert = assert, pcall = pcall, xpcall = xpcall,
}
local stdlib_fn, stdlib_err = load(stdlib_source, stdlib_chunkname, "t", stdlib_env)
if not stdlib_fn then error("stdlib compile failed: " .. tostring(stdlib_err)) end
local stdlib = stdlib_fn()

-- Returned to the host: a function that compiles a user script in a
-- fresh sandbox and returns the fully-populated env table.
local function compile(src, chunkname)
  local env = build_safe_globals(stdlib)
  local fn, err = load(src, chunkname or "=effect", "t", env)
  if not fn then return nil, err, env end
  local ok, runerr = pcall(fn)
  if not ok then return nil, runerr, env end
  return env, nil, env
end

return compile, stdlib
"""


def _new_runtime() -> "_lua.LuaRuntime":
    """Construct a Lua runtime with safe defaults."""
    return _lua.LuaRuntime(
        unpack_returned_tuples=True,
        register_eval=False,
        register_builtins=False,
        attribute_filter=_block_python_attrs,
    )


def _block_python_attrs(obj: Any, attr_name: str, is_setting: bool) -> Any:
    """Block all attribute access on Python objects from inside Lua."""
    raise AttributeError(
        f"access to Python attribute {attr_name!r} is not allowed"
    )


# LuaScript ---------------------------------------------------------------


class LuaScript:
    """One compiled, sandboxed effect script.

    Thread safety: instances are NOT safe for concurrent use. Each tick
    of the engine runs serially and per-script. The preview WS owns its
    own LuaScript per connection, so contention is not a concern.
    """

    def __init__(self, source: str, *, chunkname: str = "=effect") -> None:
        if not isinstance(source, str):
            raise ScriptError("script source must be a string")
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ScriptError(
                f"script too large (max {MAX_SOURCE_BYTES} bytes)"
            )
        self._source = source
        self._chunkname = chunkname
        self._lock = threading.Lock()
        self._runtime = _new_runtime()
        self._compile()

    # -- public ---------------------------------------------------------
    @property
    def source(self) -> str:
        return self._source

    @property
    def meta(self) -> LuaScriptMeta:
        return self._meta

    @property
    def has_render(self) -> bool:
        return self._render is not None

    @property
    def has_tick(self) -> bool:
        return self._tick is not None

    def render_slot(self, ctx_table: Any) -> dict[str, Any]:
        """Call ``render(ctx)``. Caller passes a Lua-table-friendly dict."""
        if self._render is None:
            raise ScriptError("script has no render() function")
        return self._call(self._render, ctx_table)

    def tick_frame(self, ctx_table: Any) -> Any:
        """Call ``tick(ctx)``. Returns whatever the script returns."""
        if self._tick is None:
            raise ScriptError("script has no tick() function")
        return self._call_raw(self._tick, ctx_table)

    def make_palette(self, entries: list[tuple[int, int, int]]) -> Any:
        """Construct a Lua palette object from RGB triples."""
        # Build a Lua table-of-tables from Python.
        rt = self._runtime
        tbl = rt.table()
        for i, (r, g, b) in enumerate(entries, start=1):
            inner = rt.table()
            inner[1] = int(r)
            inner[2] = int(g)
            inner[3] = int(b)
            tbl[i] = inner
        return self._stdlib.palette.new(tbl)

    def new_table(self) -> Any:
        return self._runtime.table()

    # -- internals ------------------------------------------------------
    def _compile(self) -> None:
        rt = self._runtime
        try:
            compile_fn, stdlib = rt.execute(
                _BOOTSTRAP,
                _load_stdlib_source(),
                "=stdlib",
            )
        except LuaSyntaxError as e:
            raise ScriptError(f"stdlib syntax error: {e}") from e
        except LuaError as e:
            raise ScriptError(f"stdlib load failed: {e}") from e
        self._stdlib = stdlib

        try:
            env, err, _partial_env = compile_fn(self._source, self._chunkname)
        except LuaError as e:
            raise ScriptError(_format_lua_error(str(e))) from e
        if env is None:
            line, message = _split_lua_error(str(err))
            raise ScriptError(message, line)
        self._env = env

        # Read metadata + entry points from the env.
        self._meta = LuaScriptMeta(
            name=_as_str(env.NAME) or "",
            description=_as_str(env.DESCRIPTION) or "",
            param_schema=_coerce_schema(env.PARAMS),
        )
        render = env.render
        tick = env.tick
        self._render = render if callable_lua(render) else None
        self._tick = tick if callable_lua(tick) else None
        if self._render is None and self._tick is None:
            raise ScriptError(
                "script must define render(ctx) or tick(ctx)"
            )

        # Pre-build the instruction-budget hook + state. Each call resets
        # the counter; the hook itself is a single Lua closure we keep
        # around for the life of the script.
        self._debug = rt.eval("debug")
        self._budget_reset = rt.eval(
            "function(b) _dmx_budget = b; _dmx_used = 0 end"
        )
        self._budget_hook = rt.eval(
            "function() _dmx_used = _dmx_used + 1024; "
            "if _dmx_used > _dmx_budget then "
            "  error('script exceeded instruction budget') end end"
        )

    def _call(self, fn: Any, ctx_table: Any) -> dict[str, Any]:
        """Call a Lua function with instruction-budget enforcement."""
        with self._lock:
            try:
                result = self._invoke_with_budget(fn, ctx_table)
            except LuaError as e:
                line, message = _split_lua_error(str(e))
                raise ScriptError(message, line) from e
        return _coerce_render_result(result)

    def _call_raw(self, fn: Any, ctx_table: Any) -> Any:
        with self._lock:
            try:
                return self._invoke_with_budget(fn, ctx_table)
            except LuaError as e:
                line, message = _split_lua_error(str(e))
                raise ScriptError(message, line) from e

    def _invoke_with_budget(self, fn: Any, *args: Any) -> Any:
        """Run a Lua function under a debug-hook instruction limit.

        Lua's debug.sethook with mode 'count' fires every N instructions;
        we install a hook that errors out once a per-call ceiling is hit.
        """
        debug = self._debug
        if debug is None:
            return fn(*args)
        self._budget_reset(int(MAX_LUA_INSTRUCTIONS))
        try:
            debug.sethook(self._budget_hook, "", 1024)
            return fn(*args)
        finally:
            try:
                debug.sethook()
            except Exception:
                pass


def callable_lua(obj: Any) -> bool:
    if obj is None:
        return False
    try:
        return _lua.lua_type(obj) == "function"
    except Exception:
        return callable(obj)


def _as_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    return None


def _coerce_render_result(raw: Any) -> dict[str, Any]:
    """Normalize whatever ``render`` returned into a python dict.

    Tolerates several common shapes Claude (or humans) end up writing:

    * ``{r=255, g=0, b=0, brightness=1}`` - the canonical named form.
    * ``{255, 0, 0, brightness=1}`` - mixed positional + named (Lua mixes
      array-part and hash-part). We pull positional ``[1]/[2]/[3]`` for
      ``r/g/b`` when the named keys are missing.
    * ``{ {255, 0, 0}, brightness=1 }`` - palette-style nested triple in
      slot 1; we unpack it.
    * ``nil`` or non-table returns - treated as inactive.
    """
    if raw is None:
        return {"active": False}
    if isinstance(raw, dict):
        d = dict(raw)
    elif hasattr(raw, "items"):
        try:
            d = {k: v for k, v in raw.items()}
        except Exception:
            d = {}
    else:
        return {"active": False}

    # If slot 1 holds a nested triple ``{r,g,b}``, unpack it so the rest
    # of the function sees a flat shape.
    nested = d.get(1)
    if (
        nested is not None
        and not isinstance(nested, (int, float, bool, str))
        and "r" not in d
        and "g" not in d
        and "b" not in d
    ):
        try:
            inner = {k: v for k, v in nested.items()} if hasattr(nested, "items") else None
        except Exception:
            inner = None
        if isinstance(inner, dict):
            for src, dst in ((1, "r"), (2, "g"), (3, "b"), ("r", "r"), ("g", "g"), ("b", "b")):
                if dst not in d and src in inner:
                    d[dst] = inner[src]

    out: dict[str, Any] = {}
    if "active" in d:
        out["active"] = bool(d["active"])
    elif "on" in d:
        out["active"] = bool(d["on"])
    else:
        out["active"] = True
    if not out["active"]:
        return out

    def _pick(named_key: str, positional_idx: int) -> Any:
        if named_key in d:
            return d[named_key]
        return d.get(positional_idx, 0)

    out["r"] = _byte(_pick("r", 1))
    out["g"] = _byte(_pick("g", 2))
    out["b"] = _byte(_pick("b", 3))
    bri = d.get("brightness", d.get("bri"))
    if bri is None:
        out["brightness"] = 1.0
    else:
        try:
            fv = float(bri)
        except (TypeError, ValueError):
            fv = 1.0
        if fv < 0.0:
            fv = 0.0
        elif fv > 1.0:
            fv = 1.0
        out["brightness"] = fv
    return out


def _byte(v: Any) -> int:
    try:
        iv = int(round(float(v)))
    except (TypeError, ValueError):
        return 0
    if iv < 0:
        return 0
    if iv > 255:
        return 255
    return iv


_LINE_PREFIXES = ("[string \"", "effect:", "stdlib:")


def _split_lua_error(msg: str) -> tuple[Optional[int], str]:
    """Try to pull a line number out of a Lua error message."""
    m = msg.strip()
    # Drop the stack traceback that Lua appends, we only want the head.
    head, _, _tail = m.partition("\nstack traceback:")
    m = head.strip()
    line: Optional[int] = None
    # Common shapes:
    #   [string "=effect"]:12: undefined symbol foo
    #   effect:12: undefined symbol foo
    candidate = m
    if "]:" in candidate:
        try:
            tail = candidate.split("]:", 1)[1]
            num, rest = tail.split(":", 1)
            line = int(num)
            return line, rest.strip()
        except (ValueError, IndexError):
            pass
    for prefix in ("effect:", "stdlib:"):
        if candidate.startswith(prefix):
            try:
                rest = candidate[len(prefix):]
                num, body = rest.split(":", 1)
                line = int(num)
                return line, body.strip()
            except (ValueError, IndexError):
                continue
    return line, m


def _format_lua_error(msg: str) -> str:
    line, body = _split_lua_error(msg)
    if line is not None:
        return f"line {line}: {body}"
    return body


def compile_script(source: str, *, chunkname: str = "=effect") -> LuaScript:
    """Compile a Lua source string. Raises :class:`ScriptError` on failure."""
    return LuaScript(source, chunkname=chunkname)


def smoke_test_source(
    source: str,
    *,
    params: Optional[dict[str, Any]] = None,
    palette_colors: Optional[list[str]] = None,
    chunkname: str = "=smoketest",
) -> Optional[ScriptError]:
    """Compile + dry-run a script across a few slots and timesteps.

    Returns ``None`` on success or the first :class:`ScriptError` we hit.
    Used by the chat router to validate Claude's output before it reaches
    the user; if this returns an error we feed it back to Claude as the
    next ``tool_result`` and ask for a fix."""
    try:
        script = compile_script(source, chunkname=chunkname)
    except ScriptError as e:
        return e
    triples: list[tuple[int, int, int]] = []
    for hx in palette_colors or ["#FFFFFF"]:
        s = hx.strip().lstrip("#")
        if len(s) != 6:
            continue
        try:
            triples.append((int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)))
        except ValueError:
            continue
    if not triples:
        triples.append((255, 255, 255))
    try:
        pal = script.make_palette(triples)
    except Exception as e:
        return ScriptError(f"palette init failed: {e}")
    schema = list(script.meta.param_schema)
    merged = merge_with_schema(schema, params or {})
    if not script.has_render:
        # Tick-only scripts aren't probed cell-by-cell; we still verify
        # the entry point exists. Scripts with neither render nor tick
        # have already failed compile so we won't reach here.
        return None
    # Probe across multiple t / i / n combinations. Any healthy effect
    # produces non-zero color at SOME point in this grid — a chase that
    # blanks a slot still has other slots lit, a strobe still flashes
    # at multiple of the probed t values, etc. A script that returns
    # all zeros across all probes is almost certainly broken (wrong
    # ctx field names, units mismatch, missing brightness scaling).
    saw_color = False
    last_result: Optional[dict[str, Any]] = None
    for t in (0.0, 0.25, 0.7, 1.5, 3.3):
        for i in range(n_probes := 8):
            ctx = script.new_table()
            ctx["t"] = float(t)
            ctx["i"] = i
            ctx["n"] = n_probes
            ctx["frame"] = int(t * 30)
            ctx["seed"] = 1
            ctx["palette"] = pal
            params_tbl = script.new_table()
            for k, v in merged.items():
                params_tbl[k] = v
            ctx["params"] = params_tbl
            slot_tbl = script.new_table()
            slot_tbl["light_id"] = i + 1
            slot_tbl["zone_id"] = None
            ctx["slot"] = slot_tbl
            try:
                result = script.render_slot(ctx)
            except ScriptError as e:
                return e
            except Exception as e:  # pragma: no cover - defensive
                return ScriptError(str(e))
            if isinstance(result, dict):
                last_result = result
                if not result.get("active", False):
                    continue
                rr = int(result.get("r", 0) or 0)
                gg = int(result.get("g", 0) or 0)
                bb = int(result.get("b", 0) or 0)
                bri = result.get("brightness", 1.0)
                if (rr + gg + bb) > 0 and (
                    not isinstance(bri, (int, float)) or bri > 0
                ):
                    saw_color = True

    if not saw_color:
        # Tailor the diagnostic to the most common Claude/LLM failure:
        # reading from nonexistent ctx fields (``ctx.time_s`` instead
        # of ``ctx.t``) so every slot returns 0,0,0 while still passing
        # the no-exception smoke. Surface enough hints that the refiner
        # can produce a meaningful fix.
        sample = ""
        if isinstance(last_result, dict):
            sample = (
                f" Last sample: r={last_result.get('r')}, "
                f"g={last_result.get('g')}, b={last_result.get('b')}, "
                f"brightness={last_result.get('brightness')}, "
                f"active={last_result.get('active')}."
            )
        return ScriptError(
            "render() always returned zero color and/or zero "
            "brightness across the smoke probe. The script likely "
            "reads from a ctx field that doesn't exist (the correct "
            "names are ctx.t / ctx.i / ctx.n — NOT ctx.time_s, "
            "ctx.index, ctx.count) or returns r/g/b as 0..1 floats "
            "instead of 0..255 integers. Be explicit: ``return { r = "
            "INT(0..255), g = INT(0..255), b = INT(0..255), "
            "brightness = FLOAT(0..1), active = true }``." + sample
        )
    return None
