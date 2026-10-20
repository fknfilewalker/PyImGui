// nanobind casters for the SDL types in the signatures of the SDL3 and SDL_GPU
// backends. Every file that binds those signatures includes this, so that each
// of them sees the same specializations.

#pragma once

#include <nanobind/nanobind.h>

#include <SDL3/SDL.h>

namespace nanobind::detail {

// SDL objects come from Python as ctypes objects (pysdl3 is built on ctypes) or
// as plain addresses. Returns false for anything else.
inline bool sdl_address(handle src, void** out) noexcept
{
    if (src.is_none()) {
        *out = nullptr;
        return true;
    }
    if (PyLong_Check(src.ptr())) {
        *out = PyLong_AsVoidPtr(src.ptr());
        if (PyErr_Occurred()) {
            PyErr_Clear();
            return false;
        }
        return true;
    }
    try {
        module_ ctypes = module_::import_("ctypes");
        auto is_a = [&](handle obj, const char* type) { return PyObject_IsInstance(obj.ptr(), ctypes.attr(type).ptr()) == 1; };
        auto address = [&](handle obj) { return PyLong_AsVoidPtr(ctypes.attr("addressof")(obj).ptr()); };
        // A pointer, e.g. what pysdl3's SDL_CreateWindow returns: the address it holds
        if (is_a(src, "_Pointer") || is_a(src, "c_void_p")) {
            *out = *static_cast<void**>(address(src));
            return true;
        }
        // A structure or union, e.g. pysdl3's SDL_Event, or ctypes.byref() of one
        // (whose type ctypes does not expose): the address of the structure
        object obj = hasattr(src, "_obj") ? src.attr("_obj") : borrow(src);
        if (is_a(obj, "Structure") || is_a(obj, "Union")) {
            *out = address(obj);
            return true;
        }
    } catch (...) {
        PyErr_Clear();
    }
    return false;
}

// Casts a Python object into a pointer to one of SDL's types, following the
// pattern of nanobind's own caster for void*
template <typename T> struct sdl_pointer_caster {
    template <typename T_> using Cast = T*;
    template <typename T_> static constexpr bool can_cast() { return true; }
    using Value = T*;
    static constexpr auto Name = const_name("int | ctypes._CData");
    explicit operator T*() { return value; }
    Value value = nullptr;

    bool from_python(handle src, uint32_t, cleanup_list*) noexcept
    {
        void* address;
        if (!sdl_address(src, &address))
            return false;
        value = static_cast<T*>(address);
        return true;
    }

    static handle from_cpp(T* ptr, rv_policy, cleanup_list*) noexcept
    {
        return ptr ? PyLong_FromVoidPtr(ptr) : none().release();
    }
};

template <> struct type_caster<SDL_Window> : sdl_pointer_caster<SDL_Window> {};
template <> struct type_caster<SDL_Renderer> : sdl_pointer_caster<SDL_Renderer> {};
template <> struct type_caster<SDL_GLContextState> : sdl_pointer_caster<SDL_GLContextState> {};
template <> struct type_caster<SDL_Event> : sdl_pointer_caster<SDL_Event> {};
template <> struct type_caster<SDL_GPUDevice> : sdl_pointer_caster<SDL_GPUDevice> {};
template <> struct type_caster<SDL_GPUCommandBuffer> : sdl_pointer_caster<SDL_GPUCommandBuffer> {};
template <> struct type_caster<SDL_GPURenderPass> : sdl_pointer_caster<SDL_GPURenderPass> {};
template <> struct type_caster<SDL_GPUGraphicsPipeline> : sdl_pointer_caster<SDL_GPUGraphicsPipeline> {};

// SDL's enums, which pysdl3 represents by plain ints
template <typename E> struct sdl_enum_caster {
    NB_TYPE_CASTER(E, const_name("int"))

    bool from_python(handle src, uint32_t flags, cleanup_list* cleanup) noexcept
    {
        make_caster<int> caster;
        if (!caster.from_python(src, flags, cleanup))
            return false;
        value = static_cast<E>(caster.value);
        return true;
    }

    static handle from_cpp(E value, rv_policy, cleanup_list*) noexcept
    {
        return PyLong_FromLong(static_cast<long>(value));
    }
};

template <> struct type_caster<SDL_GPUTextureFormat> : sdl_enum_caster<SDL_GPUTextureFormat> {};
template <> struct type_caster<SDL_GPUSampleCount> : sdl_enum_caster<SDL_GPUSampleCount> {};
template <> struct type_caster<SDL_GPUSwapchainComposition> : sdl_enum_caster<SDL_GPUSwapchainComposition> {};
template <> struct type_caster<SDL_GPUPresentMode> : sdl_enum_caster<SDL_GPUPresentMode> {};

} // namespace nanobind::detail
