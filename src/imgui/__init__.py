"""
Python bindings for Dear ImGui, with its SDL3 platform backend (`imgui.impl_sdl3`)
and its SDL_GPU, OpenGL 3 and Vulkan renderer backends (`imgui.impl_sdlgpu3`,
`imgui.impl_opengl3`, `imgui.impl_vulkan`), generated with litgen. On Windows, also
its Direct3D 12 renderer backend (`imgui.impl_dx12`); on macOS, its Metal and Metal 4
renderer backends (`imgui.impl_metal`, `imgui.impl_metal4`).
"""

import ctypes
import sys

# The extension does not link SDL3: its SDL functions are looked up when it is
# loaded, in the SDL3 library that pysdl3 ships, whose windows and events the
# backends are handed. pysdl3 loads that library privately (ctypes' RTLD_LOCAL),
# which hides it from the lookup; opening it again with RTLD_GLOBAL exposes it.
try:
    import sdl3
except ImportError as e:
    raise ImportError("imgui needs pysdl3, whose SDL3 library its backends call: pip install pysdl3") from e
ctypes.CDLL(sdl3.SDL_GET_BINARY(sdl3.SDL_BINARY)._name, mode=ctypes.RTLD_GLOBAL)

from ._imgui import *  # noqa: E402, F403
# The submodules are compiled into _imgui; implot.py etc. register them as modules
# of their own, so that `import imgui.implot` works
from . import imguizmo, impl_opengl3, impl_sdl3, impl_sdlgpu3, impl_vulkan, implot  # noqa: E402

if sys.platform == "win32":
    from . import impl_dx12  # noqa: E402
if sys.platform == "darwin":
    from . import impl_metal, impl_metal4  # noqa: E402
