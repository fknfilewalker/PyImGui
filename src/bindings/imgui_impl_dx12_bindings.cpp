// Bindings for backends/imgui_impl_dx12.h, written by hand: its InitInfo holds C
// callbacks, which litgen cannot bind to Python functions. Compiled on Windows only
// (see CMakeLists.txt).

#include <map>

#include <nanobind/nanobind.h>

#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include "imgui.h"
#include "imgui_impl_dx12.h"
#include "native_casters.h"

namespace nb = nanobind;
using namespace nb::literals;

namespace nanobind::detail {
template <> struct type_caster<ID3D12Device> : native_pointer_caster<ID3D12Device> {};
template <> struct type_caster<ID3D12CommandQueue> : native_pointer_caster<ID3D12CommandQueue> {};
template <> struct type_caster<ID3D12DescriptorHeap> : native_pointer_caster<ID3D12DescriptorHeap> {};
template <> struct type_caster<ID3D12GraphicsCommandList> : native_pointer_caster<ID3D12GraphicsCommandList> {};
template <> struct type_caster<DXGI_FORMAT> : native_enum_caster<DXGI_FORMAT> {};
} // namespace nanobind::detail

// ImGui_ImplDX12_InitInfo, with the Python functions for its SrvDescriptorAllocFn and
// SrvDescriptorFreeFn, which its UserData points to (the backend copies it)
struct Dx12InitInfo {
    ImGui_ImplDX12_InitInfo info;
    nb::object srv_descriptor_alloc_fn = nb::none(), srv_descriptor_free_fn = nb::none();

    ImGui_ImplDX12_InitInfo resolve();
};

// The InitInfo of each Dear ImGui context whose backend runs, which the backend
// points to until it shuts down. Never destroyed, as it holds Python objects, which
// cannot be released once the interpreter has finalized.
static std::map<ImGuiContext*, Dx12InitInfo>& running = *new std::map<ImGuiContext*, Dx12InitInfo>();

// A descriptor handle, from an int or from a ctypes D3D12_*_DESCRIPTOR_HANDLE
static uint64_t descriptor_handle(nb::handle handle)
{
    return nb::cast<uint64_t>(nb::hasattr(handle, "ptr") ? handle.attr("ptr") : nb::borrow(handle));
}

static void srv_descriptor_alloc(ImGui_ImplDX12_InitInfo* info, D3D12_CPU_DESCRIPTOR_HANDLE* out_cpu_desc_handle,
                                 D3D12_GPU_DESCRIPTOR_HANDLE* out_gpu_desc_handle)
{
    nb::object handles = ((Dx12InitInfo*)info->UserData)->srv_descriptor_alloc_fn();
    if (!nb::isinstance<nb::sequence>(handles) || nb::len(handles) != 2)
        throw nb::type_error("srv_descriptor_alloc_fn must return a (cpu_desc_handle, gpu_desc_handle) pair");
    out_cpu_desc_handle->ptr = (SIZE_T)descriptor_handle(handles[0]);
    out_gpu_desc_handle->ptr = descriptor_handle(handles[1]);
}

static void srv_descriptor_free(ImGui_ImplDX12_InitInfo* info, D3D12_CPU_DESCRIPTOR_HANDLE cpu_desc_handle,
                                D3D12_GPU_DESCRIPTOR_HANDLE gpu_desc_handle)
{
    nb::object free_fn = ((Dx12InitInfo*)info->UserData)->srv_descriptor_free_fn;
    if (!free_fn.is_none())
        free_fn((uint64_t)cpu_desc_handle.ptr, (uint64_t)gpu_desc_handle.ptr);
}

ImGui_ImplDX12_InitInfo Dx12InitInfo::resolve()
{
    ImGui_ImplDX12_InitInfo resolved = info;
    resolved.UserData = this;
    if (!srv_descriptor_alloc_fn.is_none()) {
        resolved.SrvDescriptorAllocFn = srv_descriptor_alloc;
        resolved.SrvDescriptorFreeFn = srv_descriptor_free;
    }
    return resolved;
}

// Classes that take their fields as keyword arguments, as the ones that litgen binds
template <typename T> static void init_from_kwargs(nb::pointer_and_handle<T> self, nb::kwargs kwargs)
{
    new (self.p) T();
    nb::inst_mark_ready(self.h);
    for (auto [name, value] : kwargs)
        nb::setattr(self.h, name, value);
}

static bool init(const Dx12InitInfo& info)
{
    // Asserts as ImGui_ImplDX12_Init() would, before the InitInfo of a running backend is replaced
    IM_ASSERT(ImGui::GetIO().BackendRendererUserData == nullptr && "Already initialized a renderer backend!");
    ImGuiContext* context = ImGui::GetCurrentContext();
    // (An entry that is left could be of a destroyed context whose address is reused)
    running.erase(context);
    ImGui_ImplDX12_InitInfo resolved = running.emplace(context, info).first->second.resolve();
    try {
        if (ImGui_ImplDX12_Init(&resolved))
            return true;
    } catch (...) {
        running.erase(context);
        throw;
    }
    running.erase(context);
    return false;
}

static void shutdown()
{
    ImGui_ImplDX12_Shutdown();
    running.erase(ImGui::GetCurrentContext());
}

#define INIT_FIELD(name, field, ...)                                                                    \
    def_prop_rw(                                                                                        \
        name, [](Dx12InitInfo& self) { return self.info.field; },                                       \
        [](Dx12InitInfo& self, decltype(ImGui_ImplDX12_InitInfo::field) value) { self.info.field = value; }, \
        ##__VA_ARGS__)

#define DESCRIPTOR_FIELD(name, field, doc)                                                              \
    def_prop_rw(                                                                                        \
        name, [](Dx12InitInfo& self) { return (uint64_t)self.info.field.ptr; },                         \
        [](Dx12InitInfo& self, nb::handle value) { self.info.field.ptr = descriptor_handle(value); }, doc)

void py_init_module_imgui_impl_dx12(nb::module_& m)
{
    nb::class_<Dx12InitInfo>(m, "InitInfo", "Initialization data, for ImGui_ImplDX12_Init()")
        .def(nb::init<>())
        .def("__init__", init_from_kwargs<Dx12InitInfo>, "kwargs"_a, "Takes the fields as keyword arguments")
        .INIT_FIELD("device", Device)
        .INIT_FIELD("command_queue", CommandQueue, "Command queue used for queuing texture uploads.")
        .INIT_FIELD("num_frames_in_flight", NumFramesInFlight)
        .INIT_FIELD("rtv_format", RTVFormat, "RenderTarget format.")
        .INIT_FIELD("dsv_format", DSVFormat, "DepthStencilView format.")
        .INIT_FIELD("srv_descriptor_heap", SrvDescriptorHeap,
                    "Allocating SRV descriptors for textures is up to the application, so we provide callbacks.")
        .def_rw("srv_descriptor_alloc_fn", &Dx12InitInfo::srv_descriptor_alloc_fn,
                "Called without arguments for a descriptor of srv_descriptor_heap, which it returns as a\n"
                "(cpu_desc_handle, gpu_desc_handle) pair: ints (D3D12_*_DESCRIPTOR_HANDLE.ptr), or objects whose .ptr\n"
                "they are. Exceptions that it raises propagate to the backend function that Python called.")
        .def_rw("srv_descriptor_free_fn", &Dx12InitInfo::srv_descriptor_free_fn,
                "Called with the (cpu_desc_handle, gpu_desc_handle) of a descriptor that srv_descriptor_alloc_fn\n"
                "returned, as ints, once the backend no longer uses it.")
        .DESCRIPTOR_FIELD("legacy_single_srv_cpu_descriptor", LegacySingleSrvCpuDescriptor,
                          "To facilitate transition from single descriptor to allocator callback, you may use those.\n"
                          "(Without srv_descriptor_alloc_fn, the backend draws a single texture: the font atlas)")
        .DESCRIPTOR_FIELD("legacy_single_srv_gpu_descriptor", LegacySingleSrvGpuDescriptor, "");

    m.def("init", init, "info"_a);

    m.def("shutdown", shutdown);

    m.def("new_frame", ImGui_ImplDX12_NewFrame);

    m.def("render_draw_data", ImGui_ImplDX12_RenderDrawData, "draw_data"_a, "graphics_command_list"_a);

    m.def("create_device_objects", ImGui_ImplDX12_CreateDeviceObjects,
          "Use if you want to reset your rendering device without losing Dear ImGui state.");

    m.def("invalidate_device_objects", ImGui_ImplDX12_InvalidateDeviceObjects);

    m.def("update_texture", ImGui_ImplDX12_UpdateTexture, "tex"_a,
          "(Advanced) Use e.g. if you need to precisely control the timing of texture updates (e.g. for staged rendering), by setting ImDrawData::Textures = None to handle this manually.");
}
