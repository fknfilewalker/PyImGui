# Stubs for backends/imgui_impl_metal.h (macOS only), whose bindings are written by
# hand in src/bindings/imgui_impl_metal_bindings.mm.
#
# The Metal objects are PyObjC objects (pip install pyobjc-framework-Metal),
# ctypes pointers or plain addresses.
from __future__ import annotations

from typing import Any

from . import ImDrawData, ImTextureData

MetalObject = Any

def init(device: MetalObject) -> bool: ...
def shutdown() -> None: ...
def new_frame(render_pass_descriptor: MetalObject) -> None: ...
def render_draw_data(draw_data: ImDrawData, command_buffer: MetalObject, command_encoder: MetalObject) -> None: ...
def create_device_objects(device: MetalObject) -> bool: ...
def destroy_device_objects() -> None: ...
def update_texture(tex: ImTextureData) -> None:
    """(Advanced) Use e.g. if you need to precisely control the timing of texture updates (e.g. for staged rendering), by setting ImDrawData::Textures = None to handle this manually."""

def texture_id(texture: MetalObject) -> int:
    """The ImTextureID of an MTLTexture, for imgui.image(). The texture must outlive the frames that draw it."""
