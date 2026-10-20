// Opaque handles, for the types that the headers only declare: nanobind cannot bind
// those as classes, so Python gets a handle to them instead.

#pragma once

#include <functional>

#include <nanobind/nanobind.h>

#include "imgui.h"

template <typename T> struct OpaqueHandle {
    T* ptr;
};

namespace nanobind::detail {

template <typename T> struct opaque_handle_caster {
    template <typename T_> using Cast = T*;
    template <typename T_> static constexpr bool can_cast() { return true; }
    using Value = T*;
    static constexpr auto Name = make_caster<OpaqueHandle<T>>::Name;
    explicit operator T*() { return value; }
    Value value = nullptr;

    bool from_python(handle src, uint32_t, cleanup_list*) noexcept
    {
        if (src.is_none()) {
            value = nullptr;
            return true;
        }
        OpaqueHandle<T>* opaque;
        if (!try_cast(src, opaque))
            return false;
        value = opaque->ptr;
        return true;
    }

    static handle from_cpp(T* ptr, rv_policy, cleanup_list*) noexcept
    {
        if (!ptr)
            return none().release();
        try {
            return cast(OpaqueHandle<T>{ptr}).release();
        } catch (...) {
            return handle();
        }
    }
};

// imgui.h only declares these. (Their definitions in imgui_internal.h would not do:
// the context holds vectors of types that only imgui.cpp defines.)
template <> struct type_caster<ImGuiContext> : opaque_handle_caster<ImGuiContext> {};
template <> struct type_caster<ImDrawListSharedData> : opaque_handle_caster<ImDrawListSharedData> {};
template <> struct type_caster<ImFontLoader> : opaque_handle_caster<ImFontLoader> {};

} // namespace nanobind::detail

template <typename T> void bind_opaque_handle(nanobind::module_& m, const char* name, const char* doc)
{
    namespace nb = nanobind;
    nb::class_<OpaqueHandle<T>>(m, name, doc)
        .def("__eq__", [](const OpaqueHandle<T>& a, const OpaqueHandle<T>& b) { return a.ptr == b.ptr; }, nb::is_operator())
        .def("__hash__", [](const OpaqueHandle<T>& a) { return std::hash<T*>()(a.ptr); });
}
