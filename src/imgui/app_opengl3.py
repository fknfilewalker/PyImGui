"""
Runs a Dear ImGui GUI in a window of its own, as `imgui.app` does, but draws it with
OpenGL instead of SDL_GPU: SDL3 creates the window and its GL context and delivers
its events, and imgui_impl_opengl3 draws the GUI into the window.

    import imgui
    from imgui import app_opengl3

    def gui():
        imgui.text("Hello, world!")

    app_opengl3.run(gui, window_title="Hello")

The GL context is current while `gui_function` runs, so a GUI can create and fill
textures with any GL binding (PyOpenGL, moderngl, ...) and show them with
`imgui.image(imgui.ImTextureRef(texture))`, where `texture` is the GLuint name of the
texture. `context()` has the window and the GL context.
"""

import ctypes
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

import sdl3

import imgui
from imgui import impl_opengl3, impl_sdl3, implot

# The GL functions that run() calls itself, from the GL context's own library.
# GL 1.0 functions: every context has them.
_GL_COLOR_BUFFER_BIT = 0x00004000
_GL_FUNCTIONS = {
    "glViewport": ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int),
    "glClearColor": ctypes.CFUNCTYPE(None, ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float),
    "glClear": ctypes.CFUNCTYPE(None, ctypes.c_uint),
}


@dataclass
class Context:
    """The window of `run()` and the GL context that draws into it"""

    window: Any
    """The SDL_Window"""
    gl_context: Any
    """The SDL_GLContext, current on the thread of `run()`"""
    app_shall_exit: bool = False
    """Set to close the window once the current frame is done"""


_context: Optional[Context] = None


def context() -> Context:
    """The window and the GL context of the running `run()`"""
    if _context is None:
        raise RuntimeError("imgui.app_opengl3.run() is not running")
    return _context


def run(
    gui_function: Callable[[], None],
    window_title: str = "",
    window_size: Optional[tuple[int, int]] = None,
    fps_idle: float = 9.0,
    *,
    gl_version: tuple[int, int] = (3, 2),
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
    - `gl_version`: the (major, minor) version of the core profile context to
      create, 3.2 or newer (macOS offers 3.2 to 4.1, as forward-compatible contexts).
    - `vsync`: whether frames wait for the display.
    - `clear_color`: the RGBA background, by default the one of windows in the style.
    - `post_init`: called once the window, the backends and the contexts of Dear
      ImGui and ImPlot are set up, before the first frame, with the GL context
      current: to load fonts or create textures, say.
    - `pre_exit`: called once the last frame is done, or the GUI raised, while the
      GL context is still current: to delete the textures created in it.
    """
    global _context
    if not sdl3.SDL_Init(sdl3.SDL_INIT_VIDEO | sdl3.SDL_INIT_GAMEPAD):
        raise RuntimeError(f"SDL_Init(): {sdl3.SDL_GetError().decode()}")
    try:
        # A core profile context, as in Dear ImGui's example_sdl3_opengl3. macOS only
        # offers versions above 2.1 as forward-compatible core profile contexts.
        sdl3.SDL_GL_SetAttribute(sdl3.SDL_GL_CONTEXT_FLAGS, sdl3.SDL_GL_CONTEXT_FORWARD_COMPATIBLE_FLAG)
        sdl3.SDL_GL_SetAttribute(sdl3.SDL_GL_CONTEXT_PROFILE_MASK, sdl3.SDL_GL_CONTEXT_PROFILE_CORE)
        sdl3.SDL_GL_SetAttribute(sdl3.SDL_GL_CONTEXT_MAJOR_VERSION, gl_version[0])
        sdl3.SDL_GL_SetAttribute(sdl3.SDL_GL_CONTEXT_MINOR_VERSION, gl_version[1])
        sdl3.SDL_GL_SetAttribute(sdl3.SDL_GL_DOUBLEBUFFER, 1)
        sdl3.SDL_GL_SetAttribute(sdl3.SDL_GL_DEPTH_SIZE, 24)
        sdl3.SDL_GL_SetAttribute(sdl3.SDL_GL_STENCIL_SIZE, 8)

        scale = sdl3.SDL_GetDisplayContentScale(sdl3.SDL_GetPrimaryDisplay())
        width, height = window_size or (1280, 720)
        window = sdl3.SDL_CreateWindow(window_title.encode(), int(width * scale), int(height * scale),
                                       sdl3.SDL_WINDOW_OPENGL | sdl3.SDL_WINDOW_RESIZABLE | sdl3.SDL_WINDOW_HIDDEN
                                       | sdl3.SDL_WINDOW_HIGH_PIXEL_DENSITY)
        gl_context = window and sdl3.SDL_GL_CreateContext(window)
        if not gl_context:
            error = sdl3.SDL_GetError().decode()
            if window:
                sdl3.SDL_DestroyWindow(window)
            raise RuntimeError(f"Could not open a window with an OpenGL {gl_version[0]}.{gl_version[1]} context: {error}")
        try:
            sdl3.SDL_GL_MakeCurrent(window, gl_context)
            # Without vsync, frames are shown as soon as they are drawn
            sdl3.SDL_GL_SetSwapInterval(1 if vsync else 0)
            gl = {name: ctypes.cast(sdl3.SDL_GL_GetProcAddress(name.encode()), prototype)
                  for name, prototype in _GL_FUNCTIONS.items()}
            _context = Context(window, gl_context)

            sdl3.SDL_SetWindowPosition(window, sdl3.SDL_WINDOWPOS_CENTERED, sdl3.SDL_WINDOWPOS_CENTERED)
            sdl3.SDL_ShowWindow(window)
            imgui.create_context()
            implot.create_context()
            style = imgui.get_style()
            style.scale_all_sizes(scale)
            style.font_scale_dpi = scale
            impl_sdl3.init_for_opengl(window, gl_context)
            # The GLSL version that suits the context: the backend asks it for its version
            impl_opengl3.init()
            try:
                if post_init:
                    post_init()
                _main_loop(_context, gui_function, fps_idle, clear_color, gl)
            finally:
                if pre_exit:
                    pre_exit()
                impl_opengl3.shutdown()
                impl_sdl3.shutdown()
                implot.destroy_context()
                imgui.destroy_context()
        finally:
            sdl3.SDL_GL_DestroyContext(gl_context)
            sdl3.SDL_DestroyWindow(window)
    finally:
        _context = None
        sdl3.SDL_Quit()


def _main_loop(context, gui_function, fps_idle, clear_color, gl):
    """The frames of `run()`, as in Dear ImGui's example_sdl3_opengl3"""
    window_id = sdl3.SDL_GetWindowID(context.window)
    idle_timeout = int(1000 / fps_idle) if fps_idle > 0 else 0
    width, height = ctypes.c_int(), ctypes.c_int()

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

        impl_opengl3.new_frame()
        impl_sdl3.new_frame()
        imgui.new_frame()
        gui_function()
        imgui.render()
        draw_data = imgui.get_draw_data()

        # The GUI may have made another context current, or changed the viewport
        sdl3.SDL_GL_MakeCurrent(context.window, context.gl_context)
        sdl3.SDL_GetWindowSizeInPixels(context.window, ctypes.byref(width), ctypes.byref(height))
        gl["glViewport"](0, 0, width.value, height.value)
        r, g, b, a = clear_color or imgui.get_style_color_vec4(imgui.Col_.window_bg)
        gl["glClearColor"](r * a, g * a, b * a, a)
        gl["glClear"](_GL_COLOR_BUFFER_BIT)
        if draw_data.display_size.x > 0 and draw_data.display_size.y > 0:
            impl_opengl3.render_draw_data(draw_data)
        sdl3.SDL_GL_SwapWindow(context.window)
