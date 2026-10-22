# The module is compiled into _imgui; this file pairs impl_opengl3.pyi with a source for type checkers
import sys

from ._imgui import impl_opengl3

sys.modules[__name__] = impl_opengl3
