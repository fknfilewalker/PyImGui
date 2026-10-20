# Stubs for backends/imgui_impl_dx12.h (Windows only), whose bindings are written by
# hand in src/bindings/imgui_impl_dx12_bindings.cpp.
#
# The Direct3D objects are ints (addresses), ctypes pointers (including comtypes'
# interface pointers) or objects that int() converts; None is null. DXGI_FORMATs are
# ints, and descriptor handles are the ints of their `ptr`.
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from . import ImDrawData, ImTextureData

D3D12Object = Any

class InitInfo:
    """Initialization data, for ImGui_ImplDX12_Init()"""

    device: int
    command_queue: int
    """Command queue used for queuing texture uploads."""
    num_frames_in_flight: int
    rtv_format: int
    """RenderTarget format."""
    dsv_format: int
    """DepthStencilView format."""
    srv_descriptor_heap: int
    """Allocating SRV descriptors for textures is up to the application, so we provide callbacks."""
    srv_descriptor_alloc_fn: Optional[Callable[[], Tuple[Any, Any]]]
    """Called without arguments for a descriptor of srv_descriptor_heap, which it returns as a
    (cpu_desc_handle, gpu_desc_handle) pair: ints (D3D12_*_DESCRIPTOR_HANDLE.ptr), or objects whose .ptr
    they are. Exceptions that it raises propagate to the backend function that Python called."""
    srv_descriptor_free_fn: Optional[Callable[[int, int], None]]
    """Called with the (cpu_desc_handle, gpu_desc_handle) of a descriptor that srv_descriptor_alloc_fn
    returned, as ints, once the backend no longer uses it."""
    legacy_single_srv_cpu_descriptor: int
    """To facilitate transition from single descriptor to allocator callback, you may use those.
    (Without srv_descriptor_alloc_fn, the backend draws a single texture: the font atlas)"""
    legacy_single_srv_gpu_descriptor: int

    def __init__(
        self,
        *,
        device: D3D12Object = ...,
        command_queue: D3D12Object = ...,
        num_frames_in_flight: int = ...,
        rtv_format: int = ...,
        dsv_format: int = ...,
        srv_descriptor_heap: D3D12Object = ...,
        srv_descriptor_alloc_fn: Optional[Callable[[], Tuple[Any, Any]]] = ...,
        srv_descriptor_free_fn: Optional[Callable[[int, int], None]] = ...,
        legacy_single_srv_cpu_descriptor: Any = ...,
        legacy_single_srv_gpu_descriptor: Any = ...,
    ) -> None:
        """Takes the fields as keyword arguments"""

def init(info: InitInfo) -> bool: ...
def shutdown() -> None: ...
def new_frame() -> None: ...
def render_draw_data(draw_data: ImDrawData, graphics_command_list: D3D12Object) -> None: ...
def create_device_objects() -> bool:
    """Use if you want to reset your rendering device without losing Dear ImGui state."""

def invalidate_device_objects() -> None: ...
def update_texture(tex: ImTextureData) -> None:
    """(Advanced) Use e.g. if you need to precisely control the timing of texture updates (e.g. for staged rendering), by setting ImDrawData::Textures = None to handle this manually."""
