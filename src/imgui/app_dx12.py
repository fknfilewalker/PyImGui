"""
Runs a Dear ImGui GUI in a window of its own, as `imgui.app` does, but draws it with
Direct3D 12 instead of SDL_GPU: SDL3 creates the window and delivers its events, and
imgui_impl_dx12 draws the GUI into a DXGI swapchain of the window. Windows only.

    import imgui
    from imgui import app_dx12

    def gui():
        imgui.text("Hello, world!")

    app_dx12.run(gui, window_title="Hello")

Unlike SDL_GPU, Direct3D hands out its objects: `context()` has the ID3D12Device and
the ID3D12CommandQueue, as the addresses that any Direct3D binding (ctypes, comtypes,
...) takes, and the shader-visible descriptor heap that the GUI draws its textures
from. A GUI can so show any texture of the device: it creates a shader resource view
of it in a descriptor of `context().allocate_srv_descriptor()`, and passes the GPU
handle of the descriptor to `imgui.image(imgui.ImTextureRef(gpu_handle))`. The
texture has to be in a state that pixel shaders can read when the frame is drawn
(D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE), and stay alive while the frames in
flight draw it (`Context.keep_alive()`), as does the descriptor.

The module talks to Direct3D through ctypes, and declares only the little that it uses.
"""

import ctypes
import sys
from collections.abc import Callable, Sequence
from ctypes import POINTER, byref, c_char_p, c_float, c_int32, c_long, c_size_t, c_uint8, c_uint16, c_uint32
from ctypes import c_uint64, c_void_p
from dataclasses import dataclass, field
from typing import Any, Optional

import sdl3

import imgui
from imgui import impl_dx12, impl_sdl3, implot

if sys.platform != "win32":
    raise ImportError("imgui.app_dx12 needs Windows")

FRAMES_IN_FLIGHT = 2
"""The frames that the CPU prepares while the GPU still draws earlier ones"""

BACK_BUFFERS = 3
"""The images of the window's swapchain"""

SRV_DESCRIPTORS = 256
"""The descriptors of the shader-visible heap, for the font atlas and the textures that the GUI shows"""


@dataclass
class Context:
    """The window of `run()` and the Direct3D objects that draw into it, as addresses"""

    window: Any
    """The SDL_Window"""
    hwnd: int
    """The HWND of the window"""
    device: int
    """The ID3D12Device"""
    command_queue: int
    """The direct ID3D12CommandQueue that draws the frames"""
    srv_descriptor_heap: int
    """The shader-visible ID3D12DescriptorHeap of CBV/SRV/UAV descriptors that the GUI's
    textures are drawn from"""
    frame: int = 0
    """The number of the current frame, which runs in slot `frame % FRAMES_IN_FLIGHT`"""
    app_shall_exit: bool = False
    """Set to close the window once the current frame is done"""
    _descriptors: Any = None
    _kept: list = field(default_factory=lambda: [[] for _ in range(FRAMES_IN_FLIGHT)])

    def keep_alive(self, obj: Any) -> None:
        """Holds a reference to `obj` until the GPU is done with the current frame"""
        self._kept[self.frame % FRAMES_IN_FLIGHT].append(obj)

    def allocate_srv_descriptor(self) -> tuple[int, int]:
        """A free descriptor of `srv_descriptor_heap`, as the `ptr`s of its
        (D3D12_CPU_DESCRIPTOR_HANDLE, D3D12_GPU_DESCRIPTOR_HANDLE)"""
        return self._descriptors.allocate()

    def free_srv_descriptor(self, cpu_handle: int, gpu_handle: int) -> None:
        """Returns a descriptor of `allocate_srv_descriptor()`, once the GPU is done
        with the frames that drew it"""
        self._descriptors.free(cpu_handle, gpu_handle)


_context: Optional[Context] = None


def context() -> Context:
    """The window and the Direct3D objects of the running `run()`"""
    if _context is None:
        raise RuntimeError("imgui.app_dx12.run() is not running")
    return _context


def run(
    gui_function: Callable[[], None],
    window_title: str = "",
    window_size: Optional[tuple[int, int]] = None,
    fps_idle: float = 9.0,
    *,
    device: Optional[int] = None,
    vsync: bool = True,
    clear_color: Optional[Sequence[float]] = None,
    validation: bool = False,
    post_init: Optional[Callable[[], None]] = None,
    pre_exit: Optional[Callable[[], None]] = None,
) -> None:
    """
    Opens a window and calls `gui_function` to build the GUI of every frame, until
    the window is closed or `context().app_shall_exit` is set.

    - `window_size`: in points, scaled for the display; 1280 x 720 by default.
    - `fps_idle`: the frame rate while there is no input, to spare the CPU; 0 draws
      frames continuously.
    - `device`: the ID3D12Device to draw with (its address, or a ctypes pointer), e.g.
      the one of another library whose textures the GUI shows; by default, one of
      the default adapter.
    - `vsync`: whether frames wait for the display.
    - `clear_color`: the RGBA background, by default the one of windows in the style.
    - `validation`: enables the Direct3D 12 debug layer (Windows' optional feature
      "Graphics Tools"), whose messages are printed to the standard error. Only for
      a device that `run()` creates. An error in a Direct3D call that Python makes
      through ctypes also raises an OSError (0x87D, the exception through which the
      layer reports it), as ctypes catches it.
    - `post_init`: called once the window, the backends and the contexts of Dear
      ImGui and ImPlot are set up, before the first frame: to load fonts or create
      textures, say.
    - `pre_exit`: called once the last frame is done, or the GUI raised, while
      the GPU is idle: to release the resources that the GUI showed.
    """
    global _context
    if not sdl3.SDL_Init(sdl3.SDL_INIT_VIDEO | sdl3.SDL_INIT_GAMEPAD):
        raise RuntimeError(f"SDL_Init(): {sdl3.SDL_GetError().decode()}")
    resources = []  # The COM objects to release, in reverse order
    try:
        scale = sdl3.SDL_GetDisplayContentScale(sdl3.SDL_GetPrimaryDisplay())
        width, height = window_size or (1280, 720)
        window = sdl3.SDL_CreateWindow(window_title.encode(), int(width * scale), int(height * scale),
                                       sdl3.SDL_WINDOW_RESIZABLE | sdl3.SDL_WINDOW_HIDDEN
                                       | sdl3.SDL_WINDOW_HIGH_PIXEL_DENSITY)
        if not window:
            raise RuntimeError(f"Could not open a window: {sdl3.SDL_GetError().decode()}")
        try:
            hwnd = sdl3.SDL_GetPointerProperty(sdl3.SDL_GetWindowProperties(window),
                                               sdl3.SDL_PROP_WINDOW_WIN32_HWND_POINTER, None)
            hwnd = ctypes.cast(hwnd, c_void_p).value

            if device is None:
                if validation:
                    _enable_debug_layer()
                device = _create_device()
            else:
                device = _ID3D12Device(_address(device))
                device.AddRef()
            resources.append(device)
            info_queue = _InfoQueue.of(device) if validation else None
            if info_queue:
                resources.append(info_queue)
            queue = _out(device.CreateCommandQueue, byref(_COMMAND_QUEUE_DESC(_D3D12_COMMAND_LIST_TYPE_DIRECT)),
                         byref(_IID_ID3D12CommandQueue), type=_ID3D12CommandQueue)
            resources.append(queue)
            descriptors = _DescriptorHeap(device, _D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV, SRV_DESCRIPTORS,
                                          shader_visible=True)
            resources.append(descriptors.heap)
            swapchain = _Swapchain(device, queue, hwnd, vsync)
            resources.append(swapchain)
            frames = _Frames(device)
            resources.append(frames)
            _context = Context(window, hwnd, device.address, queue.address, descriptors.heap.address, _descriptors=descriptors)

            sdl3.SDL_SetWindowPosition(window, sdl3.SDL_WINDOWPOS_CENTERED, sdl3.SDL_WINDOWPOS_CENTERED)
            sdl3.SDL_ShowWindow(window)
            imgui.create_context()
            implot.create_context()
            style = imgui.get_style()
            style.scale_all_sizes(scale)
            style.font_scale_dpi = scale
            impl_sdl3.init_for_d3d(window)
            impl_dx12.init(impl_dx12.InitInfo(
                device=device.address, command_queue=queue.address, num_frames_in_flight=FRAMES_IN_FLIGHT,
                rtv_format=_DXGI_FORMAT_R8G8B8A8_UNORM, srv_descriptor_heap=descriptors.heap.address,
                srv_descriptor_alloc_fn=descriptors.allocate, srv_descriptor_free_fn=descriptors.free))
            try:
                if post_init:
                    post_init()
                # Without fonts of its own, Dear ImGui picks its bitmap font for text that is
                # small in points, which a high-density display (for which macOS reports a
                # content scale of 1) magnifies into blocks. Its vector font is rasterized at
                # the density of the display.
                if len(imgui.get_io().fonts.fonts) == 0:
                    imgui.get_io().fonts.add_font_default_vector()
                _main_loop(_context, swapchain, frames, gui_function, fps_idle, clear_color, info_queue)
            finally:
                frames.wait_idle(queue)
                if info_queue:
                    info_queue.print_messages()
                if pre_exit:
                    pre_exit()
                impl_dx12.shutdown()
                impl_sdl3.shutdown()
                implot.destroy_context()
                imgui.destroy_context()
        finally:
            for resource in reversed(resources):
                resource.Release()
            sdl3.SDL_DestroyWindow(window)
    finally:
        _context = None
        sdl3.SDL_Quit()


def _main_loop(context, swapchain, frames, gui_function, fps_idle, clear_color, info_queue):
    """The frames of `run()`, as in Dear ImGui's example_win32_directx12"""
    window_id = sdl3.SDL_GetWindowID(context.window)
    idle_timeout = int(1000 / fps_idle) if fps_idle > 0 else 0
    queue = _ID3D12CommandQueue(context.command_queue)
    command_list = frames.command_list
    heaps = (c_void_p * 1)(context.srv_descriptor_heap)

    def process(event):
        impl_sdl3.process_event(event)
        if event.type == sdl3.SDL_EVENT_QUIT or (event.type == sdl3.SDL_EVENT_WINDOW_CLOSE_REQUESTED
                                                 and event.window.windowID == window_id):
            context.app_shall_exit = True

    event = sdl3.SDL_Event()
    while not context.app_shall_exit:
        # Without input, wait for some before building the next frame
        if idle_timeout and sdl3.SDL_WaitEventTimeout(byref(event), idle_timeout):
            process(event)
        while sdl3.SDL_PollEvent(byref(event)):
            process(event)
        if context.app_shall_exit:
            break
        if sdl3.SDL_GetWindowFlags(context.window) & sdl3.SDL_WINDOW_MINIMIZED:
            sdl3.SDL_Delay(10)
            continue

        # The slot's resources (the command allocator, what the GUI kept alive) are
        # free once the GPU is done with the frame that last used them
        slot = context.frame % FRAMES_IN_FLIGHT
        frames.wait(slot)
        context._kept[slot].clear()

        impl_dx12.new_frame()
        impl_sdl3.new_frame()
        imgui.new_frame()
        gui_function()
        imgui.render()
        draw_data = imgui.get_draw_data()

        size = _window_size_in_pixels(context.window)
        if size != swapchain.size:
            frames.wait_idle(queue)
            swapchain.resize(*size)
        if draw_data.display_size.x <= 0 or draw_data.display_size.y <= 0:
            continue
        back_buffer = swapchain.swapchain.GetCurrentBackBufferIndex()
        render_target = swapchain.buffers[back_buffer]
        rtv = swapchain.rtvs[back_buffer]

        allocator = frames.allocators[slot]
        allocator.Reset()
        command_list.Reset(allocator.address, None)
        command_list.ResourceBarrier(1, byref(_transition(render_target, _D3D12_RESOURCE_STATE_PRESENT,
                                                          _D3D12_RESOURCE_STATE_RENDER_TARGET)))
        r, g, b, a = clear_color or imgui.get_style_color_vec4(imgui.Col_.window_bg)
        command_list.ClearRenderTargetView(rtv, (c_float * 4)(r * a, g * a, b * a, a), 0, None)
        command_list.OMSetRenderTargets(1, byref(rtv), False, None)
        command_list.SetDescriptorHeaps(1, heaps)
        impl_dx12.render_draw_data(draw_data, command_list.address)
        command_list.ResourceBarrier(1, byref(_transition(render_target, _D3D12_RESOURCE_STATE_RENDER_TARGET,
                                                          _D3D12_RESOURCE_STATE_PRESENT)))
        command_list.Close()
        queue.ExecuteCommandLists(1, (c_void_p * 1)(command_list.address))
        swapchain.present()
        context.frame += 1
        frames.signal(queue, slot)
        if info_queue:
            info_queue.print_messages()


class _Swapchain:
    """The window's swapchain, its back buffers and their render target views"""

    def __init__(self, device, queue, hwnd, vsync):
        self.device, self.vsync = device, vsync
        factory = _out(_dxgi.CreateDXGIFactory1, byref(_IID_IDXGIFactory2), type=_IDXGIFactory2)
        try:
            # The window's size, which ResizeBuffers() follows
            description = _SWAP_CHAIN_DESC1(
                Format=_DXGI_FORMAT_R8G8B8A8_UNORM, SampleDesc=(1, 0), BufferUsage=_DXGI_USAGE_RENDER_TARGET_OUTPUT,
                BufferCount=BACK_BUFFERS, SwapEffect=_DXGI_SWAP_EFFECT_FLIP_DISCARD)
            swapchain1 = _out(factory.CreateSwapChainForHwnd, queue.address, hwnd, byref(description), None, None,
                              type=_IUnknown)
            # Alt+Enter would switch to an exclusive fullscreen that SDL3 does not know of
            factory.MakeWindowAssociation(hwnd, _DXGI_MWA_NO_ALT_ENTER)
        finally:
            factory.Release()
        try:
            self.swapchain = swapchain1.query(_IID_IDXGISwapChain3, _IDXGISwapChain3)
        finally:
            swapchain1.Release()
        self.rtv_heap = _DescriptorHeap(device, _D3D12_DESCRIPTOR_HEAP_TYPE_RTV, BACK_BUFFERS)
        self.rtvs = [self.rtv_heap.allocate_cpu() for _ in range(BACK_BUFFERS)]
        self.buffers = []
        self.size = (0, 0)
        self._create_render_targets()

    def _create_render_targets(self):
        description = _SWAP_CHAIN_DESC1()
        self.swapchain.GetDesc1(byref(description))
        self.size = (description.Width, description.Height)
        for index, rtv in enumerate(self.rtvs):
            buffer = _out(self.swapchain.GetBuffer, index, byref(_IID_ID3D12Resource), type=_IUnknown)
            self.device.CreateRenderTargetView(buffer.address, None, rtv)
            self.buffers.append(buffer)

    def resize(self, width, height):
        """Resizes the back buffers, once the GPU is done with them"""
        for buffer in self.buffers:
            buffer.Release()
        self.buffers = []
        self.swapchain.ResizeBuffers(0, max(width, 1), max(height, 1), _DXGI_FORMAT_UNKNOWN, 0)
        self._create_render_targets()

    def present(self):
        result = self.swapchain.Present(1 if self.vsync else 0, 0)
        # DXGI_STATUS_OCCLUDED: the window is not visible, which is no error
        return result

    def Release(self):
        for buffer in self.buffers:
            buffer.Release()
        self.rtv_heap.heap.Release()
        self.swapchain.Release()


class _Frames:
    """The command allocator of each slot of FRAMES_IN_FLIGHT, the command list that
    records the frames, and the fence that tells which frames the GPU is done with"""

    def __init__(self, device):
        self.allocators = [_out(device.CreateCommandAllocator, _D3D12_COMMAND_LIST_TYPE_DIRECT,
                                byref(_IID_ID3D12CommandAllocator), type=_ID3D12CommandAllocator)
                           for _ in range(FRAMES_IN_FLIGHT)]
        self.command_list = _out(device.CreateCommandList, 0, _D3D12_COMMAND_LIST_TYPE_DIRECT,
                                 self.allocators[0].address, None, byref(_IID_ID3D12GraphicsCommandList),
                                 type=_ID3D12GraphicsCommandList)
        # Created recording, which the first frame's Reset() expects not to be
        self.command_list.Close()
        self.fence = _out(device.CreateFence, 0, 0, byref(_IID_ID3D12Fence), type=_ID3D12Fence)
        self.event = _kernel32.CreateEventW(None, False, False, None)
        if not self.event:
            raise ctypes.WinError()
        self.value = 0
        """The value that the fence is signaled with after the last frame"""
        self.slot_values = [0] * FRAMES_IN_FLIGHT
        """The value of the fence after the last frame of each slot"""

    def _wait_for(self, value):
        if self.fence.GetCompletedValue() < value:
            self.fence.SetEventOnCompletion(value, self.event)
            _kernel32.WaitForSingleObject(self.event, 0xFFFFFFFF)

    def wait(self, slot):
        """Waits for the GPU to be done with the last frame of `slot`"""
        self._wait_for(self.slot_values[slot])

    def signal(self, queue, slot):
        """Has the fence signaled once the GPU is done with the frame just submitted, in `slot`"""
        self.value += 1
        queue.Signal(self.fence.address, self.value)
        self.slot_values[slot] = self.value

    def wait_idle(self, queue):
        """Waits for the GPU to be done with all frames"""
        self.value += 1
        queue.Signal(self.fence.address, self.value)
        self._wait_for(self.value)

    def Release(self):
        _kernel32.CloseHandle(self.event)
        self.fence.Release()
        self.command_list.Release()
        for allocator in self.allocators:
            allocator.Release()


class _DescriptorHeap:
    """A descriptor heap, whose descriptors it hands out from a free list"""

    def __init__(self, device, type, count, shader_visible=False):
        self.heap = _out(device.CreateDescriptorHeap,
                         byref(_DESCRIPTOR_HEAP_DESC(type, count, 1 if shader_visible else 0, 0)),
                         byref(_IID_ID3D12DescriptorHeap), type=_ID3D12DescriptorHeap)
        increment = device.GetDescriptorHandleIncrementSize(type)
        cpu_start = _CPU_DESCRIPTOR_HANDLE()
        self.heap.GetCPUDescriptorHandleForHeapStart(byref(cpu_start))
        gpu_start = _GPU_DESCRIPTOR_HANDLE()
        if shader_visible:
            self.heap.GetGPUDescriptorHandleForHeapStart(byref(gpu_start))
        self._cpu_start, self._gpu_start, self._increment = cpu_start.ptr, gpu_start.ptr, increment
        self._free = list(reversed(range(count)))

    def allocate(self):
        """A free descriptor, as the `ptr`s of its (CPU handle, GPU handle)"""
        if not self._free:
            raise RuntimeError("The descriptor heap is full")
        index = self._free.pop()
        return self._cpu_start + index * self._increment, self._gpu_start + index * self._increment

    def allocate_cpu(self):
        return _CPU_DESCRIPTOR_HANDLE(self.allocate()[0])

    def free(self, cpu_handle, gpu_handle):
        self._free.append((cpu_handle - self._cpu_start) // self._increment)


class _InfoQueue:
    """The messages of the debug layer, which it prints"""

    @staticmethod
    def of(device):
        queue = c_void_p()
        if _IUnknown(device.address).QueryInterface(byref(_IID_ID3D12InfoQueue), byref(queue)) < 0:
            return None
        return _ID3D12InfoQueue(queue.value)


def _print_messages(self):
    severities = ("CORRUPTION", "ERROR", "WARNING", "INFO", "MESSAGE")
    for index in range(self.GetNumStoredMessagesAllowedByRetrievalFilter()):
        length = c_size_t()
        self.GetMessage(index, None, byref(length))
        buffer = (c_uint8 * length.value)()
        message = ctypes.cast(buffer, POINTER(_MESSAGE))
        self.GetMessage(index, message, byref(length))
        severity = message.contents.Severity
        print(f"D3D12 {severities[severity] if severity < len(severities) else severity}: "
              f"{message.contents.pDescription.decode(errors='replace')}", file=sys.stderr)
    self.ClearStoredMessages()


def _enable_debug_layer():
    debug = c_void_p()
    if _d3d12.D3D12GetDebugInterface(byref(_IID_ID3D12Debug), byref(debug)) < 0:
        raise RuntimeError("The Direct3D 12 debug layer is not installed (Windows' optional feature Graphics Tools)")
    debug = _ID3D12Debug(debug.value)
    debug.EnableDebugLayer()
    debug.Release()


def _create_device():
    """An ID3D12Device of the default adapter"""
    return _out(_d3d12.D3D12CreateDevice, None, _D3D_FEATURE_LEVEL_11_0, byref(_IID_ID3D12Device),
                type=_ID3D12Device)


def _transition(resource, before, after):
    return _RESOURCE_BARRIER(_D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, 0, _RESOURCE_TRANSITION_BARRIER(
        resource.address, _D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, before, after))


def _window_size_in_pixels(window):
    width, height = ctypes.c_int(), ctypes.c_int()
    sdl3.SDL_GetWindowSizeInPixels(window, byref(width), byref(height))
    return width.value, height.value


def _address(obj):
    """The address of a COM object, from an int or a ctypes pointer"""
    if isinstance(obj, int):
        return obj
    return ctypes.cast(obj, c_void_p).value


def _out(function, *args, type):
    """The COM object that `function` creates, which it returns through its last argument"""
    obj = c_void_p()
    function(*args, byref(obj))
    return type(obj.value)


# The Direct3D 12 and DXGI API that the module uses, as ctypes declares it

_D3D_FEATURE_LEVEL_11_0 = 0xB000
_D3D12_COMMAND_LIST_TYPE_DIRECT = 0
_D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV = 0
_D3D12_DESCRIPTOR_HEAP_TYPE_RTV = 2
_D3D12_RESOURCE_BARRIER_TYPE_TRANSITION = 0
_D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES = 0xFFFFFFFF
_D3D12_RESOURCE_STATE_PRESENT = 0
_D3D12_RESOURCE_STATE_RENDER_TARGET = 0x4
_DXGI_FORMAT_UNKNOWN = 0
_DXGI_FORMAT_R8G8B8A8_UNORM = 28
_DXGI_USAGE_RENDER_TARGET_OUTPUT = 0x20
_DXGI_SWAP_EFFECT_FLIP_DISCARD = 4
_DXGI_MWA_NO_ALT_ENTER = 0x2


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16), ("Data4", c_uint8 * 8)]

    def __init__(self, data1, data2, data3, *data4):
        super().__init__(data1, data2, data3, (c_uint8 * 8)(*data4))


_IID_ID3D12Device = _GUID(0x189819f1, 0x1db6, 0x4b57, 0xbe, 0x54, 0x18, 0x21, 0x33, 0x9b, 0x85, 0xf7)
_IID_ID3D12CommandQueue = _GUID(0x0ec870a6, 0x5d7e, 0x4c22, 0x8c, 0xfc, 0x5b, 0xaa, 0xe0, 0x76, 0x16, 0xed)
_IID_ID3D12CommandAllocator = _GUID(0x6102dee4, 0xaf59, 0x4b09, 0xb9, 0x99, 0xb4, 0x4d, 0x73, 0xf0, 0x9b, 0x24)
_IID_ID3D12GraphicsCommandList = _GUID(0x5b160d0f, 0xac1b, 0x4185, 0x8b, 0xa8, 0xb3, 0xae, 0x42, 0xa5, 0xa4, 0x55)
_IID_ID3D12DescriptorHeap = _GUID(0x8efb471d, 0x616c, 0x4f49, 0x90, 0xf7, 0x12, 0x7b, 0xb7, 0x63, 0xfa, 0x51)
_IID_ID3D12Fence = _GUID(0x0a753dcf, 0xc4d8, 0x4b91, 0xad, 0xf6, 0xbe, 0x5a, 0x60, 0xd9, 0x5a, 0x76)
_IID_ID3D12Resource = _GUID(0x696442be, 0xa72e, 0x4059, 0xbc, 0x79, 0x5b, 0x5c, 0x98, 0x04, 0x0f, 0xad)
_IID_ID3D12Debug = _GUID(0x344488b7, 0x6846, 0x474b, 0xb9, 0x89, 0xf0, 0x27, 0x44, 0x82, 0x45, 0xe0)
_IID_ID3D12InfoQueue = _GUID(0x0742a90b, 0xc387, 0x483f, 0xb9, 0x46, 0x30, 0xa7, 0xe4, 0xe6, 0x14, 0x58)
_IID_IDXGIFactory2 = _GUID(0x50c83a1c, 0xe072, 0x4c48, 0x87, 0xb0, 0x36, 0x30, 0xfa, 0x36, 0xa6, 0xd0)
_IID_IDXGISwapChain3 = _GUID(0x94d99bdb, 0xf1f8, 0x4ab0, 0xb2, 0x36, 0x7d, 0xa0, 0x17, 0x0e, 0xda, 0xb1)


def _struct(name, *fields):
    return type(name, (ctypes.Structure,), {"_fields_": list(fields)})


_CPU_DESCRIPTOR_HANDLE = _struct("D3D12_CPU_DESCRIPTOR_HANDLE", ("ptr", c_size_t))
_GPU_DESCRIPTOR_HANDLE = _struct("D3D12_GPU_DESCRIPTOR_HANDLE", ("ptr", c_uint64))
_COMMAND_QUEUE_DESC = _struct("D3D12_COMMAND_QUEUE_DESC", ("Type", c_int32), ("Priority", c_int32),
                              ("Flags", c_int32), ("NodeMask", c_uint32))
_DESCRIPTOR_HEAP_DESC = _struct("D3D12_DESCRIPTOR_HEAP_DESC", ("Type", c_int32), ("NumDescriptors", c_uint32),
                                ("Flags", c_int32), ("NodeMask", c_uint32))
_SWAP_CHAIN_DESC1 = _struct(
    "DXGI_SWAP_CHAIN_DESC1", ("Width", c_uint32), ("Height", c_uint32), ("Format", c_int32), ("Stereo", c_int32),
    ("SampleDesc", c_uint32 * 2), ("BufferUsage", c_uint32), ("BufferCount", c_uint32), ("Scaling", c_int32),
    ("SwapEffect", c_int32), ("AlphaMode", c_int32), ("Flags", c_uint32))
_RESOURCE_TRANSITION_BARRIER = _struct(
    "D3D12_RESOURCE_TRANSITION_BARRIER", ("pResource", c_void_p), ("Subresource", c_uint32),
    ("StateBefore", c_int32), ("StateAfter", c_int32))
# The union of D3D12_RESOURCE_BARRIER, as its largest member, the transition barrier
_RESOURCE_BARRIER = _struct("D3D12_RESOURCE_BARRIER", ("Type", c_int32), ("Flags", c_int32),
                            ("Transition", _RESOURCE_TRANSITION_BARRIER))
_MESSAGE = _struct("D3D12_MESSAGE", ("Category", c_int32), ("Severity", c_int32), ("ID", c_int32),
                   ("pDescription", c_char_p), ("DescriptionByteLength", c_size_t))


_HRESULT = c_long


class _IUnknown:
    """A COM interface pointer, whose methods `_methods` declares: their indices in its
    virtual table, result types and argument types. Methods that return an HRESULT
    raise for the failures, and return it otherwise."""

    _methods = {
        "QueryInterface": (0, _HRESULT, POINTER(_GUID), POINTER(c_void_p)),
        "AddRef": (1, c_uint32),
        "Release": (2, c_uint32),
    }

    def __init__(self, address):
        if not address:
            raise ValueError(f"{type(self).__name__[1:]} must not be null")
        self.address = address

    def __getattr__(self, name):
        try:
            index, result, *arguments = self._methods[name]
        except KeyError:
            raise AttributeError(name) from None
        function = ctypes.WINFUNCTYPE(result, c_void_p, *arguments)(
            ctypes.cast(self.address, POINTER(POINTER(c_void_p)))[0][index])

        def method(*args):
            value = function(self.address, *args)
            if result is _HRESULT and value < 0 and name != "QueryInterface":
                raise RuntimeError(f"{type(self).__name__[1:]}::{name}() failed: HRESULT {value & 0xFFFFFFFF:#010x}")
            return value

        return method

    def query(self, iid, type):
        """The interface `iid` of the object, as a `type`"""
        obj = c_void_p()
        if self.QueryInterface(byref(iid), byref(obj)) < 0:
            raise RuntimeError(f"{type.__name__[1:]} is not available")
        return type(obj.value)


def _interface(name, methods):
    return type(name, (_IUnknown,), {"_methods": {**_IUnknown._methods, **methods}})


# The indices are the ones of the C declarations of the interfaces in the Windows
# SDK's headers, on Windows: there, methods that return a structure take a pointer
# to it instead, after `This`.
_ID3D12Device = _interface("_ID3D12Device", {
    "CreateCommandQueue": (8, _HRESULT, POINTER(_COMMAND_QUEUE_DESC), POINTER(_GUID), POINTER(c_void_p)),
    "CreateCommandAllocator": (9, _HRESULT, c_int32, POINTER(_GUID), POINTER(c_void_p)),
    "CreateCommandList": (12, _HRESULT, c_uint32, c_int32, c_void_p, c_void_p, POINTER(_GUID), POINTER(c_void_p)),
    "CreateDescriptorHeap": (14, _HRESULT, POINTER(_DESCRIPTOR_HEAP_DESC), POINTER(_GUID), POINTER(c_void_p)),
    "GetDescriptorHandleIncrementSize": (15, c_uint32, c_int32),
    "CreateRenderTargetView": (20, None, c_void_p, c_void_p, _CPU_DESCRIPTOR_HANDLE),
    "CreateFence": (36, _HRESULT, c_uint64, c_int32, POINTER(_GUID), POINTER(c_void_p)),
})
_ID3D12CommandQueue = _interface("_ID3D12CommandQueue", {
    "ExecuteCommandLists": (10, None, c_uint32, POINTER(c_void_p)),
    "Signal": (14, _HRESULT, c_void_p, c_uint64),
})
_ID3D12CommandAllocator = _interface("_ID3D12CommandAllocator", {"Reset": (8, _HRESULT)})
_ID3D12GraphicsCommandList = _interface("_ID3D12GraphicsCommandList", {
    "Close": (9, _HRESULT),
    "Reset": (10, _HRESULT, c_void_p, c_void_p),
    "ResourceBarrier": (26, None, c_uint32, POINTER(_RESOURCE_BARRIER)),
    "SetDescriptorHeaps": (28, None, c_uint32, POINTER(c_void_p)),
    "OMSetRenderTargets": (46, None, c_uint32, POINTER(_CPU_DESCRIPTOR_HANDLE), c_int32, c_void_p),
    "ClearRenderTargetView": (48, None, _CPU_DESCRIPTOR_HANDLE, POINTER(c_float), c_uint32, c_void_p),
})
_ID3D12DescriptorHeap = _interface("_ID3D12DescriptorHeap", {
    "GetCPUDescriptorHandleForHeapStart": (9, c_void_p, POINTER(_CPU_DESCRIPTOR_HANDLE)),
    "GetGPUDescriptorHandleForHeapStart": (10, c_void_p, POINTER(_GPU_DESCRIPTOR_HANDLE)),
})
_ID3D12Fence = _interface("_ID3D12Fence", {
    "GetCompletedValue": (8, c_uint64),
    "SetEventOnCompletion": (9, _HRESULT, c_uint64, c_void_p),
})
_ID3D12Debug = _interface("_ID3D12Debug", {"EnableDebugLayer": (3, None)})
_ID3D12InfoQueue = _interface("_ID3D12InfoQueue", {
    "ClearStoredMessages": (4, None),
    "GetMessage": (5, _HRESULT, c_uint64, POINTER(_MESSAGE), POINTER(c_size_t)),
    "GetNumStoredMessagesAllowedByRetrievalFilter": (9, c_uint64),
})
_ID3D12InfoQueue.print_messages = _print_messages
_IDXGIFactory2 = _interface("_IDXGIFactory2", {
    "MakeWindowAssociation": (8, _HRESULT, c_void_p, c_uint32),
    "CreateSwapChainForHwnd": (15, _HRESULT, c_void_p, c_void_p, POINTER(_SWAP_CHAIN_DESC1), c_void_p, c_void_p,
                               POINTER(c_void_p)),
})
_IDXGISwapChain3 = _interface("_IDXGISwapChain3", {
    "Present": (8, _HRESULT, c_uint32, c_uint32),
    "GetBuffer": (9, _HRESULT, c_uint32, POINTER(_GUID), POINTER(c_void_p)),
    "ResizeBuffers": (13, _HRESULT, c_uint32, c_uint32, c_uint32, c_int32, c_uint32),
    "GetDesc1": (18, _HRESULT, POINTER(_SWAP_CHAIN_DESC1)),
    "GetCurrentBackBufferIndex": (36, c_uint32),
})

_d3d12 = ctypes.WinDLL("d3d12")
_d3d12.D3D12CreateDevice.restype = _HRESULT
_d3d12.D3D12CreateDevice.argtypes = [c_void_p, c_int32, POINTER(_GUID), POINTER(c_void_p)]
_d3d12.D3D12GetDebugInterface.restype = _HRESULT
_d3d12.D3D12GetDebugInterface.argtypes = [POINTER(_GUID), POINTER(c_void_p)]
_dxgi = ctypes.WinDLL("dxgi")
_dxgi.CreateDXGIFactory1.restype = _HRESULT
_dxgi.CreateDXGIFactory1.argtypes = [POINTER(_GUID), POINTER(c_void_p)]
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateEventW.restype = c_void_p
_kernel32.CreateEventW.argtypes = [c_void_p, c_int32, c_int32, c_void_p]
_kernel32.WaitForSingleObject.argtypes = [c_void_p, c_uint32]
_kernel32.CloseHandle.argtypes = [c_void_p]


def _check_result(result, function, arguments):
    if result < 0:
        raise RuntimeError(f"{function.__name__}() failed: HRESULT {result & 0xFFFFFFFF:#010x}")
    return result


for _function in (_d3d12.D3D12CreateDevice, _dxgi.CreateDXGIFactory1):
    _function.errcheck = _check_result
