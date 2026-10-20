# Stubs for backends/imgui_impl_metal4.h (macOS only), whose bindings
# are written by hand in src/bindings/imgui_impl_metal_bindings.mm.
#
# The Metal objects are PyObjC objects (pip install pyobjc-framework-Metal),
# ctypes pointers or plain addresses.
from __future__ import annotations

from typing import Any

from . import ImDrawData, ImTextureData

MetalObject = Any

def init(device: MetalObject, command_queue: MetalObject, frames_in_flight: int) -> bool:
    """frames_in_flight must match the number of frames that the application keeps in flight. The backend uses it to
    size its own per-frame-in-flight resources, so that the CPU never overwrites a slot the GPU may still be reading."""

def shutdown() -> None: ...
def new_frame(render_pass_descriptor: MetalObject, frame_in_flight_index: int) -> None:
    """frame_in_flight_index must match the slot of the application's own per-frame-in-flight resources (e.g. of its
    command buffer/allocator), and be less than the frames_in_flight passed to init()."""

def render_draw_data(draw_data: ImDrawData, command_buffer: MetalObject, command_encoder: MetalObject) -> None: ...
def create_device_objects(device: MetalObject) -> bool: ...
def destroy_device_objects() -> None: ...
def update_texture(tex: ImTextureData) -> None:
    """(Advanced) Use e.g. if you need to precisely control the timing of texture updates (e.g. for staged rendering), by setting ImDrawData::Textures = None to handle this manually."""

def texture_id(texture: MetalObject) -> int:
    """The ImTextureID of an MTLTexture, for imgui.image(). The texture must outlive the frames that draw it."""

def forget_texture(texture: MetalObject) -> None:
    """Removes a user texture from the backend's residency set, to which the backend adds (and so retains) every
    texture that it draws. Call it once the texture is no longer drawn, and the GPU is done with the frames that did."""
