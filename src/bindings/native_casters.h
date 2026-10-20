// nanobind casters for the native GPU objects in the signatures of the Vulkan and
// Direct3D 12 backends, which Python has no common representation of: each binding
// library (ctypes, cffi, comtypes, vulkan, ...) wraps them its own way.

#pragma once

#include <nanobind/nanobind.h>

namespace nanobind::detail {

// A native handle, e.g. a VkDevice or an ID3D12Device*, as a plain address; as a
// ctypes pointer, c_void_p, function pointer or pointer-sized integer (including
// comtypes' interface pointers, which are ctypes pointers); or as any object that
// int() converts into the address, such as cffi's pointers. None is the null handle.
// Returns false for anything else.
inline bool native_address(handle src, void** out) noexcept
{
    if (src.is_none()) {
        *out = nullptr;
        return true;
    }
    if (PyFloat_Check(src.ptr()) || PyBool_Check(src.ptr()))
        return false;
    try {
        if (!PyLong_Check(src.ptr())) {
            module_ ctypes = module_::import_("ctypes");
            if (PyObject_IsInstance(src.ptr(), ctypes.attr("_SimpleCData").ptr()) == 1
                || PyObject_IsInstance(src.ptr(), ctypes.attr("_Pointer").ptr()) == 1
                || PyObject_IsInstance(src.ptr(), ctypes.attr("_CFuncPtr").ptr()) == 1) {
                // The ctypes object holds the handle: what is stored at its address
                if (cast<size_t>(ctypes.attr("sizeof")(src)) != sizeof(void*))
                    return false;
                *out = *static_cast<void**>(PyLong_AsVoidPtr(ctypes.attr("addressof")(src).ptr()));
                return !PyErr_Occurred();
            }
            if (!hasattr(src, "__int__") && !hasattr(src, "__index__"))
                return false;
        }
        object address = steal(PyNumber_Long(src.ptr()));
        if (!address.is_valid())
            throw python_error();
        // Handles are addresses, or 64-bit integers that nonetheless fit a pointer
        *out = reinterpret_cast<void*>(static_cast<uintptr_t>(PyLong_AsUnsignedLongLong(address.ptr())));
        if (PyErr_Occurred())
            throw python_error();
        return true;
    } catch (...) {
        PyErr_Clear();
    }
    return false;
}

// Casts a Python object into a pointer to an opaque native type (Vulkan's handles
// are such pointers on 64-bit platforms, Direct3D's interfaces are classes), and a
// pointer back into its address, following the pattern of nanobind's caster for void*
template <typename T> struct native_pointer_caster {
    template <typename T_> using Cast = T*;
    template <typename T_> static constexpr bool can_cast() { return true; }
    using Value = T*;
    static constexpr auto Name = const_name("int");
    explicit operator T*() { return value; }
    Value value = nullptr;

    bool from_python(handle src, uint32_t, cleanup_list*) noexcept
    {
        void* address;
        if (!native_address(src, &address))
            return false;
        value = static_cast<T*>(address);
        return true;
    }

    static handle from_cpp(T* ptr, rv_policy, cleanup_list*) noexcept
    {
        return PyLong_FromVoidPtr(ptr);
    }
};

// Native enums, which the binding libraries represent by plain ints
template <typename E> struct native_enum_caster {
    NB_TYPE_CASTER(E, const_name("int"))

    bool from_python(handle src, uint32_t flags, cleanup_list* cleanup) noexcept
    {
        make_caster<long long> caster;
        if (!caster.from_python(src, flags, cleanup))
            return false;
        value = static_cast<E>(caster.value);
        return true;
    }

    static handle from_cpp(E value, rv_policy, cleanup_list*) noexcept
    {
        return PyLong_FromLongLong(static_cast<long long>(value));
    }
};

} // namespace nanobind::detail
