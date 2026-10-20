#include <nanobind/nanobind.h>

namespace nb = nanobind;

void py_init_module_imgui(nb::module_& m);
void py_init_module_imgui_impl_sdl3(nb::module_& m);
void py_init_module_imgui_impl_sdlgpu3(nb::module_& m);
void py_init_module_imgui_impl_opengl3(nb::module_& m);
void py_init_module_imgui_impl_vulkan(nb::module_& m);
#ifdef _WIN32
void py_init_module_imgui_impl_dx12(nb::module_& m);
#endif
#ifdef __APPLE__
void py_init_module_imgui_impl_metal(nb::module_& m);
void py_init_module_imgui_impl_metal4(nb::module_& m);
#endif
void py_init_module_implot(nb::module_& m);
void py_init_module_imguizmo(nb::module_& m);

// Everything lives in one extension: Dear ImGui keeps its state in globals, which
// the backends, ImPlot and ImGuizmo have to share with the imgui functions called
// from Python.
NB_MODULE(_imgui, m)
{
    py_init_module_imgui(m);

    nb::module_ impl_sdl3 = m.def_submodule("impl_sdl3", "Dear ImGui platform backend for SDL3 (imgui_impl_sdl3.h)");
    py_init_module_imgui_impl_sdl3(impl_sdl3);

    nb::module_ impl_sdlgpu3 = m.def_submodule("impl_sdlgpu3", "Dear ImGui renderer backend for SDL_GPU (imgui_impl_sdlgpu3.h)");
    py_init_module_imgui_impl_sdlgpu3(impl_sdlgpu3);

    nb::module_ impl_opengl3 = m.def_submodule("impl_opengl3", "Dear ImGui renderer backend for OpenGL 3 (imgui_impl_opengl3.h)");
    py_init_module_imgui_impl_opengl3(impl_opengl3);

    nb::module_ impl_vulkan = m.def_submodule("impl_vulkan", "Dear ImGui renderer backend for Vulkan (imgui_impl_vulkan.h)");
    py_init_module_imgui_impl_vulkan(impl_vulkan);

#ifdef _WIN32
    nb::module_ impl_dx12 = m.def_submodule("impl_dx12", "Dear ImGui renderer backend for Direct3D 12 (imgui_impl_dx12.h), Windows only");
    py_init_module_imgui_impl_dx12(impl_dx12);
#endif

#ifdef __APPLE__
    nb::module_ impl_metal = m.def_submodule("impl_metal", "Dear ImGui renderer backend for Metal (imgui_impl_metal.h), macOS only");
    py_init_module_imgui_impl_metal(impl_metal);

    nb::module_ impl_metal4 = m.def_submodule("impl_metal4", "Dear ImGui renderer backend for Metal 4 (imgui_impl_metal4.h), macOS only");
    py_init_module_imgui_impl_metal4(impl_metal4);
#endif

    nb::module_ implot = m.def_submodule("implot", "ImPlot, plots for Dear ImGui (implot.h)");
    py_init_module_implot(implot);

    nb::module_ imguizmo = m.def_submodule("imguizmo", "ImGuizmo, 3D gizmos for Dear ImGui (ImGuizmo.h)");
    py_init_module_imguizmo(imguizmo);
}
