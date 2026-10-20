# Stubs for backends/imgui_impl_vulkan.h, whose bindings are written by hand in
# src/bindings/imgui_impl_vulkan_bindings.cpp.
#
# Vulkan's handles are ints (addresses), ctypes objects (c_void_p, pointers,
# pointer-sized integers) or objects that int() converts, such as cffi's pointers;
# None is VK_NULL_HANDLE. Handles that the functions return are ints. Vulkan's enums
# and flags are ints.
from __future__ import annotations

from typing import Any, Callable, List, Optional, Union

from . import ImDrawData, ImTextureData

VulkanHandle = Any
Buffer = Union[bytes, bytearray, memoryview]

class PipelineInfo:
    """Specify settings to create pipeline and swapchain"""

    render_pass: int
    """For Main viewport only. Ignored if using dynamic rendering"""
    subpass: int
    msaa_samples: int
    """0 defaults to VK_SAMPLE_COUNT_1_BIT"""
    extra_dynamic_states: List[int]
    """Optional, allows to insert more dynamic states into our VkPipeline"""
    color_attachment_formats: List[int]
    """PipelineRenderingCreateInfo.pColorAttachmentFormats, for dynamic rendering. Setting any of the
    PipelineRenderingCreateInfo fields makes the structure valid (sets its sType)."""
    view_mask: int
    depth_attachment_format: int
    stencil_attachment_format: int
    @property
    def has_pipeline_rendering_create_info(self) -> bool:
        """Whether PipelineRenderingCreateInfo is filled in (valid if .sType == VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR)"""
    swap_chain_image_usage: int
    """For Secondary viewports only. Extra flags for vkCreateSwapchainKHR() calls for secondary viewports. We
    automatically add VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT."""

    def __init__(
        self,
        *,
        render_pass: VulkanHandle = ...,
        subpass: int = ...,
        msaa_samples: int = ...,
        extra_dynamic_states: List[int] = ...,
        color_attachment_formats: List[int] = ...,
        view_mask: int = ...,
        depth_attachment_format: int = ...,
        stencil_attachment_format: int = ...,
        swap_chain_image_usage: int = ...,
    ) -> None:
        """Takes the fields as keyword arguments"""

class InitInfo:
    """Initialization data, for ImGui_ImplVulkan_Init()"""

    api_version: int
    """Fill with API version of Instance, e.g. VK_API_VERSION_1_3 or your value of
    VkApplicationInfo::apiVersion. May be lower than header version (VK_HEADER_VERSION_COMPLETE)"""
    instance: int
    physical_device: int
    device: int
    queue_family: int
    queue: int
    descriptor_pool: int
    """Created with VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT, holding enough
    VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER descriptors; ignored if using descriptor_pool_size > 0"""
    descriptor_pool_size: int
    """Optional: set to create internal ImageView descriptor pool automatically instead of using DescriptorPool."""
    min_image_count: int
    """>= 2"""
    image_count: int
    """>= MinImageCount"""
    pipeline_cache: int
    """Optional"""
    pipeline_info_main: PipelineInfo
    """Infos for Main Viewport (created by app/user)"""
    pipeline_info_for_viewports: PipelineInfo
    """Infos for Secondary Viewports (created by backend)"""
    use_dynamic_rendering: bool
    """Need to explicitly enable VK_KHR_dynamic_rendering extension to use this, even for Vulkan 1.3 + setup
    pipeline_info_main's PipelineRenderingCreateInfo fields."""
    check_vk_result_fn: Optional[Callable[[int], None]]
    """Called with the VkResult of the backend's Vulkan calls. Exceptions that it raises propagate to the
    backend function that Python called."""
    min_allocation_size: int
    """Minimum allocation size. Set to 1024*1024 to satisfy zealous best practices validation layer and
    waste a little memory."""
    custom_shader_vert_code: Optional[bytes]
    """SPIR-V code of CustomShaderVertCreateInfo, to customize the default vertex shader (whose inputs and
    outputs it has to match); None for the default"""
    custom_shader_frag_code: Optional[bytes]
    """SPIR-V code of CustomShaderFragCreateInfo, to customize the default fragment shader; None for the default"""

    def __init__(
        self,
        *,
        api_version: int = ...,
        instance: VulkanHandle = ...,
        physical_device: VulkanHandle = ...,
        device: VulkanHandle = ...,
        queue_family: int = ...,
        queue: VulkanHandle = ...,
        descriptor_pool: VulkanHandle = ...,
        descriptor_pool_size: int = ...,
        min_image_count: int = ...,
        image_count: int = ...,
        pipeline_cache: VulkanHandle = ...,
        pipeline_info_main: PipelineInfo = ...,
        pipeline_info_for_viewports: PipelineInfo = ...,
        use_dynamic_rendering: bool = ...,
        check_vk_result_fn: Optional[Callable[[int], None]] = ...,
        min_allocation_size: int = ...,
        custom_shader_vert_code: Optional[Buffer] = ...,
        custom_shader_frag_code: Optional[Buffer] = ...,
    ) -> None:
        """Takes the fields as keyword arguments"""

def init(info: InitInfo) -> bool:
    """Loads the Vulkan functions through SDL3's vkGetInstanceProcAddr first, unless load_functions() did."""

def shutdown() -> None: ...
def new_frame() -> None: ...
def render_draw_data(draw_data: ImDrawData, command_buffer: VulkanHandle, pipeline: VulkanHandle = None) -> None: ...
def set_min_image_count(min_image_count: int) -> None:
    """To override MinImageCount after initialization (e.g. if swap chain is recreated)"""

def create_main_pipeline(info: PipelineInfo) -> None:
    """(Advanced) Use e.g. if you need to recreate pipeline without reinitializing the backend (see #8110, #8111)
    The main window pipeline will be created by ImGui_ImplVulkan_Init() if possible (== RenderPass xor
    (UseDynamicRendering && PipelineRenderingCreateInfo->sType == VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR))
    Else, the pipeline can be created, or re-created, using ImGui_ImplVulkan_CreateMainPipeline() before rendering."""

def update_texture(tex: ImTextureData) -> None:
    """(Advanced) Use e.g. if you need to precisely control the timing of texture updates (e.g. for staged rendering), by setting ImDrawData::Textures = None to handle this manually."""

def add_texture(image_view: VulkanHandle, image_layout: int) -> int:
    """Register a texture (VkDescriptorSet for a VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE == ImTextureID), for
    imgui.image(imgui.ImTextureRef(descriptor_set))."""

def remove_texture(descriptor_set: VulkanHandle) -> None: ...
def load_functions(api_version: int, instance: VulkanHandle, get_instance_proc_addr: Any = None) -> bool:
    """Loads the Vulkan functions of the backend with vkGetInstanceProcAddr(instance, name): the one at the
    address get_instance_proc_addr, or by default SDL3's (SDL_Vulkan_GetVkGetInstanceProcAddr()).
    init() calls it with SDL3's if it has not been called since the last shutdown()."""
