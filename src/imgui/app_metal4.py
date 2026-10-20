"""
Runs a Dear ImGui GUI in a window of its own, as `imgui.app` does, but draws it with
Metal 4 instead of SDL_GPU: SDL3 creates the window and delivers its events, and
imgui_impl_metal4 draws the GUI into the window's CAMetalLayer. Needs PyObjC's
Metal bindings (pip install pyobjc-framework-Metal).

    import imgui
    from imgui import app_metal4

    def gui():
        imgui.text("Hello, world!")

    app_metal4.run(gui, window_title="Hello")

Unlike SDL_GPU, Metal hands out its objects: `context()` has the MTLDevice and the
MTL4CommandQueue, so a GUI can show any MTLTexture of the device with
`imgui.image(imgui.ImTextureRef(impl_metal4.texture_id(texture)))`, including ones
that view memory of other libraries (e.g. a buffer of a GPU array). The queue does
not track hazards: work that another queue does on such memory has to be awaited
(`command_queue.waitForEvent_value_()`), and the memory has to stay alive while the
frames in flight draw it (`Context.keep_alive()`).
"""

import ctypes
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

import Metal
import objc
import sdl3

import imgui
from imgui import impl_metal4, impl_sdl3, implot

FRAMES_IN_FLIGHT = 3
"""The frames that the CPU prepares while the GPU still draws earlier ones"""


@dataclass
class Context:
    """The window of `run()` and the Metal objects that draw into it"""

    window: Any
    """The SDL_Window"""
    device: Any
    """The id<MTLDevice>"""
    command_queue: Any
    """The id<MTL4CommandQueue> that draws the frames"""
    layer: Any
    """The CAMetalLayer of the window"""
    residency_set: Any
    """An id<MTLResidencySet> of the queue, for resources that the textures shown
    need beyond themselves, e.g. the buffers that textures view. (The backend makes
    the textures themselves resident as it draws them.)"""
    frame: int = 0
    """The number of the current frame, which runs in slot `frame % FRAMES_IN_FLIGHT`"""
    app_shall_exit: bool = False
    """Set to close the window once the current frame is done"""
    _kept: list = field(default_factory=lambda: [[] for _ in range(FRAMES_IN_FLIGHT)])

    def keep_alive(self, obj: Any) -> None:
        """Holds a reference to `obj` until the GPU is done with the current frame"""
        self._kept[self.frame % FRAMES_IN_FLIGHT].append(obj)


_context: Optional[Context] = None


def context() -> Context:
    """The window and the Metal objects of the running `run()`"""
    if _context is None:
        raise RuntimeError("imgui.app_metal4.run() is not running")
    return _context


def run(
    gui_function: Callable[[], None],
    window_title: str = "",
    window_size: Optional[tuple[int, int]] = None,
    fps_idle: float = 9.0,
    *,
    device: Any = None,
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
    - `device`: the MTLDevice to draw with (a PyObjC object or an address), e.g. the
      one of another library whose textures the GUI shows; by default, the system's.
    - `vsync`: whether frames wait for the display.
    - `clear_color`: the RGBA background, by default the one of windows in the style.
    - `post_init`: called once the window, the backends and the contexts of Dear
      ImGui and ImPlot are set up, before the first frame: to load fonts or create
      textures, say.
    - `pre_exit`: called once the last frame is done, or the GUI raised, while
      the GPU is idle: to release the resources that the GUI showed.
    """
    global _context
    if isinstance(device, int):
        device = objc.objc_object(c_void_p=device)
    device = device or Metal.MTLCreateSystemDefaultDevice()
    if device is None or not device.respondsToSelector_(b"newMTL4CommandQueue"):
        raise RuntimeError("Metal 4 is not available: it needs Apple silicon")

    if not sdl3.SDL_Init(sdl3.SDL_INIT_VIDEO | sdl3.SDL_INIT_GAMEPAD):
        raise RuntimeError(f"SDL_Init(): {sdl3.SDL_GetError().decode()}")
    try:
        scale = sdl3.SDL_GetDisplayContentScale(sdl3.SDL_GetPrimaryDisplay())
        width, height = window_size or (1280, 720)
        window = sdl3.SDL_CreateWindow(window_title.encode(), int(width * scale), int(height * scale),
                                       sdl3.SDL_WINDOW_METAL | sdl3.SDL_WINDOW_RESIZABLE | sdl3.SDL_WINDOW_HIDDEN
                                       | sdl3.SDL_WINDOW_HIGH_PIXEL_DENSITY)
        view = window and sdl3.SDL_Metal_CreateView(window)
        if not view:
            error = sdl3.SDL_GetError().decode()
            if window:
                sdl3.SDL_DestroyWindow(window)
            raise RuntimeError(f"Could not open a window: {error}")
        try:
            layer = objc.objc_object(c_void_p=sdl3.SDL_Metal_GetLayer(view))
            layer.setDevice_(device)
            layer.setPixelFormat_(Metal.MTLPixelFormatBGRA8Unorm)
            layer.setDisplaySyncEnabled_(vsync)
            queue = device.newMTL4CommandQueue()
            queue.addResidencySet_(layer.residencySet())
            residency_set, error = device.newResidencySetWithDescriptor_error_(
                Metal.MTLResidencySetDescriptor.new(), None)
            if residency_set is None:
                raise RuntimeError(f"Could not create a residency set: {error}")
            queue.addResidencySet_(residency_set)
            _context = Context(window, device, queue, layer, residency_set)

            sdl3.SDL_SetWindowPosition(window, sdl3.SDL_WINDOWPOS_CENTERED, sdl3.SDL_WINDOWPOS_CENTERED)
            sdl3.SDL_ShowWindow(window)
            imgui.create_context()
            implot.create_context()
            style = imgui.get_style()
            style.scale_all_sizes(scale)
            style.font_scale_dpi = scale
            impl_sdl3.init_for_metal(window)
            impl_metal4.init(device, queue, FRAMES_IN_FLIGHT)
            # Signaled with the number of each frame once the GPU is done with it
            frames_done = device.newSharedEvent()
            try:
                if post_init:
                    post_init()
                # Without fonts of its own, Dear ImGui picks its bitmap font for text that is
                # small in points, which a high-density display (for which macOS reports a
                # content scale of 1) magnifies into blocks. Its vector font is rasterized at
                # the density of the display.
                if len(imgui.get_io().fonts.fonts) == 0:
                    imgui.get_io().fonts.add_font_default_vector()
                _main_loop(_context, gui_function, fps_idle, clear_color, frames_done)
            finally:
                frames_done.waitUntilSignaledValue_timeoutMS_(_context.frame, 10000)
                if pre_exit:
                    pre_exit()
                impl_metal4.shutdown()
                impl_sdl3.shutdown()
                implot.destroy_context()
                imgui.destroy_context()
        finally:
            sdl3.SDL_Metal_DestroyView(view)
            sdl3.SDL_DestroyWindow(window)
    finally:
        _context = None
        sdl3.SDL_Quit()


def _main_loop(context, gui_function, fps_idle, clear_color, frames_done):
    """The frames of `run()`, as in Dear ImGui's example_sdl3_metal4"""
    window_id = sdl3.SDL_GetWindowID(context.window)
    idle_timeout = int(1000 / fps_idle) if fps_idle > 0 else 0
    device, queue, layer = context.device, context.command_queue, context.layer
    allocators = [device.newCommandAllocator() for _ in range(FRAMES_IN_FLIGHT)]
    command_buffers = [device.newCommandBuffer() for _ in range(FRAMES_IN_FLIGHT)]
    render_pass = Metal.MTL4RenderPassDescriptor.new()
    target = render_pass.colorAttachments().objectAtIndexedSubscript_(0)
    target.setLoadAction_(Metal.MTLLoadActionClear)
    target.setStoreAction_(Metal.MTLStoreActionStore)
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

        with objc.autorelease_pool():
            # The slot's resources (the backend's, the command buffer and allocator,
            # what the GUI kept alive) are free once the GPU is done with the frame
            # that last used them
            slot = context.frame % FRAMES_IN_FLIGHT
            if context.frame >= FRAMES_IN_FLIGHT:
                frames_done.waitUntilSignaledValue_timeoutMS_(context.frame - FRAMES_IN_FLIGHT + 1, 10000)
            context._kept[slot].clear()

            impl_sdl3.new_frame()
            imgui.new_frame()
            gui_function()
            imgui.render()
            draw_data = imgui.get_draw_data()

            # The drawable is acquired once the GUI is built, so that the GUI's own
            # work (which may be long) does not hold one of the layer's few
            sdl3.SDL_GetWindowSizeInPixels(context.window, ctypes.byref(width), ctypes.byref(height))
            if (width.value, height.value) != tuple(layer.drawableSize()):
                layer.setDrawableSize_((width.value, height.value))
            drawable = layer.nextDrawable() if draw_data.display_size.x > 0 and draw_data.display_size.y > 0 else None
            if drawable is not None:
                target.setTexture_(drawable.texture())
                r, g, b, a = clear_color or imgui.get_style_color_vec4(imgui.Col_.window_bg)
                target.setClearColor_(Metal.MTLClearColorMake(r * a, g * a, b * a, a))
                # Only needs the render pass's format, which comes with the drawable
                impl_metal4.new_frame(render_pass, slot)
                allocators[slot].reset()
                command_buffer = command_buffers[slot]
                command_buffer.beginCommandBufferWithAllocator_(allocators[slot])
                encoder = command_buffer.renderCommandEncoderWithDescriptor_(render_pass)
                impl_metal4.render_draw_data(draw_data, command_buffer, encoder)
                encoder.endEncoding()
                command_buffer.endCommandBuffer()
                queue.waitForDrawable_(drawable)
                queue.commit_count_([command_buffer], 1)
                target.setTexture_(None)
            context.frame += 1
            queue.signalEvent_value_(frames_done, context.frame)
            if drawable is not None:
                queue.signalDrawable_(drawable)
                drawable.present()
