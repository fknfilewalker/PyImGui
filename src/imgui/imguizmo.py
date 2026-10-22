# The module is compiled into _imgui; this file pairs imguizmo.pyi with a source for type checkers
import sys

from ._imgui import imguizmo

sys.modules[__name__] = imguizmo
