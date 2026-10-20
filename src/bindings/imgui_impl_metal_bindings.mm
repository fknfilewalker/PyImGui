// Bindings for backends/imgui_impl_metal.h and backends/imgui_impl_metal4.h, written
// by hand: their API is Objective-C only, which litgen cannot parse. Compiled on
// macOS only (see CMakeLists.txt).

#include <cstring>
#include <stdexcept>
#include <string>

#include <nanobind/nanobind.h>

#include "imgui.h"
#include "imgui_impl_metal.h"
#include "imgui_impl_metal4.h"

#import <Metal/Metal.h>

namespace nb = nanobind;

// Metal objects come from Python as PyObjC proxies (e.g. Metal.MTLCreateSystemDefaultDevice()),
// as ctypes pointers or as plain addresses: returns the address of the object
static void* objc_address(nb::handle obj, const char* name)
{
    if (PyLong_Check(obj.ptr())) {
        void* address = PyLong_AsVoidPtr(obj.ptr());
        if (PyErr_Occurred())
            throw nb::python_error();
        return address;
    }
    if (!obj.is_none()) {
        // PyObjC's proxies convert to a ctypes.c_void_p, and ctypes' pointers have a value
        nb::object pointer = nb::hasattr(obj, "__c_void_p__") ? obj.attr("__c_void_p__")() : nb::borrow(obj);
        if (nb::hasattr(pointer, "value")) {
            nb::object value = pointer.attr("value");
            if (value.is_none())
                return nullptr;
            if (PyLong_Check(value.ptr()))
                return PyLong_AsVoidPtr(value.ptr());
        }
    }
    throw nb::type_error((std::string(name) + " must be a PyObjC object, a ctypes pointer or an address").c_str());
}

template <typename T> static T objc_object(nb::handle obj, const char* name)
{
    void* address = objc_address(obj, name);
    if (!address)
        throw nb::value_error((std::string(name) + " must not be nil").c_str());
    return (__bridge T)address;
}

static ImTextureID texture_id(nb::handle texture)
{
    return (ImTextureID)(intptr_t)objc_address(texture, "texture");
}

static const char* texture_id_doc =
    "The ImTextureID of an MTLTexture (a PyObjC object, a ctypes pointer or an address), for imgui.image().\n"
    "The texture must outlive the frames that draw it.";

void py_init_module_imgui_impl_metal(nb::module_& m)
{
    m.def("init",
        [](nb::handle device) { return ImGui_ImplMetal_Init(objc_object<id<MTLDevice>>(device, "device")); },
        nb::arg("device"));

    m.def("shutdown", ImGui_ImplMetal_Shutdown);

    m.def("new_frame",
        [](nb::handle render_pass_descriptor)
        {
            ImGui_ImplMetal_NewFrame(objc_object<MTLRenderPassDescriptor*>(render_pass_descriptor, "render_pass_descriptor"));
        },
        nb::arg("render_pass_descriptor"));

    m.def("render_draw_data",
        [](ImDrawData* draw_data, nb::handle command_buffer, nb::handle command_encoder)
        {
            ImGui_ImplMetal_RenderDrawData(draw_data,
                                           objc_object<id<MTLCommandBuffer>>(command_buffer, "command_buffer"),
                                           objc_object<id<MTLRenderCommandEncoder>>(command_encoder, "command_encoder"));
        },
        nb::arg("draw_data"), nb::arg("command_buffer"), nb::arg("command_encoder"));

    m.def("create_device_objects",
        [](nb::handle device) { return ImGui_ImplMetal_CreateDeviceObjects(objc_object<id<MTLDevice>>(device, "device")); },
        nb::arg("device"));

    m.def("destroy_device_objects", ImGui_ImplMetal_DestroyDeviceObjects);

    m.def("update_texture", ImGui_ImplMetal_UpdateTexture, nb::arg("tex"),
        "(Advanced) Use e.g. if you need to precisely control the timing of texture updates (e.g. for staged rendering), by setting ImDrawData::Textures = None to handle this manually.");

    m.def("texture_id", texture_id, nb::arg("texture"), texture_id_doc);
}

// The backend adds every texture that it draws to its residency set, which retains
// them, and never removes one. Its data is a struct whose first member is its
// MetalContext, which has the set as property residencySet.
static void metal4_forget_texture(nb::handle texture)
{
    id<MTLTexture> tex = objc_object<id<MTLTexture>>(texture, "texture");
    ImGuiIO& io = ImGui::GetIO();
    if (!io.BackendRendererName || strcmp(io.BackendRendererName, "imgui_impl_metal4") != 0)
        throw std::runtime_error("imgui_impl_metal4 is not the renderer backend");
    id context = (__bridge id)(*(void**)io.BackendRendererUserData);
    id<MTLResidencySet> residency_set = [context valueForKey:@"residencySet"];
    [residency_set removeAllocation:tex];
    [residency_set commit];
}

void py_init_module_imgui_impl_metal4(nb::module_& m)
{
    m.def("init",
        [](nb::handle device, nb::handle command_queue, int frames_in_flight) -> bool
        {
            return ImGui_ImplMetal4_Init(objc_object<id<MTLDevice>>(device, "device"),
                                         objc_object<id<MTL4CommandQueue>>(command_queue, "command_queue"),
                                         frames_in_flight);
        },
        nb::arg("device"), nb::arg("command_queue"), nb::arg("frames_in_flight"),
        "frames_in_flight must match the number of frames that the application keeps in flight. The backend uses it to\n"
        "size its own per-frame-in-flight resources, so that the CPU never overwrites a slot the GPU may still be reading.");

    m.def("shutdown", ImGui_ImplMetal4_Shutdown);

    m.def("new_frame",
        [](nb::handle render_pass_descriptor, int frame_in_flight_index)
        {
            ImGui_ImplMetal4_NewFrame(
                objc_object<MTL4RenderPassDescriptor*>(render_pass_descriptor, "render_pass_descriptor"),
                frame_in_flight_index);
        },
        nb::arg("render_pass_descriptor"), nb::arg("frame_in_flight_index"),
        "frame_in_flight_index must match the slot of the application's own per-frame-in-flight resources (e.g. of its\n"
        "command buffer/allocator), and be less than the frames_in_flight passed to init().");

    m.def("render_draw_data",
        [](ImDrawData* draw_data, nb::handle command_buffer, nb::handle command_encoder)
        {
            ImGui_ImplMetal4_RenderDrawData(
                draw_data,
                objc_object<id<MTL4CommandBuffer>>(command_buffer, "command_buffer"),
                objc_object<id<MTL4RenderCommandEncoder>>(command_encoder, "command_encoder"));
        },
        nb::arg("draw_data"), nb::arg("command_buffer"), nb::arg("command_encoder"));

    m.def("create_device_objects",
        [](nb::handle device) -> bool
        {
            return ImGui_ImplMetal4_CreateDeviceObjects(objc_object<id<MTLDevice>>(device, "device"));
        },
        nb::arg("device"));

    m.def("destroy_device_objects", ImGui_ImplMetal4_DestroyDeviceObjects);

    m.def("update_texture", ImGui_ImplMetal4_UpdateTexture, nb::arg("tex"),
        "(Advanced) Use e.g. if you need to precisely control the timing of texture updates (e.g. for staged rendering), by setting ImDrawData::Textures = None to handle this manually.");

    m.def("texture_id", texture_id, nb::arg("texture"), texture_id_doc);

    m.def("forget_texture", metal4_forget_texture, nb::arg("texture"),
        "Removes a user texture from the backend's residency set, to which the backend adds (and so retains) every\n"
        "texture that it draws. Call it once the texture is no longer drawn, and the GPU is done with the frames that did.");
}
