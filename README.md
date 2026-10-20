# imgui

Python bindings for [Dear ImGui](https://github.com/ocornut/imgui) (v1.93.0, docking branch), its
SDL3 platform backend, its SDL_GPU, OpenGL 3 and Vulkan renderer backends, on Windows its Direct3D 12
renderer backend, on macOS its Metal and Metal 4 renderer backends, [ImPlot](https://github.com/epezent/implot) and
[ImGuizmo](https://github.com/CedricGuillemet/ImGuizmo). The bindings are generated
with [litgen](https://pthom.github.io/litgen/) from the unmodified upstream headers and built with
nanobind. Windows, GPU devices and events come from
[pysdl3](https://github.com/Aermoss/PySDL3).

```python
import imgui
from imgui import impl_sdl3, impl_sdlgpu3

imgui.create_context()
impl_sdl3.init_for_sdl_gpu(window)             # pysdl3's window and GPU device
impl_sdlgpu3.init(impl_sdlgpu3.InitInfo(device=gpu_device, color_target_format=swapchain_format))
...
impl_sdl3.process_event(event)                 # pysdl3's SDL_Event
impl_sdlgpu3.new_frame()
impl_sdl3.new_frame()
imgui.new_frame()
if imgui.begin("Hello")[0]:
    changed, value = imgui.slider_float("Value", value, 0.0, 1.0)
imgui.end()
imgui.render()
draw_data = imgui.get_draw_data()
impl_sdlgpu3.prepare_draw_data(draw_data, command_buffer)  # before the render pass
impl_sdlgpu3.render_draw_data(draw_data, command_buffer, render_pass)
```

The full program is [src/imgui/app.py](src/imgui/app.py): `imgui.app.run()` sets all of this up
on Metal, Vulkan or Direct3D 12, and calls a function to build the GUI of every frame:

```python
import imgui
from imgui import app

def gui():
    imgui.text(f"Drawn with {app.context().backend.name}")

app.run(gui, window_title="Hello", window_size=(1280, 720), fps_idle=0,
        backends=(app.Backend.vulkan, app.Backend.metal))  # by default, SDL's choice
```

`backends` lists the SDL_GPU drivers in order of preference. `app.context()` holds the window
and the GPU device, and `app.context().app_shall_exit = True` closes the window.

### OpenGL 3

`imgui.impl_opengl3` binds `imgui_impl_opengl3.h`, whose textures are GL texture names:
`imgui.image(imgui.ImTextureRef(texture), size)` takes the `GLuint`. The extension does not link
OpenGL: the backend opens the system's OpenGL library when `impl_opengl3.init()` runs, and calls it
in the GL context that is current. [src/imgui/app_opengl3.py](src/imgui/app_opengl3.py) is `run()` for
SDL3 and OpenGL, which creates a core profile context (3.2 by default, `gl_version=` for another)
and keeps it current while the GUI is built, so that any GL binding (PyOpenGL, moderngl...) can
draw into textures for it:

```python
from imgui import app_opengl3

def gui():
    imgui.image(imgui.ImTextureRef(texture), (512, 512))   # a GLuint of app_opengl3's context

app_opengl3.run(gui, window_title="Hello", post_init=create_textures)
```

`app_opengl3.context()` holds the window and the `SDL_GLContext`.

### Vulkan

`imgui.impl_vulkan` binds `imgui_impl_vulkan.h`. Its handles are ints (addresses), ctypes objects
or anything that `int()` converts (cffi's pointers, as the `vulkan` package has them), and
`impl_vulkan.InitInfo` and `impl_vulkan.PipelineInfo` take their fields as keyword arguments. Textures
are descriptor sets: `imgui.image(imgui.ImTextureRef(impl_vulkan.add_texture(view, layout)), size)`.
The extension does not link Vulkan: `impl_vulkan.init()` looks the backend's functions up through
the `vkGetInstanceProcAddr` of the Vulkan library that SDL3 loaded (for a window with
`SDL_WINDOW_VULKAN`), unless `impl_vulkan.load_functions()` was given another one.
[src/imgui/app_vulkan.py](src/imgui/app_vulkan.py) is `run()` for SDL3 and Vulkan 1.3, which it
calls through ctypes, and draws with dynamic rendering:

```python
from imgui import app_vulkan, impl_vulkan

def gui():
    context = app_vulkan.context()          # the window, VkInstance, VkDevice, VkQueue, ...
    context.keep_alive(texture)             # until the GPU is done with this frame
    imgui.image(imgui.ImTextureRef(descriptor_set), (512, 512))  # of impl_vulkan.add_texture()

app_vulkan.run(gui, window_title="Hello", device_extensions=["VK_KHR_external_memory_win32"])
```

`context()` holds the handles as ints, and the address of `vkGetInstanceProcAddr` to load further
functions with. `device_extensions` and `instance_extensions` enable extensions beyond the swapchain's,
e.g. to share memory with another API, and `validation=True` enables the Khronos validation layer.

### Direct3D 12 (Windows)

On Windows, `imgui.impl_dx12` binds `imgui_impl_dx12.h`. Its objects are ints (addresses) or ctypes
pointers (comtypes' as well), and descriptor handles are the ints of their `ptr`. The backend takes the
SRV descriptors of its textures from the application, through the Python functions
`srv_descriptor_alloc_fn` (returning a `(cpu_handle, gpu_handle)` pair) and `srv_descriptor_free_fn`
of `impl_dx12.InitInfo`. The texture of `imgui.image()` is the GPU handle of an SRV in the same
heap. [src/imgui/app_dx12.py](src/imgui/app_dx12.py) is `run()` for SDL3 and Direct3D 12, which
it calls through ctypes, and hands out the descriptors of its heap:

```python
from imgui import app_dx12

srv = {}

def post_init():
    srv["cpu"], srv["gpu"] = app_dx12.context().allocate_srv_descriptor()
    ...                                     # ID3D12Device::CreateShaderResourceView(texture, ..., srv["cpu"])

def gui():
    imgui.image(imgui.ImTextureRef(srv["gpu"]), (512, 512))

app_dx12.run(gui, window_title="Hello", post_init=post_init, device=device)  # by default, its own device
```

`context()` holds the window, the `ID3D12Device`, the `ID3D12CommandQueue` and the descriptor
heap, as ints, and `validation=True` enables the debug layer, whose messages are printed.

### Metal and Metal 4 (macOS)

On macOS, `imgui.impl_metal` and `imgui.impl_metal4` bind `imgui_impl_metal.h` and
`imgui_impl_metal4.h`. Their Metal objects are
[PyObjC](https://pyobjc.readthedocs.io)'s (the `metal` extra installs `pyobjc-framework-Metal`),
ctypes pointers or addresses; `texture_id(texture)` gives the `ImTextureID` of any `MTLTexture`.
[src/imgui/app_metal4.py](src/imgui/app_metal4.py) is `run()` for SDL3 and Metal 4, which also takes
the `MTLDevice` to draw with:

```python
from imgui import app_metal4, impl_metal4

def gui():
    context = app_metal4.context()          # the window, MTLDevice, MTL4CommandQueue, ...
    context.keep_alive(texture)             # until the GPU is done with this frame
    imgui.image(imgui.ImTextureRef(impl_metal4.texture_id(texture)), (512, 512))

app_metal4.run(gui, window_title="Hello", device=device)  # by default, the system's device
```

The GUI can so show textures that view the memory of another library on the same device, e.g. a
GPU array's buffer (`MTLBuffer.newTextureWithDescriptor:offset:bytesPerRow:`). Metal 4 queues do not
track hazards: the queue has to wait for the other library's work (`context().command_queue
.waitForEvent_value_()`), the memory has to stay alive while frames draw it (`keep_alive()`), and the
buffers behind views belong in `context().residency_set`. The backend adds every texture that it draws
to a residency set of its own, which retains it: `impl_metal4.forget_texture()` removes one that is
no longer drawn.

## Installing

PyPI has wheels for Python 3.12+ on Windows and Linux (glibc 2.34+), on x86-64 and ARM64, and on macOS 26+
on Apple silicon. Running needs pysdl3, which the `sdl3` extra installs (its SDL3 is what needs macOS 26).

```sh
pip install "PyImGui[sdl3]"    # or: uv pip install "PyImGui[sdl3]"
```

Building from source needs a C++17 compiler and CMake. CMake downloads Dear ImGui, ImPlot, ImGuizmo
and the headers of SDL3 and Vulkan, which are not built or linked; on Windows, it also downloads the SDL3 import
library to link against.

```sh
pip install ".[sdl3]"    # in the repository's root
```

On macOS, the Metal backends need PyObjC's Metal bindings, which the `metal` extra adds:
`pip install ".[metal]"`.

## The Python API

The module follows these conventions:

- `ImGui::BeginTable()` is `imgui.begin_table()`, `ImGuiWindowFlags_NoResize` is
  `imgui.WindowFlags_.no_resize`, `ImGuiIO` is `imgui.IO`.
- Functions that modify their pointer arguments return the new values instead:
  `changed, v = imgui.checkbox("On", v)`, `visible, p_open = imgui.begin("Window", p_open)`.
- Tuples and lists work wherever an `ImVec2` or `ImVec4` is expected, and `ImVec2`/`ImVec4`
  support arithmetic, indexing and unpacking.
- Strings are Python strings, `imgui.input_text("Name", name)` included.
- `io.ini_filename` sets or (with `None`) disables the settings file.
- The colors of a style are indexed by its `Col_` enum: `style.colors[imgui.Col_.text] = (1, 1, 1, 1)`.
  The same goes for `implot.Style` and `imguizmo.Style`.
- The backends are the submodules `imgui.impl_sdl3`, `imgui.impl_sdlgpu3`, `imgui.impl_opengl3`,
  `imgui.impl_vulkan` and, on Windows, `imgui.impl_dx12`:
  `ImGui_ImplSDL3_ProcessEvent()` is `imgui.impl_sdl3.process_event()`. ImPlot and ImGuizmo are
  `imgui.implot` and `imgui.imguizmo` (see below).

SDL objects are passed as pysdl3 creates them: its pointers (`SDL_Window*`, `SDL_GPUDevice*`,
...), its `SDL_Event`, `ctypes.byref()` of one, or plain addresses. SDL's enums are ints, as
in pysdl3.

Textures are passed to `imgui.image()` and similar functions as `imgui.ImTextureRef(id)`. The `id`
is the address of the `SDL_GPUTexture` (`ctypes.cast(texture, ctypes.c_void_p).value`).

Dear ImGui's assertions raise a `RuntimeError`. This covers the API misuse it checks for, such as a
missing `end()`. To recover from those errors in the running app instead, set
`io.config_error_recovery_enable_assert = False`.

The extension does not link SDL3 (except on Windows). Its SDL functions are looked up when
`import imgui` loads it, in the SDL3 library that pysdl3 ships, which `import imgui` imports and
exposes first. The backends and pysdl3 thus share one SDL3, so the windows and events that pysdl3
creates are the backends' own. That library has to be SDL 3.4.16 or newer, the release whose headers
the extension is compiled against (`SDL3_VERSION` in [CMakeLists.txt](CMakeLists.txt)).

Not bound: C callbacks (`InputText` and size-constraint callbacks, `ImDrawList.add_callback`,
ImPlot's getters, formatters and transforms), drag-and-drop payloads, the memory allocators, the
`va_list` variants of the text functions, the manual gamepad mode of the SDL3 backend, the per-point
color and size arrays of `implot.Spec`, the row and column ratios of `implot.begin_subplots()`,
ImGuizmo's other widgets (ImSequencer, ImCurveEdit...), and the internal headers.

## ImPlot and ImGuizmo

```python
import math
import numpy as np
import imgui
from imgui import app, imguizmo, implot

xs = np.linspace(0, 10, 200)
model = np.eye(4, dtype=np.float32)             # modified in place by the gizmo, hence float32
view = np.eye(4, dtype=np.float32)
view[2, 3] = -5                                 # camera 5 units back, looking down -z
f, near, far = 1 / math.tan(math.radians(22.5)), 0.1, 100.0
projection = np.array([[f / 16 * 9, 0, 0, 0], [0, f, 0, 0],   # OpenGL's perspective, 16:9
                       [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
                       [0, 0, -1, 0]])

def gui():
    imguizmo.begin_frame()
    imgui.begin("Plot")
    if implot.begin_plot("Waves"):
        implot.plot_line("sin", xs, np.sin(xs), implot.Spec(line_weight=2, marker=implot.Marker_.circle))
        implot.plot_bars("bars", np.arange(5))
        implot.end_plot()
    imgui.end()
    size = imgui.get_io().display_size
    imguizmo.set_rect(0, 0, size.x, size.y)
    imguizmo.manipulate(view, projection, imguizmo.OPERATION.translate | imguizmo.OPERATION.rotate,
                        imguizmo.MODE.world, model)

app.run(gui, window_size=(1280, 720))
```

- ImPlot needs a context of its own, which `app.run()` creates. Elsewhere, call
  `implot.create_context()` after `imgui.create_context()`.
- The plotting functions take numpy arrays (or any array with DLPack or the buffer protocol) of any
  integer or floating-point type. The arrays of one call share a type and are 1D and contiguous,
  except for `plot_heatmap()` and `plot_bar_groups()`, which take a 2D array. `implot.Spec` holds
  the style of an item, and takes its fields as keyword arguments. `ImPlotPoint` is
  `implot.Point`, for which tuples work as well.
- `imguizmo.begin_frame()` starts each frame, after `imgui.new_frame()`. Matrices are 4x4 arrays
  indexed `[row, column]`, whatever their memory order: the translation of a model matrix is
  `matrix[:3, 3]`, and vectors are multiplied from the right, as in OpenGL. The functions that
  modify a matrix (`manipulate()`, `view_manipulate()`, `recompose_matrix_from_components()`) write
  it back into the array, which must be float32. The others take any numeric type.

## Layout and regenerating the bindings

- `CMakeLists.txt` pins the Dear ImGui release (`IMGUI_GIT_TAG`), the ImPlot and ImGuizmo commits
  (`IMPLOT_GIT_TAG`, `IMGUIZMO_GIT_TAG`) and the Vulkan headers (`VULKAN_HEADERS_GIT_TAG`), and
  builds the single extension `imgui._imgui`, which holds them, the backends and all the bindings. They have to live in one extension because Dear
  ImGui keeps its state in globals.
- `.github/workflows/wheels.yml` builds the wheels with cibuildwheel (options in `pyproject.toml`),
  and uploads them to PyPI when a GitHub release is published. Bump `version` in `pyproject.toml`
  first: the release tag has to match it (`v0.2.0` for `0.2.0`).
- `generate.py` holds the litgen options, and writes the code between the litgen markers of
  `src/bindings/*.cpp` and `src/imgui/*.pyi`. The code outside those markers is written by hand:
  the module definition, the Metal backends' bindings (`imgui_impl_metal_bindings.mm`, whose
  Objective-C headers litgen cannot parse), the Vulkan and Direct3D 12 backends' bindings
  (`imgui_impl_vulkan_bindings.cpp`, `imgui_impl_dx12_bindings.cpp`, whose structs hold native
  structs and C callbacks), the SDL casters in `sdl3_casters.h`, the casters of native GPU handles
  in `native_casters.h`, the opaque `Context` handles, the
  `IM_ASSERT` of `imgui_config.h`, the ImGuizmo functions that take matrices, and the ImPlot
  functions that take 2D arrays or labels.

The generated code is checked in, so building does not need litgen. After changing the options or
a pinned release, regenerate it with:

```sh
uv run generate.py    # or: pip install litgen && python generate.py
```

## License

MIT ([LICENSE](LICENSE)); the code that litgen generates is not subject to litgen's GPL. The wheels
also contain Dear ImGui with its fonts, ImPlot, ImGuizmo and nanobind, whose licenses are in
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).
