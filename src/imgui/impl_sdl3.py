# The module is compiled into _imgui; this file pairs impl_sdl3.pyi with a source for type checkers
import sys

from ._imgui import impl_sdl3

sys.modules[__name__] = impl_sdl3
