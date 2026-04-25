"""Lua-driven effect engine.

Public surface:

* :class:`LuaScript` - compiled, sandboxed effect script.
* :func:`compile_script` - parse + compile a Lua source string.
* :func:`builtin_sources` - mapping of builtin script name -> Lua source.
* :class:`ScriptError` - raised on compile/runtime errors.
"""

from .runtime import (
    LuaScript,
    LuaScriptMeta,
    ScriptError,
    compile_script,
    smoke_test_source,
)
from .registry import builtin_sources, get_builtin_source

__all__ = [
    "LuaScript",
    "LuaScriptMeta",
    "ScriptError",
    "compile_script",
    "smoke_test_source",
    "builtin_sources",
    "get_builtin_source",
]
