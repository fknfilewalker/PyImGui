// Bindings for backends/imgui_impl_vulkan.h, written by hand: its structs hold Vulkan
// structs, arrays and C callbacks, which litgen cannot bind as Python objects.
//
// The extension does not link Vulkan: the backend is compiled with
// IMGUI_IMPL_VULKAN_NO_PROTOTYPES (see CMakeLists.txt), and init() loads its functions
// through the vkGetInstanceProcAddr of the Vulkan library that SDL3 loaded, unless
// load_functions() was given another one.

#include <algorithm>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include <nanobind/nanobind.h>
#include <nanobind/stl/vector.h>

#include "imgui.h"
#include "imgui_impl_vulkan.h"
#include "native_casters.h"

#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>

namespace nb = nanobind;
using namespace nb::literals;

// Vulkan's handles are pointers to opaque structs on 64-bit platforms (on 32-bit ones,
// its non-dispatchable handles are 64-bit integers instead): Python passes them as
// ints, ctypes or cffi objects
#if !VK_USE_64_BIT_PTR_DEFINES
#error "The Vulkan bindings expect a 64-bit platform"
#endif

namespace nanobind::detail {
template <> struct type_caster<VkInstance_T> : native_pointer_caster<VkInstance_T> {};
template <> struct type_caster<VkPhysicalDevice_T> : native_pointer_caster<VkPhysicalDevice_T> {};
template <> struct type_caster<VkDevice_T> : native_pointer_caster<VkDevice_T> {};
template <> struct type_caster<VkQueue_T> : native_pointer_caster<VkQueue_T> {};
template <> struct type_caster<VkCommandBuffer_T> : native_pointer_caster<VkCommandBuffer_T> {};
template <> struct type_caster<VkDescriptorPool_T> : native_pointer_caster<VkDescriptorPool_T> {};
template <> struct type_caster<VkDescriptorSet_T> : native_pointer_caster<VkDescriptorSet_T> {};
template <> struct type_caster<VkPipelineCache_T> : native_pointer_caster<VkPipelineCache_T> {};
template <> struct type_caster<VkPipeline_T> : native_pointer_caster<VkPipeline_T> {};
template <> struct type_caster<VkRenderPass_T> : native_pointer_caster<VkRenderPass_T> {};
template <> struct type_caster<VkImageView_T> : native_pointer_caster<VkImageView_T> {};
template <> struct type_caster<VkFormat> : native_enum_caster<VkFormat> {};
template <> struct type_caster<VkSampleCountFlagBits> : native_enum_caster<VkSampleCountFlagBits> {};
template <> struct type_caster<VkImageLayout> : native_enum_caster<VkImageLayout> {};
template <> struct type_caster<VkDynamicState> : native_enum_caster<VkDynamicState> {};
} // namespace nanobind::detail

// ImGui_ImplVulkan_PipelineInfo, whose PipelineRenderingCreateInfo points to an
// array of formats, which this keeps
struct VulkanPipelineInfo {
    ImGui_ImplVulkan_PipelineInfo info = {};
    std::vector<VkFormat> color_attachment_formats;

    // The structure for the backend, pointing into this one
    ImGui_ImplVulkan_PipelineInfo resolve() const
    {
        ImGui_ImplVulkan_PipelineInfo resolved = info;
        resolved.PipelineRenderingCreateInfo.colorAttachmentCount = (uint32_t)color_attachment_formats.size();
        resolved.PipelineRenderingCreateInfo.pColorAttachmentFormats =
            color_attachment_formats.empty() ? nullptr : color_attachment_formats.data();
        return resolved;
    }

    // Filling in PipelineRenderingCreateInfo makes it valid, which its sType tells
    VkPipelineRenderingCreateInfoKHR& rendering()
    {
        info.PipelineRenderingCreateInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR;
        return info.PipelineRenderingCreateInfo;
    }
};

// ImGui_ImplVulkan_InitInfo, with the pipeline infos above, the Python function for
// CheckVkResultFn, and the SPIR-V code of the custom shaders
struct VulkanInitInfo {
    ImGui_ImplVulkan_InitInfo info = {};
    VulkanPipelineInfo pipeline_info_main, pipeline_info_for_viewports;
    nb::object check_vk_result_fn = nb::none();
    std::vector<uint32_t> custom_shader_vert_code, custom_shader_frag_code;

    ImGui_ImplVulkan_InitInfo resolve() const;
};

// The InitInfo of each Dear ImGui context whose backend runs, which the backend
// points into until it shuts down. Never destroyed, as it holds Python objects,
// which cannot be released once the interpreter has finalized.
static std::map<ImGuiContext*, VulkanInitInfo>& running = *new std::map<ImGuiContext*, VulkanInitInfo>();

// CheckVkResultFn passes no user data: the function is the current context's
static void check_vk_result(VkResult err)
{
    auto it = running.find(ImGui::GetCurrentContext());
    if (it != running.end() && !it->second.check_vk_result_fn.is_none())
        it->second.check_vk_result_fn((int)err);
}

static void shader_module_create_info(VkShaderModuleCreateInfo& info, const std::vector<uint32_t>& code)
{
    if (code.empty())
        return;
    info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
    info.codeSize = code.size() * sizeof(uint32_t);
    info.pCode = code.data();
}

ImGui_ImplVulkan_InitInfo VulkanInitInfo::resolve() const
{
    ImGui_ImplVulkan_InitInfo resolved = info;
    resolved.PipelineInfoMain = pipeline_info_main.resolve();
    resolved.PipelineInfoForViewports = pipeline_info_for_viewports.resolve();
    resolved.CheckVkResultFn = check_vk_result_fn.is_none() ? nullptr : check_vk_result;
    shader_module_create_info(resolved.CustomShaderVertCreateInfo, custom_shader_vert_code);
    shader_module_create_info(resolved.CustomShaderFragCreateInfo, custom_shader_frag_code);
    return resolved;
}

// SPIR-V code, from bytes (or anything with the buffer protocol) to 32-bit words
static std::vector<uint32_t> spirv_code(nb::handle code)
{
    if (code.is_none())
        return {};
    Py_buffer view;
    if (PyObject_GetBuffer(code.ptr(), &view, PyBUF_SIMPLE) != 0)
        throw nb::python_error();
    std::vector<uint32_t> words((size_t)view.len / sizeof(uint32_t));
    bool aligned = view.len % sizeof(uint32_t) == 0;
    if (aligned)
        memcpy(words.data(), view.buf, (size_t)view.len);
    PyBuffer_Release(&view);
    if (!aligned)
        throw nb::value_error("SPIR-V code must be a whole number of 32-bit words");
    return words;
}

static nb::object spirv_bytes(const std::vector<uint32_t>& code)
{
    if (code.empty())
        return nb::none();
    return nb::bytes(code.data(), code.size() * sizeof(uint32_t));
}

// Classes that take their fields as keyword arguments, as the ones that litgen binds
template <typename T> static void init_from_kwargs(nb::pointer_and_handle<T> self, nb::kwargs kwargs)
{
    new (self.p) T();
    nb::inst_mark_ready(self.h);
    for (auto [name, value] : kwargs)
        nb::setattr(self.h, name, value);
}

// ImGui_ImplVulkan_LoadFunctions(), whose loader looks the functions up with
// vkGetInstanceProcAddr(instance, name)
struct VulkanLoader {
    PFN_vkGetInstanceProcAddr get_instance_proc_addr;
    VkInstance instance;
};

static bool functions_loaded = false;

static bool load_functions(uint32_t api_version, VkInstance instance, nb::handle get_instance_proc_addr)
{
    void* address = nullptr;
    if (!get_instance_proc_addr.is_none()) {
        if (!nb::detail::native_address(get_instance_proc_addr, &address) || !address)
            throw nb::type_error("get_instance_proc_addr must be the address of vkGetInstanceProcAddr, or a ctypes function pointer to it");
    } else if (!(address = (void*)SDL_Vulkan_GetVkGetInstanceProcAddr())) {
        throw std::runtime_error(std::string("SDL has not loaded Vulkan: create a window with SDL_WINDOW_VULKAN, "
                                             "call SDL_Vulkan_LoadLibrary(), or pass get_instance_proc_addr (")
                                 + SDL_GetError() + ")");
    }
    VulkanLoader loader = {(PFN_vkGetInstanceProcAddr)address, instance};
    functions_loaded = ImGui_ImplVulkan_LoadFunctions(
        api_version,
        [](const char* name, void* user_data)
        {
            VulkanLoader* loader = (VulkanLoader*)user_data;
            return loader->get_instance_proc_addr(loader->instance, name);
        },
        &loader);
    return functions_loaded;
}

static bool init(const VulkanInitInfo& info)
{
    // Asserts as ImGui_ImplVulkan_Init() would, before the InitInfo of a running backend is replaced
    IM_ASSERT(ImGui::GetIO().BackendRendererUserData == nullptr && "Already initialized a renderer backend!");
    ImGuiContext* context = ImGui::GetCurrentContext();
    if (!functions_loaded && !load_functions(info.info.ApiVersion, info.info.Instance, nb::none()))
        throw std::runtime_error("Could not load the Vulkan functions of imgui_impl_vulkan through SDL3's vkGetInstanceProcAddr");
    // (An entry that is left could be of a destroyed context whose address is reused)
    running.erase(context);
    VulkanInitInfo& stored = running.emplace(context, info).first->second;
    ImGui_ImplVulkan_InitInfo resolved = stored.resolve();
    try {
        if (ImGui_ImplVulkan_Init(&resolved))
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
    ImGui_ImplVulkan_Shutdown();
    running.erase(ImGui::GetCurrentContext());
    // The next init() loads the functions for its own instance, unless load_functions() precedes it
    functions_loaded = false;
}

// Exposes the fields of a VkPipelineRenderingCreateInfoKHR
#define RENDERING_FIELD(name, field)                                                                    \
    def_prop_rw(                                                                                        \
        name, [](VulkanPipelineInfo& self) { return self.info.PipelineRenderingCreateInfo.field; },     \
        [](VulkanPipelineInfo& self, decltype(VkPipelineRenderingCreateInfoKHR::field) value) { self.rendering().field = value; })

void py_init_module_imgui_impl_vulkan(nb::module_& m)
{
    nb::class_<VulkanPipelineInfo>(m, "PipelineInfo", "Specify settings to create pipeline and swapchain")
        .def(nb::init<>())
        .def("__init__", init_from_kwargs<VulkanPipelineInfo>, "kwargs"_a,
             "Takes the fields as keyword arguments")
        .def_prop_rw(
            "render_pass", [](VulkanPipelineInfo& self) { return self.info.RenderPass; },
            [](VulkanPipelineInfo& self, VkRenderPass value) { self.info.RenderPass = value; },
            "For Main viewport only. Ignored if using dynamic rendering")
        .def_prop_rw(
            "subpass", [](VulkanPipelineInfo& self) { return self.info.Subpass; },
            [](VulkanPipelineInfo& self, uint32_t value) { self.info.Subpass = value; })
        .def_prop_rw(
            "msaa_samples", [](VulkanPipelineInfo& self) { return self.info.MSAASamples; },
            [](VulkanPipelineInfo& self, VkSampleCountFlagBits value) { self.info.MSAASamples = value; },
            "0 defaults to VK_SAMPLE_COUNT_1_BIT")
        .def_prop_rw(
            "extra_dynamic_states",
            [](VulkanPipelineInfo& self)
            {
                const ImVector<VkDynamicState>& states = self.info.ExtraDynamicStates;
                return std::vector<VkDynamicState>(states.begin(), states.end());
            },
            [](VulkanPipelineInfo& self, const std::vector<VkDynamicState>& states)
            {
                self.info.ExtraDynamicStates.resize((int)states.size());
                std::copy(states.begin(), states.end(), self.info.ExtraDynamicStates.begin());
            },
            "Optional, allows to insert more dynamic states into our VkPipeline")
        .def_prop_rw(
            "color_attachment_formats", [](VulkanPipelineInfo& self) { return self.color_attachment_formats; },
            [](VulkanPipelineInfo& self, const std::vector<VkFormat>& formats)
            {
                self.rendering();
                self.color_attachment_formats = formats;
            },
            "PipelineRenderingCreateInfo.pColorAttachmentFormats, for dynamic rendering. Setting any of the\n"
            "PipelineRenderingCreateInfo fields makes the structure valid (sets its sType).")
        .RENDERING_FIELD("view_mask", viewMask)
        .RENDERING_FIELD("depth_attachment_format", depthAttachmentFormat)
        .RENDERING_FIELD("stencil_attachment_format", stencilAttachmentFormat)
        .def_prop_ro(
            "has_pipeline_rendering_create_info",
            [](VulkanPipelineInfo& self)
            { return self.info.PipelineRenderingCreateInfo.sType == VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR; },
            "Whether PipelineRenderingCreateInfo is filled in (valid if .sType == VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR)")
        .def_prop_rw(
            "swap_chain_image_usage", [](VulkanPipelineInfo& self) { return self.info.SwapChainImageUsage; },
            [](VulkanPipelineInfo& self, VkImageUsageFlags value) { self.info.SwapChainImageUsage = value; },
            "For Secondary viewports only. Extra flags for vkCreateSwapchainKHR() calls for secondary viewports. We\n"
            "automatically add VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT.");

#define INIT_FIELD(name, field, ...)                                                                    \
    def_prop_rw(                                                                                        \
        name, [](VulkanInitInfo& self) { return self.info.field; },                                     \
        [](VulkanInitInfo& self, decltype(ImGui_ImplVulkan_InitInfo::field) value) { self.info.field = value; }, \
        ##__VA_ARGS__)

    nb::class_<VulkanInitInfo>(m, "InitInfo", "Initialization data, for ImGui_ImplVulkan_Init()")
        .def(nb::init<>())
        .def("__init__", init_from_kwargs<VulkanInitInfo>, "kwargs"_a, "Takes the fields as keyword arguments")
        .INIT_FIELD("api_version", ApiVersion,
                    "Fill with API version of Instance, e.g. VK_API_VERSION_1_3 or your value of\n"
                    "VkApplicationInfo::apiVersion. May be lower than header version (VK_HEADER_VERSION_COMPLETE)")
        .INIT_FIELD("instance", Instance)
        .INIT_FIELD("physical_device", PhysicalDevice)
        .INIT_FIELD("device", Device)
        .INIT_FIELD("queue_family", QueueFamily)
        .INIT_FIELD("queue", Queue)
        .INIT_FIELD("descriptor_pool", DescriptorPool,
                    "Created with VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT, holding enough\n"
                    "VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER descriptors; ignored if using descriptor_pool_size > 0")
        .INIT_FIELD("descriptor_pool_size", DescriptorPoolSize,
                    "Optional: set to create internal ImageView descriptor pool automatically instead of using DescriptorPool.")
        .INIT_FIELD("min_image_count", MinImageCount, ">= 2")
        .INIT_FIELD("image_count", ImageCount, ">= MinImageCount")
        .INIT_FIELD("pipeline_cache", PipelineCache, "Optional")
        .def_rw("pipeline_info_main", &VulkanInitInfo::pipeline_info_main,
                "Infos for Main Viewport (created by app/user)")
        .def_rw("pipeline_info_for_viewports", &VulkanInitInfo::pipeline_info_for_viewports,
                "Infos for Secondary Viewports (created by backend)")
        .INIT_FIELD("use_dynamic_rendering", UseDynamicRendering,
                    "Need to explicitly enable VK_KHR_dynamic_rendering extension to use this, even for Vulkan 1.3 + setup\n"
                    "pipeline_info_main's PipelineRenderingCreateInfo fields.")
        .def_rw("check_vk_result_fn", &VulkanInitInfo::check_vk_result_fn,
                "Called with the VkResult of the backend's Vulkan calls. Exceptions that it raises propagate to the\n"
                "backend function that Python called.")
        .INIT_FIELD("min_allocation_size", MinAllocationSize,
                    "Minimum allocation size. Set to 1024*1024 to satisfy zealous best practices validation layer and\n"
                    "waste a little memory.")
        .def_prop_rw(
            "custom_shader_vert_code", [](VulkanInitInfo& self) { return spirv_bytes(self.custom_shader_vert_code); },
            [](VulkanInitInfo& self, nb::handle code) { self.custom_shader_vert_code = spirv_code(code); },
            "SPIR-V code of CustomShaderVertCreateInfo, to customize the default vertex shader (whose inputs and\n"
            "outputs it has to match); None for the default")
        .def_prop_rw(
            "custom_shader_frag_code", [](VulkanInitInfo& self) { return spirv_bytes(self.custom_shader_frag_code); },
            [](VulkanInitInfo& self, nb::handle code) { self.custom_shader_frag_code = spirv_code(code); },
            "SPIR-V code of CustomShaderFragCreateInfo, to customize the default fragment shader; None for the default");
#undef INIT_FIELD

    m.def("init", init, "info"_a,
          "Loads the Vulkan functions through SDL3's vkGetInstanceProcAddr first, unless load_functions() did.");

    m.def("shutdown", shutdown);

    m.def("new_frame", ImGui_ImplVulkan_NewFrame);

    m.def("render_draw_data", ImGui_ImplVulkan_RenderDrawData, "draw_data"_a, "command_buffer"_a,
          "pipeline"_a = nb::none());

    m.def("set_min_image_count", ImGui_ImplVulkan_SetMinImageCount, "min_image_count"_a,
          "To override MinImageCount after initialization (e.g. if swap chain is recreated)");

    m.def(
        "create_main_pipeline", [](const VulkanPipelineInfo& info)
        {
            ImGui_ImplVulkan_PipelineInfo resolved = info.resolve();
            ImGui_ImplVulkan_CreateMainPipeline(&resolved);
        },
        "info"_a,
        "(Advanced) Use e.g. if you need to recreate pipeline without reinitializing the backend (see #8110, #8111)\n"
        "The main window pipeline will be created by ImGui_ImplVulkan_Init() if possible (== RenderPass xor\n"
        "(UseDynamicRendering && PipelineRenderingCreateInfo->sType == VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR))\n"
        "Else, the pipeline can be created, or re-created, using ImGui_ImplVulkan_CreateMainPipeline() before rendering.");

    m.def("update_texture", ImGui_ImplVulkan_UpdateTexture, "tex"_a,
          "(Advanced) Use e.g. if you need to precisely control the timing of texture updates (e.g. for staged rendering), by setting ImDrawData::Textures = None to handle this manually.");

    m.def(
        "add_texture", [](VkImageView image_view, VkImageLayout image_layout)
        { return ImGui_ImplVulkan_AddTexture(image_view, image_layout); },
        "image_view"_a, "image_layout"_a,
        "Register a texture (VkDescriptorSet for a VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE == ImTextureID), for\n"
        "imgui.image(imgui.ImTextureRef(descriptor_set)).");

    m.def("remove_texture", ImGui_ImplVulkan_RemoveTexture, "descriptor_set"_a);

    m.def("load_functions", load_functions, "api_version"_a, "instance"_a, "get_instance_proc_addr"_a = nb::none(),
          "Loads the Vulkan functions of the backend with vkGetInstanceProcAddr(instance, name): the one at the\n"
          "address get_instance_proc_addr, or by default SDL3's (SDL_Vulkan_GetVkGetInstanceProcAddr()).\n"
          "init() calls it with SDL3's if it has not been called since the last shutdown().");
}
