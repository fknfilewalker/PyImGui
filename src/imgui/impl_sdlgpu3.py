# The module is compiled into _imgui; this file pairs impl_sdlgpu3.pyi with a source for type checkers
import sys

from ._imgui import impl_sdlgpu3

sys.modules[__name__] = impl_sdlgpu3
