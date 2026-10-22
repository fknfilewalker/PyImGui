# The module is compiled into _imgui; this file pairs impl_metal.pyi with a source for type checkers
import sys

from ._imgui import impl_metal

sys.modules[__name__] = impl_metal
