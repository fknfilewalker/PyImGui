"""
Runs a Dear ImGui GUI in a window of its own, as `imgui.app` does, but draws it with
Vulkan instead of SDL_GPU: SDL3 creates the window and its Vulkan surface, and
imgui_impl_vulkan draws the GUI into the window's swapchain, with dynamic rendering.
Needs Vulkan 1.3; on macOS, MoltenVK (e.g. the Vulkan SDK's), which SDL3 loads.

    import imgui
    from imgui import app_vulkan

    def gui():
        imgui.text("Hello, world!")

    app_vulkan.run(gui, window_title="Hello")

Unlike SDL_GPU, Vulkan hands out its objects: `context()` has the instance, the
device and its queue, as the addresses that any Vulkan binding (ctypes, cffi's
`vulkan`, ...) takes, and `vkGetInstanceProcAddr` to load the functions with. A GUI
can so show any image view of the device with
`imgui.image(imgui.ImTextureRef(impl_vulkan.add_texture(view, layout)))`, including
ones of memory that other APIs share with it (`device_extensions` enables the
extensions for that, such as VK_KHR_external_memory_win32). The image has to be in
`layout` when the frame is drawn, and stay alive while the frames in flight draw it
(`Context.keep_alive()`).

The module talks to Vulkan through ctypes, and declares only the little that it uses.
"""

import contextlib
import ctypes
from collections.abc import Callable, Sequence
from ctypes import POINTER, byref, c_char, c_char_p, c_float, c_int32, c_uint32, c_uint64, c_void_p
from dataclasses import dataclass, field
from typing import Any, Optional

import sdl3

import imgui
from imgui import impl_sdl3, impl_vulkan, implot

FRAMES_IN_FLIGHT = 2
"""The frames that the CPU prepares while the GPU still draws earlier ones"""

API_VERSION = (1 << 22) | (3 << 12)
"""VK_API_VERSION_1_3, the version of Vulkan that run() asks for and needs"""


@dataclass
class Context:
    """The window of `run()` and the Vulkan objects that draw into it, as addresses"""

    window: Any
    """The SDL_Window"""
    instance: int
    """The VkInstance, of API_VERSION"""
    physical_device: int
    """The VkPhysicalDevice"""
    device: int
    """The VkDevice"""
    queue_family: int
    """The index of the queue family of `queue`, which does graphics"""
    queue: int
    """The VkQueue that draws and presents the frames"""
    get_instance_proc_addr: int
    """The address of vkGetInstanceProcAddr, to load the Vulkan functions with"""
    color_format: int
    """The VkFormat of the window's images"""
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
    """The window and the Vulkan objects of the running `run()`"""
    if _context is None:
        raise RuntimeError("imgui.app_vulkan.run() is not running")
    return _context


def run(
    gui_function: Callable[[], None],
    window_title: str = "",
    window_size: Optional[tuple[int, int]] = None,
    fps_idle: float = 9.0,
    *,
    vsync: bool = True,
    clear_color: Optional[Sequence[float]] = None,
    instance_extensions: Sequence[str] = (),
    device_extensions: Sequence[str] = (),
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
    - `vsync`: whether frames wait for the display.
    - `clear_color`: the RGBA background, by default the one of windows in the style.
    - `instance_extensions`, `device_extensions`: Vulkan extensions to enable beyond
      the ones that the window needs. The device is the first one that has them all,
      preferring discrete GPUs.
    - `validation`: enables the Khronos validation layer (of the Vulkan SDK), which
      reports misuse of Vulkan on the standard output.
    - `post_init`: called once the window, the backends and the contexts of Dear
      ImGui and ImPlot are set up, before the first frame: to load fonts or create
      textures, say.
    - `pre_exit`: called once the last frame is done, or the GUI raised, while
      the GPU is idle: to release the resources that the GUI showed.
    """
    global _context
    if not sdl3.SDL_Init(sdl3.SDL_INIT_VIDEO | sdl3.SDL_INIT_GAMEPAD):
        raise RuntimeError(f"SDL_Init(): {sdl3.SDL_GetError().decode()}")
    try:
        # Resources are released in the reverse order of their creation, when the
        # loop ends or anything raises
        with contextlib.ExitStack() as cleanup:
            scale = sdl3.SDL_GetDisplayContentScale(sdl3.SDL_GetPrimaryDisplay())
            width, height = window_size or (1280, 720)
            window = sdl3.SDL_CreateWindow(window_title.encode(), int(width * scale), int(height * scale),
                                           sdl3.SDL_WINDOW_VULKAN | sdl3.SDL_WINDOW_RESIZABLE | sdl3.SDL_WINDOW_HIDDEN
                                           | sdl3.SDL_WINDOW_HIGH_PIXEL_DENSITY)
            if not window:
                raise RuntimeError(f"Could not open a window: {sdl3.SDL_GetError().decode()}")
            cleanup.callback(sdl3.SDL_DestroyWindow, window)

            # The Vulkan library that SDL3 loaded for the window
            get_instance_proc_addr = ctypes.cast(sdl3.SDL_Vulkan_GetVkGetInstanceProcAddr(), c_void_p).value
            instance = _create_instance(_PFN_vkGetInstanceProcAddr(get_instance_proc_addr),
                                        instance_extensions, validation)
            vk = _Vulkan(get_instance_proc_addr, instance)
            cleanup.callback(vk.vkDestroyInstance, instance, None)
            surface = sdl3.VkSurfaceKHR()
            if not sdl3.SDL_Vulkan_CreateSurface(window, instance, None, byref(surface)):
                raise RuntimeError(f"Could not create a Vulkan surface: {sdl3.SDL_GetError().decode()}")
            surface = surface.value
            cleanup.callback(sdl3.SDL_Vulkan_DestroySurface, instance, surface, None)

            physical_device, queue_family, extensions = _select_physical_device(vk, instance, surface,
                                                                                 device_extensions)
            device = _create_device(vk, physical_device, queue_family, extensions)
            vk.load_device_functions(device)
            cleanup.callback(vk.vkDestroyDevice, device, None)
            queue = c_void_p()
            vk.vkGetDeviceQueue(device, queue_family, 0, byref(queue))
            color_format, color_space = _select_surface_format(vk, physical_device, surface)
            present_mode = _select_present_mode(vk, physical_device, surface, vsync)

            swapchain = _Swapchain(vk, physical_device, device, surface, color_format, color_space, present_mode)
            cleanup.callback(swapchain.destroy)
            swapchain.create(*_window_size_in_pixels(window))
            frames = _Frames(vk, device, queue_family)
            cleanup.callback(frames.destroy)
            _context = Context(window, instance, physical_device, device, queue_family, queue.value,
                               get_instance_proc_addr, color_format)

            sdl3.SDL_SetWindowPosition(window, sdl3.SDL_WINDOWPOS_CENTERED, sdl3.SDL_WINDOWPOS_CENTERED)
            sdl3.SDL_ShowWindow(window)
            imgui.create_context()
            cleanup.callback(imgui.destroy_context)
            implot.create_context()
            cleanup.callback(implot.destroy_context)
            style = imgui.get_style()
            style.scale_all_sizes(scale)
            style.font_scale_dpi = scale
            impl_sdl3.init_for_vulkan(window)
            cleanup.callback(impl_sdl3.shutdown)
            info = impl_vulkan.InitInfo(
                api_version=API_VERSION, instance=instance, physical_device=physical_device, device=device,
                queue_family=queue_family, queue=queue.value,
                # The backend's own pool, for the font atlas and the textures that the
                # GUI adds (add_texture())
                descriptor_pool_size=1024,
                min_image_count=swapchain.min_image_count,
                image_count=max(len(swapchain.images), FRAMES_IN_FLIGHT),
                use_dynamic_rendering=True,
                check_vk_result_fn=_check_vk_result)
            info.pipeline_info_main.color_attachment_formats = [color_format]
            # Loads the backend's Vulkan functions through SDL3's vkGetInstanceProcAddr
            impl_vulkan.init(info)
            cleanup.callback(impl_vulkan.shutdown)
            if pre_exit:
                cleanup.callback(pre_exit)
            # Before anything is released, including what the GUI showed
            cleanup.callback(vk.vkDeviceWaitIdle, device)

            if post_init:
                post_init()
            # Without fonts of its own, Dear ImGui picks its bitmap font for text that is
            # small in points, which a high-density display (for which macOS reports a
            # content scale of 1) magnifies into blocks. Its vector font is rasterized at
            # the density of the display.
            if len(imgui.get_io().fonts.fonts) == 0:
                imgui.get_io().fonts.add_font_default_vector()
            _main_loop(_context, vk, swapchain, frames, gui_function, fps_idle, clear_color)
    finally:
        _context = None
        sdl3.SDL_Quit()


def _main_loop(context, vk, swapchain, frames, gui_function, fps_idle, clear_color):
    """The frames of `run()`, as in Dear ImGui's example_sdl3_vulkan"""
    window_id = sdl3.SDL_GetWindowID(context.window)
    idle_timeout = int(1000 / fps_idle) if fps_idle > 0 else 0
    device, queue = context.device, context.queue
    image_index = c_uint32()

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

        # The slot's resources (the command buffer, what the GUI kept alive) are free
        # once the GPU is done with the frame that last used them
        slot = context.frame % FRAMES_IN_FLIGHT
        frame = frames.slots[slot]
        _check(vk.vkWaitForFences(device, 1, byref(c_void_p(frame.fence)), True, _NO_TIMEOUT), "vkWaitForFences")
        context._kept[slot].clear()

        impl_vulkan.new_frame()
        impl_sdl3.new_frame()
        imgui.new_frame()
        gui_function()
        imgui.render()
        draw_data = imgui.get_draw_data()

        # The image is acquired once the GUI is built, so that the GUI's own work
        # (which may be long) does not hold one of the swapchain's few
        size = _window_size_in_pixels(context.window)
        if swapchain.stale or size != swapchain.window_size:
            swapchain.create(*size)
            impl_vulkan.set_min_image_count(swapchain.min_image_count)
        if not swapchain.handle or draw_data.display_size.x <= 0 or draw_data.display_size.y <= 0:
            continue
        result = vk.vkAcquireNextImageKHR(device, swapchain.handle, _NO_TIMEOUT, frame.image_acquired, None,
                                          byref(image_index))
        if result == _VK_ERROR_OUT_OF_DATE_KHR:
            swapchain.stale = True
            continue
        swapchain.stale = result == _VK_SUBOPTIMAL_KHR
        _check(result, "vkAcquireNextImageKHR")
        image = image_index.value

        _check(vk.vkResetFences(device, 1, byref(c_void_p(frame.fence))), "vkResetFences")
        _check(vk.vkResetCommandPool(device, frame.command_pool, 0), "vkResetCommandPool")
        command_buffer = frame.command_buffer
        _check(vk.vkBeginCommandBuffer(command_buffer, byref(_CommandBufferBeginInfo(
            _VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, None, _VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT))),
            "vkBeginCommandBuffer")
        # The image's former contents are discarded, as the render pass clears it
        _image_barrier(vk, command_buffer, swapchain.images[image],
                       _VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, 0, _VK_IMAGE_LAYOUT_UNDEFINED,
                       _VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, _VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
                       _VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL)
        r, g, b, a = clear_color or imgui.get_style_color_vec4(imgui.Col_.window_bg)
        attachment = _RenderingAttachmentInfo(
            sType=_VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, imageView=swapchain.views[image],
            imageLayout=_VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, loadOp=_VK_ATTACHMENT_LOAD_OP_CLEAR,
            storeOp=_VK_ATTACHMENT_STORE_OP_STORE, clearValue=(c_float * 4)(r * a, g * a, b * a, a))
        vk.vkCmdBeginRendering(command_buffer, byref(_RenderingInfo(
            sType=_VK_STRUCTURE_TYPE_RENDERING_INFO, renderArea=_Rect2D((0, 0), swapchain.extent), layerCount=1,
            colorAttachmentCount=1, pColorAttachments=ctypes.pointer(attachment))))
        impl_vulkan.render_draw_data(draw_data, command_buffer)
        vk.vkCmdEndRendering(command_buffer)
        _image_barrier(vk, command_buffer, swapchain.images[image],
                       _VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, _VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
                       _VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, _VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0,
                       _VK_IMAGE_LAYOUT_PRESENT_SRC_KHR)
        _check(vk.vkEndCommandBuffer(command_buffer), "vkEndCommandBuffer")

        # Drawing waits for the image, presenting for the drawing
        wait_semaphore = c_void_p(frame.image_acquired)
        render_complete = c_void_p(swapchain.render_complete[image])
        _check(vk.vkQueueSubmit(queue, 1, byref(_SubmitInfo(
            _VK_STRUCTURE_TYPE_SUBMIT_INFO, None, 1, ctypes.pointer(wait_semaphore),
            ctypes.pointer(c_uint32(_VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT)),
            1, ctypes.pointer(c_void_p(command_buffer)), 1, ctypes.pointer(render_complete))), frame.fence),
            "vkQueueSubmit")
        result = vk.vkQueuePresentKHR(queue, byref(_PresentInfoKHR(
            _VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, None, 1, ctypes.pointer(render_complete), 1,
            ctypes.pointer(c_void_p(swapchain.handle)), ctypes.pointer(c_uint32(image)), None)))
        if result in (_VK_ERROR_OUT_OF_DATE_KHR, _VK_SUBOPTIMAL_KHR):
            swapchain.stale = True
        else:
            _check(result, "vkQueuePresentKHR")
        context.frame += 1


class _Swapchain:
    """The window's swapchain, its images and their views, and a semaphore per image
    that presenting it waits for. Created again when the window's size changes."""

    def __init__(self, vk, physical_device, device, surface, color_format, color_space, present_mode):
        self.vk, self.physical_device, self.device, self.surface = vk, physical_device, device, surface
        self.color_format, self.color_space, self.present_mode = color_format, color_space, present_mode
        self.handle = None
        self.window_size = self.extent = (0, 0)
        self.min_image_count = 2
        self.images, self.views, self.render_complete = [], [], []
        self.stale = False

    def create(self, width, height):
        """(Re)creates the swapchain for a window of `width` x `height` pixels"""
        vk, device = self.vk, self.device
        self.window_size = (width, height)
        capabilities = _SurfaceCapabilitiesKHR()
        _check(vk.vkGetPhysicalDeviceSurfaceCapabilitiesKHR(self.physical_device, self.surface, byref(capabilities)),
               "vkGetPhysicalDeviceSurfaceCapabilitiesKHR")
        # The surface's size, unless it takes the swapchain's
        if capabilities.currentExtent.width != 0xFFFFFFFF:
            width, height = capabilities.currentExtent.width, capabilities.currentExtent.height
        else:
            width = min(max(width, capabilities.minImageExtent.width), capabilities.maxImageExtent.width)
            height = min(max(height, capabilities.minImageExtent.height), capabilities.maxImageExtent.height)
        vk.vkDeviceWaitIdle(device)
        self._destroy_images()
        self.extent = (width, height)
        self.stale = False
        if width == 0 or height == 0:
            self.destroy()
            return
        # One image more than the minimum, so that the next one is free sooner
        self.min_image_count = capabilities.minImageCount + 1
        if capabilities.maxImageCount:
            self.min_image_count = min(self.min_image_count, capabilities.maxImageCount)
        composite_alpha = next(bit for bit in (1, 8, 2, 4) if capabilities.supportedCompositeAlpha & bit)
        old = self.handle
        handle = c_void_p()
        _check(vk.vkCreateSwapchainKHR(device, byref(_SwapchainCreateInfoKHR(
            sType=_VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, surface=self.surface,
            minImageCount=self.min_image_count, imageFormat=self.color_format, imageColorSpace=self.color_space,
            imageExtent=_Extent2D(width, height), imageArrayLayers=1,
            imageUsage=_VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, preTransform=capabilities.currentTransform,
            compositeAlpha=composite_alpha, presentMode=self.present_mode, clipped=True, oldSwapchain=old)),
            None, byref(handle)), "vkCreateSwapchainKHR")
        if old:
            vk.vkDestroySwapchainKHR(device, old, None)
        self.handle = handle.value
        self.images = [image or 0 for image in _enumerate(vk.vkGetSwapchainImagesKHR, c_void_p, device, self.handle)]
        for image in self.images:
            view = c_void_p()
            _check(vk.vkCreateImageView(device, byref(_ImageViewCreateInfo(
                sType=_VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, image=image, viewType=_VK_IMAGE_VIEW_TYPE_2D,
                format=self.color_format, subresourceRange=_COLOR_SUBRESOURCE_RANGE)), None, byref(view)),
                "vkCreateImageView")
            self.views.append(view.value)
            self.render_complete.append(_create_semaphore(vk, device))

    def _destroy_images(self):
        for view in self.views:
            self.vk.vkDestroyImageView(self.device, view, None)
        for semaphore in self.render_complete:
            self.vk.vkDestroySemaphore(self.device, semaphore, None)
        self.images, self.views, self.render_complete = [], [], []

    def destroy(self):
        self._destroy_images()
        if self.handle:
            self.vk.vkDestroySwapchainKHR(self.device, self.handle, None)
        self.handle = None


@dataclass
class _Frame:
    """The resources of a frame in flight"""

    command_pool: int
    command_buffer: int
    fence: int
    """Signaled once the GPU is done with the frame"""
    image_acquired: int
    """The semaphore that drawing waits for, signaled once the image is free"""


class _Frames:
    """A _Frame for each slot of FRAMES_IN_FLIGHT"""

    def __init__(self, vk, device, queue_family):
        self.vk, self.device = vk, device
        self.slots = []
        for _ in range(FRAMES_IN_FLIGHT):
            command_pool = c_void_p()
            _check(vk.vkCreateCommandPool(device, byref(_CommandPoolCreateInfo(
                _VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, None, 0, queue_family)), None, byref(command_pool)),
                "vkCreateCommandPool")
            command_buffer = c_void_p()
            _check(vk.vkAllocateCommandBuffers(device, byref(_CommandBufferAllocateInfo(
                _VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, None, command_pool, _VK_COMMAND_BUFFER_LEVEL_PRIMARY,
                1)), byref(command_buffer)), "vkAllocateCommandBuffers")
            fence = c_void_p()
            # Signaled, as if a frame before the first were done
            _check(vk.vkCreateFence(device, byref(_FenceCreateInfo(
                _VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, None, _VK_FENCE_CREATE_SIGNALED_BIT)), None, byref(fence)),
                "vkCreateFence")
            self.slots.append(_Frame(command_pool.value, command_buffer.value, fence.value,
                                     _create_semaphore(vk, device)))

    def destroy(self):
        for frame in self.slots:
            self.vk.vkDestroySemaphore(self.device, frame.image_acquired, None)
            self.vk.vkDestroyFence(self.device, frame.fence, None)
            # Frees the command buffer as well
            self.vk.vkDestroyCommandPool(self.device, frame.command_pool, None)
        self.slots = []


def _create_instance(get_instance_proc_addr, extensions, validation):
    """A VkInstance of API_VERSION, with the extensions that SDL3 needs for the
    window's surface and `extensions`"""
    enumerate_version = get_instance_proc_addr(None, b"vkEnumerateInstanceVersion")
    version = c_uint32((1 << 22) | (0 << 12))
    if enumerate_version:
        ctypes.CFUNCTYPE(c_int32, POINTER(c_uint32))(enumerate_version)(byref(version))
    if version.value < API_VERSION:
        raise RuntimeError(f"Vulkan 1.3 is not available: the Vulkan library is {_version_string(version.value)}")

    count = c_uint32()
    names = sdl3.SDL_Vulkan_GetInstanceExtensions(byref(count))
    if not names:
        raise RuntimeError(f"SDL_Vulkan_GetInstanceExtensions(): {sdl3.SDL_GetError().decode()}")
    wanted = [names[i] for i in range(count.value)] + [name.encode() for name in extensions]
    enumerate_extensions = ctypes.CFUNCTYPE(c_int32, c_char_p, POINTER(c_uint32), POINTER(_ExtensionProperties))(
        get_instance_proc_addr(None, b"vkEnumerateInstanceExtensionProperties"))
    available = {extension.extensionName for extension in _enumerate(enumerate_extensions, _ExtensionProperties, None)}
    flags = 0
    # Vulkan on top of other APIs (MoltenVK) only shows with this extension
    if b"VK_KHR_portability_enumeration" in available:
        wanted.append(b"VK_KHR_portability_enumeration")
        flags |= _VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR
    missing = [name.decode() for name in wanted if name not in available]
    if missing:
        raise RuntimeError(f"The Vulkan instance extensions {missing} are not available")
    wanted = list(dict.fromkeys(wanted))
    layers = [b"VK_LAYER_KHRONOS_validation"] if validation else []
    if validation:
        enumerate_layers = ctypes.CFUNCTYPE(c_int32, POINTER(c_uint32), POINTER(_LayerProperties))(
            get_instance_proc_addr(None, b"vkEnumerateInstanceLayerProperties"))
        if layers[0] not in {layer.layerName for layer in _enumerate(enumerate_layers, _LayerProperties)}:
            raise RuntimeError("The Vulkan validation layer is not installed (it comes with the Vulkan SDK)")

    application = _ApplicationInfo(_VK_STRUCTURE_TYPE_APPLICATION_INFO, None, b"imgui", 1, b"imgui", 1, API_VERSION)
    instance = c_void_p()
    create = ctypes.CFUNCTYPE(c_int32, POINTER(_InstanceCreateInfo), c_void_p, POINTER(c_void_p))(
        get_instance_proc_addr(None, b"vkCreateInstance"))
    _check(create(byref(_InstanceCreateInfo(
        _VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, None, flags, ctypes.pointer(application),
        len(layers), (c_char_p * len(layers))(*layers), len(wanted), (c_char_p * len(wanted))(*wanted))),
        None, byref(instance)), "vkCreateInstance")
    return instance.value


def _select_physical_device(vk, instance, surface, extensions):
    """The physical device to draw with, the index of a queue family of it that draws
    and presents to `surface`, and the device extensions to enable: the first device
    that has a such family, Vulkan 1.3 and the extensions, preferring discrete GPUs"""
    candidates = []
    for physical_device in _enumerate(vk.vkEnumeratePhysicalDevices, c_void_p, instance):
        properties = _PhysicalDeviceProperties()
        vk.vkGetPhysicalDeviceProperties(physical_device, byref(properties))
        available = {extension.extensionName for extension in
                     _enumerate(vk.vkEnumerateDeviceExtensionProperties, _ExtensionProperties, physical_device, None)}
        wanted = [b"VK_KHR_swapchain"] + [name.encode() for name in extensions]
        # Vulkan on top of other APIs (MoltenVK) has to be told that it is known to be so
        if b"VK_KHR_portability_subset" in available:
            wanted.append(b"VK_KHR_portability_subset")
        if properties.apiVersion < API_VERSION or not set(wanted) <= available:
            continue
        families = _enumerate(vk.vkGetPhysicalDeviceQueueFamilyProperties, _QueueFamilyProperties, physical_device)
        for index, family in enumerate(families):
            supported = c_uint32()
            vk.vkGetPhysicalDeviceSurfaceSupportKHR(physical_device, index, surface, byref(supported))
            if family.queueFlags & _VK_QUEUE_GRAPHICS_BIT and supported.value:
                preference = {_VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU: 0,
                              _VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU: 1}.get(properties.deviceType, 2)
                candidates.append((preference, len(candidates), physical_device, index, list(dict.fromkeys(wanted))))
                break
    if not candidates:
        raise RuntimeError("No Vulkan 1.3 device can draw into the window"
                           + (f" with the extensions {list(extensions)}" if extensions else ""))
    return min(candidates)[2:]


def _create_device(vk, physical_device, queue_family, extensions):
    """A VkDevice with a queue of `queue_family`, dynamic rendering and `extensions`"""
    priority = c_float(1.0)
    queue_info = _DeviceQueueCreateInfo(_VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, None, 0, queue_family, 1,
                                        ctypes.pointer(priority))
    dynamic_rendering = _PhysicalDeviceDynamicRenderingFeatures(
        _VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES, None, True)
    device = c_void_p()
    _check(vk.vkCreateDevice(physical_device, byref(_DeviceCreateInfo(
        _VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, ctypes.cast(ctypes.pointer(dynamic_rendering), c_void_p), 0, 1,
        ctypes.pointer(queue_info), 0, None, len(extensions), (c_char_p * len(extensions))(*extensions), None)),
        None, byref(device)), "vkCreateDevice")
    return device.value


def _select_surface_format(vk, physical_device, surface):
    """The VkFormat and VkColorSpaceKHR of the window's images: 8-bit BGRA or RGBA,
    which Dear ImGui's colors are meant for, if available"""
    formats = _enumerate(vk.vkGetPhysicalDeviceSurfaceFormatsKHR, _SurfaceFormatKHR, physical_device, surface)
    for wanted in (_VK_FORMAT_B8G8R8A8_UNORM, _VK_FORMAT_R8G8B8A8_UNORM):
        for surface_format in formats:
            if surface_format.format == wanted and surface_format.colorSpace == _VK_COLOR_SPACE_SRGB_NONLINEAR_KHR:
                return surface_format.format, surface_format.colorSpace
    if len(formats) == 1 and formats[0].format == _VK_FORMAT_UNDEFINED:  # any format
        return _VK_FORMAT_B8G8R8A8_UNORM, formats[0].colorSpace
    return formats[0].format, formats[0].colorSpace


def _select_present_mode(vk, physical_device, surface, vsync):
    """FIFO with vsync (which every device supports), else the first of mailbox and
    immediate that the window supports"""
    modes = list(_enumerate(vk.vkGetPhysicalDeviceSurfacePresentModesKHR, c_int32, physical_device, surface))
    if not vsync:
        for mode in (_VK_PRESENT_MODE_MAILBOX_KHR, _VK_PRESENT_MODE_IMMEDIATE_KHR):
            if mode in modes:
                return mode
    return _VK_PRESENT_MODE_FIFO_KHR


def _image_barrier(vk, command_buffer, image, src_stage, src_access, old_layout, dst_stage, dst_access, new_layout):
    barrier = _ImageMemoryBarrier(_VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, None, src_access, dst_access, old_layout,
                                  new_layout, _VK_QUEUE_FAMILY_IGNORED, _VK_QUEUE_FAMILY_IGNORED, image,
                                  _COLOR_SUBRESOURCE_RANGE)
    vk.vkCmdPipelineBarrier(command_buffer, src_stage, dst_stage, 0, 0, None, 0, None, 1, byref(barrier))


def _create_semaphore(vk, device):
    semaphore = c_void_p()
    _check(vk.vkCreateSemaphore(device, byref(_SemaphoreCreateInfo(_VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO, None, 0)),
                                None, byref(semaphore)), "vkCreateSemaphore")
    return semaphore.value


def _window_size_in_pixels(window):
    width, height = ctypes.c_int(), ctypes.c_int()
    sdl3.SDL_GetWindowSizeInPixels(window, byref(width), byref(height))
    return width.value, height.value


def _enumerate(function, item_type, *args):
    """The items that a Vulkan function enumerates, which it is called twice for:
    for their number, and for the items"""
    name = getattr(function, "__name__", "A Vulkan enumeration")
    count = c_uint32()
    _check(function(*args, byref(count), None), name)
    items = (item_type * count.value)()
    _check(function(*args, byref(count), items), name)
    return items[:count.value]


def _version_string(version):
    return f"{version >> 22}.{(version >> 12) & 0x3FF}.{version & 0xFFF}"


def _check(result, what):
    # A function without a VkResult returns None
    if result is not None and result < 0:
        raise RuntimeError(f"{what}() failed: VkResult {result}")
    return result


def _check_vk_result(result):
    """The CheckVkResultFn of imgui_impl_vulkan"""
    _check(result, "A Vulkan function of imgui_impl_vulkan")


# The Vulkan API that the module uses, as ctypes declares it. Handles are c_void_p.

_VK_STRUCTURE_TYPE_APPLICATION_INFO = 0
_VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO = 1
_VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO = 2
_VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO = 3
_VK_STRUCTURE_TYPE_SUBMIT_INFO = 4
_VK_STRUCTURE_TYPE_FENCE_CREATE_INFO = 8
_VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO = 9
_VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO = 15
_VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO = 39
_VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO = 40
_VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO = 42
_VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER = 45
_VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR = 1000001000
_VK_STRUCTURE_TYPE_PRESENT_INFO_KHR = 1000001001
_VK_STRUCTURE_TYPE_RENDERING_INFO = 1000044000
_VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO = 1000044001
_VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES = 1000044003
_VK_SUBOPTIMAL_KHR = 1000001003
_VK_ERROR_OUT_OF_DATE_KHR = -1000001004
_VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR = 0x1
_VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU = 1
_VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU = 2
_VK_QUEUE_GRAPHICS_BIT = 0x1
_VK_QUEUE_FAMILY_IGNORED = 0xFFFFFFFF
_VK_FORMAT_UNDEFINED = 0
_VK_FORMAT_R8G8B8A8_UNORM = 37
_VK_FORMAT_B8G8R8A8_UNORM = 44
_VK_COLOR_SPACE_SRGB_NONLINEAR_KHR = 0
_VK_PRESENT_MODE_IMMEDIATE_KHR = 0
_VK_PRESENT_MODE_MAILBOX_KHR = 1
_VK_PRESENT_MODE_FIFO_KHR = 2
_VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT = 0x10
_VK_IMAGE_VIEW_TYPE_2D = 1
_VK_IMAGE_ASPECT_COLOR_BIT = 0x1
_VK_IMAGE_LAYOUT_UNDEFINED = 0
_VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL = 2
_VK_IMAGE_LAYOUT_PRESENT_SRC_KHR = 1000001002
_VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT = 0x100
_VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT = 0x400
_VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT = 0x2000
_VK_ATTACHMENT_LOAD_OP_CLEAR = 1
_VK_ATTACHMENT_STORE_OP_STORE = 0
_VK_COMMAND_BUFFER_LEVEL_PRIMARY = 0
_VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT = 0x1
_VK_FENCE_CREATE_SIGNALED_BIT = 0x1
_NO_TIMEOUT = 0xFFFFFFFFFFFFFFFF


def _struct(name, *fields):
    return type(name, (ctypes.Structure,), {"_fields_": list(fields)})


_Extent2D = _struct("VkExtent2D", ("width", c_uint32), ("height", c_uint32))
_Rect2D = _struct("VkRect2D", ("offset", c_int32 * 2), ("extent", _Extent2D))
_ApplicationInfo = _struct(
    "VkApplicationInfo", ("sType", c_int32), ("pNext", c_void_p), ("pApplicationName", c_char_p),
    ("applicationVersion", c_uint32), ("pEngineName", c_char_p), ("engineVersion", c_uint32),
    ("apiVersion", c_uint32))
_InstanceCreateInfo = _struct(
    "VkInstanceCreateInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32),
    ("pApplicationInfo", POINTER(_ApplicationInfo)), ("enabledLayerCount", c_uint32),
    ("ppEnabledLayerNames", POINTER(c_char_p)), ("enabledExtensionCount", c_uint32),
    ("ppEnabledExtensionNames", POINTER(c_char_p)))
_ExtensionProperties = _struct("VkExtensionProperties", ("extensionName", c_char * 256), ("specVersion", c_uint32))
_LayerProperties = _struct(
    "VkLayerProperties", ("layerName", c_char * 256), ("specVersion", c_uint32),
    ("implementationVersion", c_uint32), ("description", c_char * 256))
# Followed by VkPhysicalDeviceLimits and VkPhysicalDeviceSparseProperties, which the
# padding leaves room for
_PhysicalDeviceProperties = _struct(
    "VkPhysicalDeviceProperties", ("apiVersion", c_uint32), ("driverVersion", c_uint32), ("vendorID", c_uint32),
    ("deviceID", c_uint32), ("deviceType", c_int32), ("deviceName", c_char * 256), ("pipelineCacheUUID", c_char * 16),
    ("limitsAndSparseProperties", c_uint64 * 256))
_QueueFamilyProperties = _struct(
    "VkQueueFamilyProperties", ("queueFlags", c_uint32), ("queueCount", c_uint32), ("timestampValidBits", c_uint32),
    ("minImageTransferGranularity", c_uint32 * 3))
_DeviceQueueCreateInfo = _struct(
    "VkDeviceQueueCreateInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32),
    ("queueFamilyIndex", c_uint32), ("queueCount", c_uint32), ("pQueuePriorities", POINTER(c_float)))
_PhysicalDeviceDynamicRenderingFeatures = _struct(
    "VkPhysicalDeviceDynamicRenderingFeatures", ("sType", c_int32), ("pNext", c_void_p), ("dynamicRendering", c_uint32))
_DeviceCreateInfo = _struct(
    "VkDeviceCreateInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32),
    ("queueCreateInfoCount", c_uint32), ("pQueueCreateInfos", POINTER(_DeviceQueueCreateInfo)),
    ("enabledLayerCount", c_uint32), ("ppEnabledLayerNames", POINTER(c_char_p)),
    ("enabledExtensionCount", c_uint32), ("ppEnabledExtensionNames", POINTER(c_char_p)),
    ("pEnabledFeatures", c_void_p))
_SurfaceCapabilitiesKHR = _struct(
    "VkSurfaceCapabilitiesKHR", ("minImageCount", c_uint32), ("maxImageCount", c_uint32),
    ("currentExtent", _Extent2D), ("minImageExtent", _Extent2D), ("maxImageExtent", _Extent2D),
    ("maxImageArrayLayers", c_uint32), ("supportedTransforms", c_uint32), ("currentTransform", c_uint32),
    ("supportedCompositeAlpha", c_uint32), ("supportedUsageFlags", c_uint32))
_SurfaceFormatKHR = _struct("VkSurfaceFormatKHR", ("format", c_int32), ("colorSpace", c_int32))
_SwapchainCreateInfoKHR = _struct(
    "VkSwapchainCreateInfoKHR", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32), ("surface", c_void_p),
    ("minImageCount", c_uint32), ("imageFormat", c_int32), ("imageColorSpace", c_int32), ("imageExtent", _Extent2D),
    ("imageArrayLayers", c_uint32), ("imageUsage", c_uint32), ("imageSharingMode", c_int32),
    ("queueFamilyIndexCount", c_uint32), ("pQueueFamilyIndices", POINTER(c_uint32)), ("preTransform", c_uint32),
    ("compositeAlpha", c_uint32), ("presentMode", c_int32), ("clipped", c_uint32), ("oldSwapchain", c_void_p))
_ImageSubresourceRange = _struct(
    "VkImageSubresourceRange", ("aspectMask", c_uint32), ("baseMipLevel", c_uint32), ("levelCount", c_uint32),
    ("baseArrayLayer", c_uint32), ("layerCount", c_uint32))
_COLOR_SUBRESOURCE_RANGE = _ImageSubresourceRange(_VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1)
_ImageViewCreateInfo = _struct(
    "VkImageViewCreateInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32), ("image", c_void_p),
    ("viewType", c_int32), ("format", c_int32), ("components", c_int32 * 4),
    ("subresourceRange", _ImageSubresourceRange))
_CommandPoolCreateInfo = _struct(
    "VkCommandPoolCreateInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32),
    ("queueFamilyIndex", c_uint32))
_CommandBufferAllocateInfo = _struct(
    "VkCommandBufferAllocateInfo", ("sType", c_int32), ("pNext", c_void_p), ("commandPool", c_void_p),
    ("level", c_int32), ("commandBufferCount", c_uint32))
_CommandBufferBeginInfo = _struct(
    "VkCommandBufferBeginInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32),
    ("pInheritanceInfo", c_void_p))
_SemaphoreCreateInfo = _struct("VkSemaphoreCreateInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32))
_FenceCreateInfo = _struct("VkFenceCreateInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32))
_ImageMemoryBarrier = _struct(
    "VkImageMemoryBarrier", ("sType", c_int32), ("pNext", c_void_p), ("srcAccessMask", c_uint32),
    ("dstAccessMask", c_uint32), ("oldLayout", c_int32), ("newLayout", c_int32), ("srcQueueFamilyIndex", c_uint32),
    ("dstQueueFamilyIndex", c_uint32), ("image", c_void_p), ("subresourceRange", _ImageSubresourceRange))
_RenderingAttachmentInfo = _struct(
    "VkRenderingAttachmentInfo", ("sType", c_int32), ("pNext", c_void_p), ("imageView", c_void_p),
    ("imageLayout", c_int32), ("resolveMode", c_uint32), ("resolveImageView", c_void_p),
    ("resolveImageLayout", c_int32), ("loadOp", c_int32), ("storeOp", c_int32), ("clearValue", c_float * 4))
_RenderingInfo = _struct(
    "VkRenderingInfo", ("sType", c_int32), ("pNext", c_void_p), ("flags", c_uint32), ("renderArea", _Rect2D),
    ("layerCount", c_uint32), ("viewMask", c_uint32), ("colorAttachmentCount", c_uint32),
    ("pColorAttachments", POINTER(_RenderingAttachmentInfo)), ("pDepthAttachment", c_void_p),
    ("pStencilAttachment", c_void_p))
_SubmitInfo = _struct(
    "VkSubmitInfo", ("sType", c_int32), ("pNext", c_void_p), ("waitSemaphoreCount", c_uint32),
    ("pWaitSemaphores", POINTER(c_void_p)), ("pWaitDstStageMask", POINTER(c_uint32)),
    ("commandBufferCount", c_uint32), ("pCommandBuffers", POINTER(c_void_p)), ("signalSemaphoreCount", c_uint32),
    ("pSignalSemaphores", POINTER(c_void_p)))
_PresentInfoKHR = _struct(
    "VkPresentInfoKHR", ("sType", c_int32), ("pNext", c_void_p), ("waitSemaphoreCount", c_uint32),
    ("pWaitSemaphores", POINTER(c_void_p)), ("swapchainCount", c_uint32), ("pSwapchains", POINTER(c_void_p)),
    ("pImageIndices", POINTER(c_uint32)), ("pResults", POINTER(c_int32)))

_PFN_vkGetInstanceProcAddr = ctypes.CFUNCTYPE(c_void_p, c_void_p, c_char_p)
_VkResult = c_int32
_H = c_void_p  # A handle
_P = POINTER

# The functions of the instance, and of the device, by their result and argument types
_INSTANCE_FUNCTIONS = {
    "vkDestroyInstance": (None, _H, _H),
    "vkEnumeratePhysicalDevices": (_VkResult, _H, _P(c_uint32), _P(_H)),
    "vkGetPhysicalDeviceProperties": (None, _H, _P(_PhysicalDeviceProperties)),
    "vkGetPhysicalDeviceQueueFamilyProperties": (None, _H, _P(c_uint32), _P(_QueueFamilyProperties)),
    "vkEnumerateDeviceExtensionProperties": (_VkResult, _H, c_char_p, _P(c_uint32), _P(_ExtensionProperties)),
    "vkGetPhysicalDeviceSurfaceSupportKHR": (_VkResult, _H, c_uint32, _H, _P(c_uint32)),
    "vkGetPhysicalDeviceSurfaceCapabilitiesKHR": (_VkResult, _H, _H, _P(_SurfaceCapabilitiesKHR)),
    "vkGetPhysicalDeviceSurfaceFormatsKHR": (_VkResult, _H, _H, _P(c_uint32), _P(_SurfaceFormatKHR)),
    "vkGetPhysicalDeviceSurfacePresentModesKHR": (_VkResult, _H, _H, _P(c_uint32), _P(c_int32)),
    "vkCreateDevice": (_VkResult, _H, _P(_DeviceCreateInfo), _H, _P(_H)),
    "vkGetDeviceProcAddr": (c_void_p, _H, c_char_p),
}
_DEVICE_FUNCTIONS = {
    "vkDestroyDevice": (None, _H, _H),
    "vkGetDeviceQueue": (None, _H, c_uint32, c_uint32, _P(_H)),
    "vkDeviceWaitIdle": (_VkResult, _H),
    "vkCreateSwapchainKHR": (_VkResult, _H, _P(_SwapchainCreateInfoKHR), _H, _P(_H)),
    "vkDestroySwapchainKHR": (None, _H, _H, _H),
    "vkGetSwapchainImagesKHR": (_VkResult, _H, _H, _P(c_uint32), _P(_H)),
    "vkAcquireNextImageKHR": (_VkResult, _H, _H, c_uint64, _H, _H, _P(c_uint32)),
    "vkQueuePresentKHR": (_VkResult, _H, _P(_PresentInfoKHR)),
    "vkCreateImageView": (_VkResult, _H, _P(_ImageViewCreateInfo), _H, _P(_H)),
    "vkDestroyImageView": (None, _H, _H, _H),
    "vkCreateCommandPool": (_VkResult, _H, _P(_CommandPoolCreateInfo), _H, _P(_H)),
    "vkDestroyCommandPool": (None, _H, _H, _H),
    "vkResetCommandPool": (_VkResult, _H, _H, c_uint32),
    "vkAllocateCommandBuffers": (_VkResult, _H, _P(_CommandBufferAllocateInfo), _P(_H)),
    "vkBeginCommandBuffer": (_VkResult, _H, _P(_CommandBufferBeginInfo)),
    "vkEndCommandBuffer": (_VkResult, _H),
    "vkCmdPipelineBarrier": (None, _H, c_uint32, c_uint32, c_uint32, c_uint32, c_void_p, c_uint32, c_void_p,
                             c_uint32, _P(_ImageMemoryBarrier)),
    "vkCmdBeginRendering": (None, _H, _P(_RenderingInfo)),
    "vkCmdEndRendering": (None, _H),
    "vkQueueSubmit": (_VkResult, _H, c_uint32, _P(_SubmitInfo), _H),
    "vkCreateSemaphore": (_VkResult, _H, _P(_SemaphoreCreateInfo), _H, _P(_H)),
    "vkDestroySemaphore": (None, _H, _H, _H),
    "vkCreateFence": (_VkResult, _H, _P(_FenceCreateInfo), _H, _P(_H)),
    "vkDestroyFence": (None, _H, _H, _H),
    "vkWaitForFences": (_VkResult, _H, c_uint32, _P(_H), c_uint32, c_uint64),
    "vkResetFences": (_VkResult, _H, c_uint32, _P(_H)),
}


class _Vulkan:
    """The Vulkan functions of `_INSTANCE_FUNCTIONS` and `_DEVICE_FUNCTIONS`, as
    attributes, looked up with vkGetInstanceProcAddr and vkGetDeviceProcAddr"""

    def __init__(self, get_instance_proc_addr, instance):
        get_instance_proc_addr = _PFN_vkGetInstanceProcAddr(get_instance_proc_addr)
        for name, (result, *arguments) in _INSTANCE_FUNCTIONS.items():
            self._load(name, get_instance_proc_addr(instance, name.encode()), result, arguments)

    def load_device_functions(self, device):
        for name, (result, *arguments) in _DEVICE_FUNCTIONS.items():
            self._load(name, self.vkGetDeviceProcAddr(device, name.encode()), result, arguments)

    def _load(self, name, address, result, arguments):
        if not address:
            raise RuntimeError(f"The Vulkan function {name} is not available")
        function = ctypes.CFUNCTYPE(result, *arguments)(address)
        function.__name__ = name
        setattr(self, name, function)
