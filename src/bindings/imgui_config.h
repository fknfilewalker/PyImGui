// Compile-time configuration of Dear ImGui (see imconfig.h) and ImPlot, for all of
// their sources and the bindings: CMakeLists.txt points IMGUI_USER_CONFIG here.

#pragma once

#include <stdexcept>

// Failed assertions throw, which the bindings turn into a RuntimeError in Python,
// rather than abort (debug builds) or go unchecked (release builds). This covers
// the API misuse that Dear ImGui detects, e.g. a missing End(). To recover from
// that instead, as Dear ImGui does without assertions, set
// io.config_error_recovery_enable_assert = False.
#define IM_ASSERT_STRINGIFY_(x) #x
#define IM_ASSERT_STRINGIFY(x) IM_ASSERT_STRINGIFY_(x)
#define IM_ASSERT(_EXPR)                                                       \
    do {                                                                       \
        if (!(_EXPR))                                                          \
            throw std::runtime_error("IM_ASSERT(" #_EXPR ") failed at "        \
                                     __FILE__ ":" IM_ASSERT_STRINGIFY(__LINE__)); \
    } while (0)

// The types that ImPlot's plotting functions are instantiated for: those that the
// bindings pass numpy's arrays as (int64_t among them, which is long on Linux and
// long long elsewhere)
#define IMPLOT_CUSTOM_NUMERIC_TYPES (signed char)(unsigned char)(short)(unsigned short)(int)(unsigned int)(long)(unsigned long)(long long)(unsigned long long)(float)(double)
