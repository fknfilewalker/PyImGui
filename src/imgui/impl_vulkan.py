# The module is compiled into _imgui; this file pairs impl_vulkan.pyi with a source for type checkers
import sys

from ._imgui import impl_vulkan

sys.modules[__name__] = impl_vulkan
