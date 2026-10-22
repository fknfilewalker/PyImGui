# The module is compiled into _imgui; this file pairs impl_dx12.pyi with a source for type checkers
import sys

from ._imgui import impl_dx12

sys.modules[__name__] = impl_dx12
