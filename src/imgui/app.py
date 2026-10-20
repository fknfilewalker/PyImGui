"""
Runs a Dear ImGui GUI in a window of its own: SDL3 creates the window and
delivers its events, and SDL_GPU draws the GUI, on Metal, Vulkan or Direct3D 12.

    import imgui
    from imgui import app

    def gui():
        imgui.text("Hello, world!")

    app.run(gui, window_title="Hello", backends=(app.Backend.vulkan, app.Backend.metal))

A GUI that draws more than widgets, into a texture for `imgui.image()` for
example, finds the window and the GPU device in `app.context()`.
"""

import ctypes
import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional, Union

import sdl3

import imgui
from imgui import impl_sdl3, impl_sdlgpu3, implot


class Backend(enum.Enum):
    """The drivers of SDL_GPU, by their names in SDL"""

    metal = "metal"
    vulkan = "vulkan"
    direct3d12 = "direct3d12"


# The formats that imgui_impl_sdlgpu3 ships its shaders in
SHADER_FORMATS = (sdl3.SDL_GPU_SHADERFORMAT_SPIRV | sdl3.SDL_GPU_SHADERFORMAT_DXIL
                  | sdl3.SDL_GPU_SHADERFORMAT_MSL | sdl3.SDL_GPU_SHADERFORMAT_METALLIB)


@dataclass
class Context:
    """The window of `run()` and the GPU device that draws into it"""

    window: Any
    """The SDL_Window"""
    gpu_device: Any
    """The SDL_GPUDevice"""
    backend: Backend
    """The SDL_GPU driver in use"""
    app_shall_exit: bool = False
    """Set to close the window once the current frame is done"""


_context: Optional[Context] = None


def context() -> Context:
    """The window and the GPU device of the running `run()`"""
    if _context is None:
        raise RuntimeError("imgui.app.run() is not running")
    return _context


def run(
    gui_function: Callable[[], None],
    window_title: str = "",
    window_size: Optional[tuple[int, int]] = None,
    fps_idle: float = 9.0,
    *,
    backends: Union[Backend, Sequence[Backend], None] = None,
    vsync: bool = True,
    clear_color: Optional[Sequence[float]] = None,
    post_init: Optional[Callable[[], None]] = None,
    pre_exit: Optional[Callable[[], None]] = None,
) -> None:
    """
    Opens a window and calls `gui_function` to build the GUI of every frame, until
    the window is closed or `context().app_shall_exit` is set.

    - `window_size`: in points, scaled for the display; 1280 x 720 by default.
    - `fps_idle`: the frame rate while there is no input, to spare the CPU; 0 draws
      frames continuously.
    - `backends`: the SDL_GPU drivers to try, in order of preference; by default,
      the one that SDL prefers.
    - `vsync`: whether frames wait for the display. Without, a frame for which the
      display has no image free is built but not shown.
    - `clear_color`: the RGBA background, by default the one of windows in the style.
    - `post_init`: called once the window, the backends and the contexts of Dear
      ImGui and ImPlot are set up, before the first frame: to load fonts or create
      textures, say.
    - `pre_exit`: called once the last frame is done, or the GUI raised, while
      the GPU device still exists: to release the textures created on it.
    """
    global _context
    # None stands for the driver that SDL prefers. Backend() also accepts the names
    # ("metal"), and raises a ValueError for anything else.
    preferred = [backends] if isinstance(backends, (Backend, str)) else backends or [None]
    drivers = [Backend(driver) if driver is not None else None for driver in preferred]

    if not sdl3.SDL_Init(sdl3.SDL_INIT_VIDEO | sdl3.SDL_INIT_GAMEPAD):
        raise RuntimeError(f"SDL_Init(): {sdl3.SDL_GetError().decode()}")
    try:
        for driver in drivers:
            device = sdl3.SDL_CreateGPUDevice(SHADER_FORMATS, False, driver.value.encode() if driver else None)
            if device:
                break
        else:
            names = [driver.name for driver in drivers if driver]
            raise RuntimeError(f"None of the backends {names} is available: {sdl3.SDL_GetError().decode()}")

        scale = sdl3.SDL_GetDisplayContentScale(sdl3.SDL_GetPrimaryDisplay())
        width, height = window_size or (1280, 720)
        window = sdl3.SDL_CreateWindow(window_title.encode(), int(width * scale), int(height * scale),
                                       sdl3.SDL_WINDOW_RESIZABLE | sdl3.SDL_WINDOW_HIDDEN
                                       | sdl3.SDL_WINDOW_HIGH_PIXEL_DENSITY)
        if not window or not sdl3.SDL_ClaimWindowForGPUDevice(device, window):
            error = sdl3.SDL_GetError().decode()
            if window:
                sdl3.SDL_DestroyWindow(window)
            sdl3.SDL_DestroyGPUDevice(device)
            raise RuntimeError(f"Could not open a window: {error}")

        # Without vsync, the first mode that the window supports (SDL_GPU only promises vsync)
        modes = () if vsync else (sdl3.SDL_GPU_PRESENTMODE_IMMEDIATE, sdl3.SDL_GPU_PRESENTMODE_MAILBOX)
        mode = next((mode for mode in modes if sdl3.SDL_WindowSupportsGPUPresentMode(device, window, mode)),
                    sdl3.SDL_GPU_PRESENTMODE_VSYNC)
        sdl3.SDL_SetGPUSwapchainParameters(device, window, sdl3.SDL_GPU_SWAPCHAINCOMPOSITION_SDR, mode)
        _context = Context(window, device, Backend(sdl3.SDL_GetGPUDeviceDriver(device).decode()))

        sdl3.SDL_SetWindowPosition(window, sdl3.SDL_WINDOWPOS_CENTERED, sdl3.SDL_WINDOWPOS_CENTERED)
        sdl3.SDL_ShowWindow(window)
        imgui.create_context()
        implot.create_context()
        style = imgui.get_style()
        style.scale_all_sizes(scale)
        style.font_scale_dpi = scale
        impl_sdl3.init_for_sdl_gpu(window)
        info = impl_sdlgpu3.InitInfo()
        info.device = device
        info.color_target_format = sdl3.SDL_GetGPUSwapchainTextureFormat(device, window)
        info.msaa_samples = sdl3.SDL_GPU_SAMPLECOUNT_1
        impl_sdlgpu3.init(info)
        try:
            if post_init:
                post_init()
            # Without fonts of its own, Dear ImGui picks its bitmap font for text that is
            # small in points, which a high-density display (for which macOS reports a
            # content scale of 1) magnifies into blocks. Its vector font is rasterized at
            # the density of the display.
            if len(imgui.get_io().fonts.fonts) == 0:
                imgui.get_io().fonts.add_font_default_vector()
            _main_loop(_context, gui_function, fps_idle, vsync, clear_color)
        finally:
            sdl3.SDL_WaitForGPUIdle(device)
            if pre_exit:
                pre_exit()
            impl_sdl3.shutdown()
            impl_sdlgpu3.shutdown()
            implot.destroy_context()
            imgui.destroy_context()
            sdl3.SDL_ReleaseWindowFromGPUDevice(device, window)
            sdl3.SDL_DestroyGPUDevice(device)
            sdl3.SDL_DestroyWindow(window)
    finally:
        _context = None
        sdl3.SDL_Quit()


def _main_loop(context, gui_function, fps_idle, vsync, clear_color):
    """The frames of `run()`, as in Dear ImGui's example_sdl3_sdlgpu3"""
    window_id = sdl3.SDL_GetWindowID(context.window)
    idle_timeout = int(1000 / fps_idle) if fps_idle > 0 else 0
    # Waiting for a swapchain texture would bind the frame rate to the display
    acquire = sdl3.SDL_WaitAndAcquireGPUSwapchainTexture if vsync else sdl3.SDL_AcquireGPUSwapchainTexture
    swapchain_texture = ctypes.POINTER(sdl3.SDL_GPUTexture)()

    def process(event):
        impl_sdl3.process_event(event)
        if event.type == sdl3.SDL_EVENT_QUIT or (event.type == sdl3.SDL_EVENT_WINDOW_CLOSE_REQUESTED
                                                 and event.window.windowID == window_id):
            context.app_shall_exit = True

    event = sdl3.SDL_Event()
    while not context.app_shall_exit:
        # Without input, wait for some before building the next frame
        if idle_timeout and sdl3.SDL_WaitEventTimeout(ctypes.byref(event), idle_timeout):
            process(event)
        while sdl3.SDL_PollEvent(ctypes.byref(event)):
            process(event)
        if context.app_shall_exit:
            break
        if sdl3.SDL_GetWindowFlags(context.window) & sdl3.SDL_WINDOW_MINIMIZED:
            sdl3.SDL_Delay(10)
            continue

        impl_sdlgpu3.new_frame()
        impl_sdl3.new_frame()
        imgui.new_frame()
        gui_function()
        imgui.render()
        draw_data = imgui.get_draw_data()

        command_buffer = sdl3.SDL_AcquireGPUCommandBuffer(context.gpu_device)
        acquire(command_buffer, context.window, ctypes.byref(swapchain_texture), None, None)
        if swapchain_texture and draw_data.display_size.x > 0 and draw_data.display_size.y > 0:
            # Uploads the vertices and indices, which has to precede the render pass
            impl_sdlgpu3.prepare_draw_data(draw_data, command_buffer)
            r, g, b, a = clear_color or imgui.get_style_color_vec4(imgui.Col_.window_bg)
            target = sdl3.SDL_GPUColorTargetInfo()
            target.texture = swapchain_texture
            target.clear_color = sdl3.SDL_FColor(r * a, g * a, b * a, a)
            target.load_op = sdl3.SDL_GPU_LOADOP_CLEAR
            target.store_op = sdl3.SDL_GPU_STOREOP_STORE
            render_pass = sdl3.SDL_BeginGPURenderPass(command_buffer, ctypes.byref(target), 1, None)
            impl_sdlgpu3.render_draw_data(draw_data, command_buffer, render_pass)
            sdl3.SDL_EndGPURenderPass(render_pass)
        sdl3.SDL_SubmitGPUCommandBuffer(command_buffer)
