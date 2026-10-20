cmake_minimum_required(VERSION 3.24...4.4)
project(imgui_bindings LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# The Dear ImGui release that the bindings in src/ were generated from. generate.py
# reads the tag from this line, so the two cannot drift apart: bump it, rerun
# generate.py, rebuild, and update its notices in THIRD_PARTY_LICENSES.txt.
set(IMGUI_GIT_TAG v1.93.0-docking)
# The same for ImPlot and ImGuizmo, as commits: their last releases predate their
# fixes for the ImDrawList changes of Dear ImGui 1.92.8
set(IMPLOT_GIT_TAG 851cdbdd60d1dfb64c23e987969694de0f547a6b)
set(IMGUIZMO_GIT_TAG 105e4fde45e4e74aae8973d98750820ef7aca7e2)

# The SDL3 release whose headers the backends are compiled against. SDL3 itself is
# not linked (see _imgui below), so the SDL3 loaded at runtime has to be this
# release or a newer one.
set(SDL3_VERSION 3.4.16)

# The Vulkan headers that the Vulkan backend is compiled against. Vulkan itself is
# not linked either: the backend looks its functions up at runtime (see below).
set(VULKAN_HEADERS_GIT_TAG vulkan-sdk-1.4.363.0)

include(FetchContent)
# The archives' files get the time of their extraction (DOWNLOAD_EXTRACT_TIMESTAMP
# FALSE), not their dates in the archive: those predate the objects of an earlier
# release in the kept build directory (see pyproject.toml), which would then pass
# for up to date after bumping a release below.
FetchContent_Declare(imgui
    URL https://github.com/ocornut/imgui/archive/refs/tags/${IMGUI_GIT_TAG}.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP FALSE)
# Only the headers of SDL3 are used: SOURCE_SUBDIR names a directory without a
# CMakeLists.txt, so SDL3 is downloaded but not built
FetchContent_Declare(sdl3
    URL https://github.com/libsdl-org/SDL/releases/download/release-${SDL3_VERSION}/SDL3-${SDL3_VERSION}.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP FALSE
    SOURCE_SUBDIR include)
FetchContent_Declare(implot
    URL https://github.com/epezent/implot/archive/${IMPLOT_GIT_TAG}.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP FALSE)
# ImGuizmo's own CMakeLists.txt builds all of its widgets: only ImGuizmo.cpp is compiled below
FetchContent_Declare(imguizmo
    URL https://github.com/CedricGuillemet/ImGuizmo/archive/${IMGUIZMO_GIT_TAG}.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP FALSE
    SOURCE_SUBDIR src)
# Only the headers, as for SDL3
FetchContent_Declare(vulkan_headers
    URL https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/${VULKAN_HEADERS_GIT_TAG}.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP FALSE
    SOURCE_SUBDIR include)
FetchContent_MakeAvailable(imgui sdl3 implot imguizmo vulkan_headers)

find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module Development.SABIModule)
find_package(nanobind CONFIG REQUIRED)

# Dear ImGui, its C++ string helpers, the backends, ImPlot and ImGuizmo, compiled
# with the regular optimization flags (nanobind_add_module optimizes for size)
add_library(imgui STATIC
    ${imgui_SOURCE_DIR}/imgui.cpp
    ${imgui_SOURCE_DIR}/imgui_demo.cpp
    ${imgui_SOURCE_DIR}/imgui_draw.cpp
    ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp
    ${imgui_SOURCE_DIR}/misc/cpp/imgui_stdlib.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_sdl3.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_sdlgpu3.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_opengl3.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_vulkan.cpp
    ${implot_SOURCE_DIR}/implot.cpp
    ${implot_SOURCE_DIR}/implot_items.cpp
    ${implot_SOURCE_DIR}/implot_demo.cpp
    ${imguizmo_SOURCE_DIR}/src/ImGuizmo.cpp)
target_include_directories(imgui PUBLIC
    ${imgui_SOURCE_DIR}
    ${imgui_SOURCE_DIR}/backends
    ${imgui_SOURCE_DIR}/misc/cpp
    ${implot_SOURCE_DIR}
    ${imguizmo_SOURCE_DIR}/src)
target_compile_definitions(imgui PUBLIC IMGUI_USER_CONFIG="imgui_config.h")
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    # IM_ASSERT throws (see imgui_config.h), which the few destructors that assert
    # cannot pass on: an assertion that fails in one of them terminates instead
    target_compile_options(imgui PUBLIC -Wno-exceptions)
endif()
target_include_directories(imgui PUBLIC src/bindings ${sdl3_SOURCE_DIR}/include)
set_target_properties(imgui PROPERTIES POSITION_INDEPENDENT_CODE ON)
# OpenGL is not linked either: imgui_impl_opengl3_loader.h opens the system's
# OpenGL library at runtime (dlopen, or LoadLibrary on Windows) and looks up its
# functions there, in the GL context that is current
target_link_libraries(imgui PUBLIC ${CMAKE_DL_LIBS})
# Nor is Vulkan: without prototypes, the Vulkan backend calls the functions that
# ImGui_ImplVulkan_LoadFunctions() looks up, which the bindings do through the
# vkGetInstanceProcAddr of the Vulkan library that SDL3 loaded. Defined for the
# bindings as well, which have to see the same declarations.
target_include_directories(imgui PUBLIC ${vulkan_headers_SOURCE_DIR}/include)
target_compile_definitions(imgui PUBLIC IMGUI_IMPL_VULKAN_NO_PROTOTYPES)

# STABLE_ABI: the module uses the stable ABI only, so that one wheel (abi3, see
# pyproject.toml) serves every Python release from 3.12 on
nanobind_add_module(_imgui STABLE_ABI
    src/bindings/module.cpp
    src/bindings/imgui_bindings.cpp
    src/bindings/imgui_impl_sdl3_bindings.cpp
    src/bindings/imgui_impl_sdlgpu3_bindings.cpp
    src/bindings/imgui_impl_opengl3_bindings.cpp
    src/bindings/imgui_impl_vulkan_bindings.cpp
    src/bindings/implot_bindings.cpp
    src/bindings/imguizmo_bindings.cpp)
target_link_libraries(_imgui PRIVATE imgui)

# All of imgui_bindings.cpp's bindings sit in one function of some 6700 lines, which
# MSVC's optimizer takes superlinearly long to compile: around nine minutes, against
# the twelve seconds of the five other translation units together. Lowering the
# inlining level (/Ob1) does not help, so that one file is compiled unoptimized,
# which brings it down to some twenty seconds. The cost is a little overhead in the
# lambdas that forward to Dear ImGui, whose own code is optimized as usual in the
# imgui library above. /Od comes last and thus wins over the /O2 /Ob2 /Os of the
# release flags, which warns (D9025) but is exactly the intent.
if(MSVC)
    set_source_files_properties(src/bindings/imgui_bindings.cpp
        PROPERTIES COMPILE_OPTIONS "/Od")
endif()

# SDL3 is not linked: the module leaves its SDL functions unresolved, and they are
# looked up when it is loaded, in the SDL3 that pysdl3 loaded before (see
# src/imgui/__init__.py). The backends thus share pysdl3's SDL3, whose windows and
# events they are handed. Linux allows unresolved symbols in shared libraries as
# is, macOS has to be told; a Windows DLL cannot have any, so it links SDL3.dll,
# through the import library of the SDL3 release above. At runtime, Windows
# resolves SDL3.dll to the one pysdl3 has already loaded. The wheel must not bundle
# an SDL3.dll of its own (see the cibuildwheel options in pyproject.toml).
if(APPLE)
    target_link_options(_imgui PRIVATE -Wl,-undefined,dynamic_lookup)
elseif(WIN32)
    FetchContent_Declare(sdl3_devel
        URL https://github.com/libsdl-org/SDL/releases/download/release-${SDL3_VERSION}/SDL3-devel-${SDL3_VERSION}-VC.zip
        DOWNLOAD_EXTRACT_TIMESTAMP FALSE)
    FetchContent_MakeAvailable(sdl3_devel)
    find_package(SDL3 CONFIG REQUIRED COMPONENTS SDL3-shared PATHS ${sdl3_devel_SOURCE_DIR} NO_DEFAULT_PATH)
    target_link_libraries(_imgui PRIVATE SDL3::SDL3-shared)
endif()

# The Direct3D 12 backend, on Windows. It loads d3d12.dll itself, and links DXGI and
# the shader compiler (d3dcompiler_47.dll), which are part of Windows.
if(WIN32)
    target_sources(imgui PRIVATE ${imgui_SOURCE_DIR}/backends/imgui_impl_dx12.cpp)
    target_link_libraries(imgui PUBLIC dxgi d3dcompiler)
    target_sources(_imgui PRIVATE src/bindings/imgui_impl_dx12_bindings.cpp)
endif()

# The Metal backends, on macOS: Objective-C++, with ARC as Dear ImGui's examples
# build them. Both define Objective-C classes of the same names, which would clash
# when linked together (and with any other class of those names in the process,
# as the Objective-C runtime is global): each gets its own names.
if(APPLE)
    enable_language(OBJCXX)
    set(IMGUI_METAL_RENAMES
        MetalBuffer MetalTexture MetalContext FramebufferDescriptor ImGuiViewportDataMetal shaderCode)
    foreach(backend Metal Metal4)
        string(TOLOWER ${backend} name)
        set(renames)
        foreach(symbol ${IMGUI_METAL_RENAMES})
            list(APPEND renames "${symbol}=ImGui_Impl${backend}_${symbol}")
        endforeach()
        set_source_files_properties(${imgui_SOURCE_DIR}/backends/imgui_impl_${name}.mm PROPERTIES
            COMPILE_DEFINITIONS "${renames}"
            COMPILE_OPTIONS "-fobjc-arc;-fobjc-weak")
        target_sources(imgui PRIVATE ${imgui_SOURCE_DIR}/backends/imgui_impl_${name}.mm)
    endforeach()
    target_link_libraries(imgui PUBLIC "-framework Metal" "-framework QuartzCore" "-framework Cocoa")
    target_sources(_imgui PRIVATE src/bindings/imgui_impl_metal_bindings.mm)
    set_source_files_properties(src/bindings/imgui_impl_metal_bindings.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc")
endif()

install(TARGETS _imgui LIBRARY DESTINATION imgui)
