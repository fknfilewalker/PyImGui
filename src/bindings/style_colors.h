// The Colors array of a style struct, which litgen does not bind (it holds ImVec4s),
// as a Python sequence: style.colors[imgui.Col_.text] = (1, 1, 1, 1). Its items are
// the ImVec4s of the array itself, so style.colors[imgui.Col_.text].w = 0.5 changes
// the style too. generate.py adds it to the style classes (add_style_colors).

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/make_iterator.h>

#include "imgui.h"

template <typename Style, int Count> struct StyleColors {
    Style* style;

    ImVec4& at(int i)
    {
        if (i < 0)
            i += Count;
        if (i < 0 || i >= Count)
            throw nanobind::index_error();
        return style->Colors[i];
    }
};

template <int Count, typename Style> void bind_style_colors(nanobind::class_<Style>& style_class)
{
    namespace nb = nanobind;
    using Colors = StyleColors<Style, Count>;
    nb::class_<Colors>(style_class, "Colors", "The colors of a style, indexed by the Col_ enum")
        .def("__len__", [](const Colors&) { return Count; })
        .def("__getitem__", [](Colors& colors, int i) -> ImVec4& { return colors.at(i); },
             nb::rv_policy::reference_internal)
        .def("__setitem__", [](Colors& colors, int i, const ImVec4& color) { colors.at(i) = color; })
        .def("__iter__", [](const Colors& colors) {
            return nb::make_iterator(nb::type<Colors>(), "iterator", colors.style->Colors,
                                     colors.style->Colors + Count);
        }, nb::keep_alive<0, 1>());
    style_class.def_prop_ro("colors", [](Style& style) { return Colors{&style}; }, nb::keep_alive<0, 1>(),
                            "The colors, indexed by the Col_ enum: style.colors[Col_.text] = (1, 1, 1, 1)");
}
