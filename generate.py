# /// script
# requires-python = ">=3.10"
# dependencies = ["litgen>=0.22"]
# ///
"""
Generates the Python bindings in src/ with litgen (https://pthom.github.io/litgen/)

    uv run generate.py

Parses the headers of the Dear ImGui, ImPlot and ImGuizmo releases that
CMakeLists.txt pins, and rewrites the code between the litgen markers of
src/bindings/*.cpp (nanobind) and of src/imgui/*.pyi (the stubs). Only needed after
changing the options below or bumping one of the *_GIT_TAGs: the generated code is
checked in, and building the package does not need litgen.

The options shape the Python API: snake_case names, `imgui.WindowFlags_.no_resize`,
functions that return `(changed, value)` in place of modifying their pointer
arguments, tuples accepted for ImVec2 and ImVec4. The headers are parsed as
upstream ships them, unmodified.
"""

import re
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import litgen
from codemanip.code_replacements import RegexReplacementList
from litgen.internal import cpp_to_python
from srcmlcpp.srcmlcpp_options import WarningType

ROOT = Path(__file__).parent
BINDINGS = ROOT / "src" / "bindings"
STUBS = ROOT / "src" / "imgui"


def sources(repository: str, variable: str) -> Path:
    """Downloads the release or commit of the GitHub `repository` that
    CMakeLists.txt pins with `variable`, once"""
    cmake = (ROOT / "CMakeLists.txt").read_text()
    tag = re.search(rf"set\({variable} (\S+)\)", cmake).group(1)
    name = repository.split("/")[1].lower()
    directory = ROOT / "build" / f"{name}-{tag}"
    if not directory.exists():
        url = f"https://github.com/{repository}/archive/{tag}.tar.gz"
        print(f"Downloading {url}")
        directory.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory.parent) as temporary:
            with urllib.request.urlopen(url) as response, tarfile.open(fileobj=response, mode="r|gz") as archive:
                archive.extractall(temporary, filter="data")
            # The archive holds a single directory, named after the repository and the tag
            next(Path(temporary).iterdir()).rename(directory)
    return directory


def join(*alternatives: str) -> str:
    return "|".join(alternatives)


def imgui_options(stdlib: bool = False) -> litgen.LitgenOptions:
    """Options for imgui.h, or for misc/cpp/imgui_stdlib.h with `stdlib`"""
    options = litgen.LitgenOptions()
    options.use_nanobind()

    # Parsing
    def preprocess(code: str) -> str:
        # printf format attributes, which srcML cannot parse, and the debugger
        # attribute of inline helpers, which it would take for part of their return type
        code = re.sub(r"IM_FMT(ARGS|LIST)\(\d\)", "", code)
        return re.sub(r"(?<!#define )\bIM_NODEBUGSTEP\b\s*", "", code)

    options.srcmlcpp_options.code_preprocess_function = preprocess
    options.srcmlcpp_options.functions_api_prefixes = "IMGUI_API"
    options.fn_exclude_non_api = False  # inline helpers like ImVec2's constructors carry no IMGUI_API
    options.srcmlcpp_options.header_filter_acceptable__regex += join(
        "", "^IMGUI_DISABLE$", "^IMGUI_OVERRIDE_DRAWVERT_STRUCT_LAYOUT$")
    options.srcmlcpp_options.flag_show_progress = False

    # Names: ImGui::BeginTable() -> imgui.begin_table(), ImGuiWindowFlags_NoResize
    # -> imgui.WindowFlags_.no_resize, ImGuiIO -> imgui.IO
    options.namespaces_root = ["ImGui"]
    options.python_convert_to_snake_case = True
    options.type_replacements = cpp_to_python.standard_type_replacements()
    options.type_replacements.merge_replacements(RegexReplacementList.from_string(r"""
        ^signed char$ -> int
        ^char$ -> int
        \bImVector\s*<\s*(.*?)\s*> -> ImVector_\1
    """))
    options.type_replacements.add_last_replacement(r"ImGui([A-Z][a-zA-Z0-9]*)", r"\1")
    options.function_names_replacements.merge_replacements(RegexReplacementList.from_string(r"""
        RGBtoHSV -> RgbToHsv
        HSVtoRGB -> HsvToRgb
    """))
    options.var_names_replacements.add_last_replacement(r"^id$", "id_")  # id() is a Python builtin
    options.var_names_replacements.add_last_replacement(r"im_gui_", "")
    options.enum_flag_remove_values_prefix = True
    options.enum_flag_remove_values_prefix_group_private = True
    options.enum_make_arithmetic__regex = r".*"
    options.enum_make_flag__regex = r".*"

    # Functions
    options.fn_exclude_by_name__regex = join(
        # Memory allocators and raw buffers
        r"^SetAllocatorFunctions$", r"^GetAllocatorFunctions$", r"^MemAlloc$", r"^MemFree$",
        r"^GetTexDataAs", r"^CalcTextSizeA$", r"^appendfv$",
        # va_list variants of the printf-like functions, which are bound themselves
        r"[a-z0-9]V$",
        # Drag and drop payloads are raw memory
        r"^SetDragDropPayload$", r"^AcceptDragDropPayload$", r"^GetDragDropPayload$",
        # Their output parameters would become inputs: see the custom bindings below
        r"^ColorConvertRGBtoHSV$", r"^ColorConvertHSVtoRGB$",
    )
    if not stdlib:
        # Bound from imgui_stdlib.h, which takes a std::string rather than a char buffer
        options.fn_exclude_by_name__regex += r"|^InputText"
    # Functions that take double pointers, single chars or wide strings, or
    # draw callbacks (C function pointers, which Python cannot provide)
    options.fn_exclude_by_param_type__regex = r"^char$|^const ImWchar \*$|\* \*|^ImDrawCallback$"
    # Callbacks are C function pointers, which Python cannot provide
    options.fn_params_exclude_types__regex = join(
        r"size_t[ ]*\*", r"^ImGuiInputTextCallback$", r"^ImGuiSizeCallback$")
    options.fn_params_exclude_names__regex = join(r"^user_data$", r"^custom_callback_data$")
    # bool* p_open, float* v, ... -> returned as (result, value)
    options.fn_params_output_modifiable_immutable_to_return__regex = r".*"
    options.fn_params_replace_c_array_modifiable_by_boxed__regex = ""
    options.fn_params_replace_buffer_by_array__regex = r"^Plot"
    options.fn_params_type_replacements.add_replacements([(r"\bImVec2\b", "ImVec2Like"), (r"\bImVec4\b", "ImVec4Like")])

    def is_immutable_cpp_type(cpp_type: str) -> bool:
        # Integers and enums, whose defaults need not be re-created at every call
        return cpp_type.endswith("Flags") or cpp_type in (
            "ImGuiDataType_", "ImGuiKey", "ImGuiID", "ImS8", "ImU8", "ImS16", "ImU16",
            "ImS32", "ImU32", "ImS64", "ImU64", "IM_COL32")

    options.fn_params_adapt_mutable_param_with_default_value__fn_is_known_immutable_type = is_immutable_cpp_type
    # Returned pointers and references refer to ImGui's own data, which Python must not free
    options.fn_return_force_policy_reference_for_pointers__regex = r".*"
    options.fn_return_force_policy_reference_for_references__regex = r".*"
    # Overloads that nanobind cannot tell apart from a bare function pointer
    options.fn_force_overload__regex = join(
        r"^SetScroll", r"^Drag", r"^Slider", r"^InputText", r"Popup", r"DrawList", r"^Table",
        r"^SetWindowPos", r"^SetWindowSize", r"^SetWindowCollapsed", r"^ImageButton$", r"^IsKey",
        r"^IsMouse", r"^AddPolyline", r"^AddConvexPolyFilled", r"^AddConcavePolyFilled",
        r"^ColorPicker", r"^ColorEdit", r"^Shortcut", r"^SetItemKeyOwner", r"^GetIO", r"^GetPlatformIO",
        r"^PushFont")
    # (ImGuiTextFilter::Draw: its obsolete overload, which litgen skips)
    options.fn_force_lambda__regex = join(r"^SetWindowFocus$", r"^AddRect$", r"^PathStroke$", r"^AddPolyline$", r"^Draw$")

    # Classes
    options.class_copy__regex = r"^ImVec2$|^ImVec4$|^ImColor$"
    # Unchecked in release builds: see the __getitem__ of the custom bindings
    options.member_exclude_by_name_and_class__regex["ImVec2"] = r"^operator\[\]$"
    options.member_exclude_by_name_and_class__regex["ImVec4"] = r"^operator\[\]$"
    options.member_exclude_by_name__regex = join(r"^TexPixelsAlpha8$", r"^ErrorCallback$")
    options.member_exclude_by_type__regex = join(
        r"^ImDrawCallback$", r"const ImWchar\s*\*", r"unsigned char\s*\*", r"unsigned int\s*\*",
        r"ImGuiStoragePair", r"ImFontLoader", r"^ImFontAtlasBuilder", r"^ImSmallStack_")
    options.member_readonly_by_type__regex = r"^char\s*\*"
    options.member_numeric_c_array_types += "|" + join(
        "ImGuiID", "ImS8", "ImU8", "ImS16", "ImU16", "ImS32", "ImU32", "ImS64", "ImU64")
    # ImSmallStack_: an internal fixed-capacity stack (of ImDrawList's draw flags)
    options.class_exclude_by_name__regex = "^ImStableVector$|^ImSmallStack_$"
    add_imvector_specializations(options)

    options.srcmlcpp_options.ignored_warnings = [
        WarningType.LitgenClassMemberSkipBitfield,
        WarningType.LitgenClassMemberUnparsableSize,
        WarningType.LitgenClassMemberNonNumericCStyleArray,
    ]
    options.srcmlcpp_options.ignored_warning_parts += [
        "C style function pointers",
        "function_decl as a param",
        'Unsupported zero param "operator',
        "operators are supported only when implemented as a member functions",
        "Ignoring template function",
        "inline ImVector<T>& operator=(const ImVector<T>& src)",
    ]

    if not stdlib:
        add_custom_bindings(options)

    # Layout of the stubs
    options.cpp_indent_size = 4
    options.python_max_line_length = -1  # Dear ImGui declares each function on a single line
    options.original_location_flag_show = False
    options.original_signature_flag_show = True
    options.python_run_black_formatter = True
    options.python_black_formatter_line_length = 120

    def postprocess(stub: str) -> str:
        stub = re.sub(r"\bImVector\s*\[\s*(.*?)\s*\]", r"ImVector_\1", stub)
        stub = re.sub(r"\bImVector_(ImVec[24])Like\b", r"ImVector_\1", stub)
        stub = stub.replace("= SelectionRequestType()", "= SelectionRequestType.none")
        return grouped_overloads(stub)

    options.postprocess_stub_function = postprocess
    return options


def grouped_overloads(stub: str) -> str:
    """Moves the overloads of each module-level function next to each other, as
    type checkers require. (imgui.h declares SetWindowPos(name, ...) and the like
    apart from their unnamed variants.)"""
    lines = stub.split("\n")
    chunks: list[tuple[str | None, list[str]]] = []  # (function name or None, lines)
    i = 0
    while i < len(lines):
        match = re.match(r"(?:@overload\n)?def (\w+)\(", "\n".join(lines[i:i + 2]))
        if not match:
            chunks.append((None, [lines[i]]))
            i += 1
            continue
        # The function comes with its original C++ signature, its (multi-line) def and its body
        start = i
        if chunks and chunks[-1][0] is None and chunks[-1][1][0].endswith("/* original C++ signature */"):
            head = chunks.pop()[1]
        else:
            head = []
        i += 1
        while i < len(lines) and (lines[i].startswith((" ", ")", "def "))
                                  or (lines[i] == "" and i + 1 < len(lines) and lines[i + 1].startswith(" "))):
            if lines[i].startswith("def ") and not lines[i - 1].startswith("@overload"):
                break
            i += 1
        chunks.append((match.group(1), head + lines[start:i] + [""]))
    grouped: list[str] = []
    emitted: set[str] = set()
    for name, chunk in chunks:
        if name is None:
            grouped += chunk
        elif name not in emitted:
            emitted.add(name)
            for other_name, other_chunk in chunks:
                if other_name == name:
                    grouped += other_chunk
    return "\n".join(grouped)


def single_overloads_undecorated(stub: str) -> str:
    """Removes @overload from the functions that are defined once: those whose
    other overloads are excluded"""
    counts: dict[str, int] = {}
    for name in re.findall(r"^def (\w+)\(", stub, flags=re.M):
        counts[name] = counts.get(name, 0) + 1
    return re.sub(r"^@overload\n(?=def (\w+)\()", lambda m: "" if counts[m.group(1)] == 1 else m.group(0), stub,
                  flags=re.M)


def add_imvector_specializations(options: litgen.LitgenOptions) -> None:
    """ImVector<T> becomes ImVector_T, iterable, for the Ts that imgui.h uses"""
    types = [
        "int", "uint", "ushort", "float", "char", "uchar", "ImDrawCmd", "ImDrawChannel", "ImDrawVert", "ImVec4", "ImVec2",
        "ImDrawList*", "ImFont*", "ImFontGlyph", "ImGuiPlatformMonitor", "ImGuiViewport*", "ImFontConfig",
        "ImFontConfig*", "ImGuiSelectionRequest", "ImTextureData*", "ImTextureRef", "ImTextureRect",
    ]
    synonyms = [
        "ImTextureID=int", "ImDrawIdx=ushort", "ImGuiID=uint", "ImU32=uint", "ImU16=ushort", "ImWchar32=uint",
        "ImWchar=ImWchar32", "ImU8=uchar",
    ]
    options.class_template_options.add_specialization(
        name_regex="^ImVector$", cpp_types_list_str=types, cpp_synonyms_list_str=synonyms)
    # Publish the size, indexing, iteration and push/pop, not the raw memory
    # management. (litgen also mistypes const T& for pointer Ts, in resize(), find()...)
    options.member_exclude_by_name_and_class__regex["ImVector"] = join(
        "^Size$", "^Capacity$", "^Data$", "^clear_delete$", "^size_in_bytes$", "^max_size$", "^capacity$",
        "^begin$", "^end$", "^front$", "^back$", "^swap$", "^_grow_capacity$", "^resize$", "^shrink$",
        "^reserve", "^erase", "^insert$", "^contains$", "^find", "^index_from_ptr$")
    for t in types:
        python_type = t.replace("ImGui", "").replace("*", "_ptr")
        options.class_iterables_infos.add_iterable_class(
            python_class_name__regex=f"^ImVector_{python_type}$",
            python_iterable_type=python_type.removesuffix("_ptr"))
    options.srcmlcpp_options.ignored_warning_parts += [
        "Excluding template type ImVector<const char*>",
        "Excluding template type ImVector<ImGuiTextRange>",
        "Excluding template type ImVector<ImGuiStoragePair>",
        "Excluding template type ImVector<ImDrawListSharedData*>",
        "Excluding template type const ImVector<T>",
        "Excluding template type ImVector<T>",
        "Ignoring template class ImStableVector",
    ]


def add_custom_bindings(options: litgen.LitgenOptions) -> None:
    """Hand-written additions: tuple conversions and arithmetic for ImVec2 and
    ImVec4, the style colors, the settings file names, the IM_COL32 color macros,
    and the color conversions that return their output"""
    for vec, n in ("ImVec2", 2), ("ImVec4", 4):
        fields = "xyzw"[:n]
        floats = ", ".join(["float"] * n)
        options.custom_bindings.add_custom_bindings_to_class(
            qualified_class=vec,
            stub_code=f'''
            @overload
            def __init__(self, xy{"zw" if n == 4 else ""}: tuple[{floats}] | list[float]) -> None:
                """From a tuple or list of {n} floats, which are also accepted wherever {vec} is"""
                ...
            def __repr__(self) -> str: ...
            def __len__(self) -> int: ...
            def __iter__(self) -> Iterator[float]: ...
            def __getitem__(self, index: int) -> float: ...
            def __setitem__(self, index: int, value: float) -> None: ...
            def __eq__(self, other: object) -> bool: ...
            def __neg__(self) -> {vec}: ...
            def __add__(self, other: {vec}Like) -> {vec}: ...
            def __sub__(self, other: {vec}Like) -> {vec}: ...
            def __mul__(self, other: {vec}Like | float) -> {vec}: ...
            def __truediv__(self, other: {vec}Like | float) -> {vec}: ...
            ''',
            pydef_code=f'''
            LG_CLASS.def("__init__", []({vec}* self, nb::sequence s) {{
                if (nb::len(s) != {n})
                    throw nb::value_error("{vec} needs a sequence of {n} floats");
                new (self) {vec}({", ".join(f"nb::cast<float>(s[{i}])" for i in range(n))});
            }}, nb::arg("{fields}"));
            nb::implicitly_convertible<nb::tuple, {vec}>();
            nb::implicitly_convertible<nb::list, {vec}>();
            LG_CLASS.def("__repr__", []({vec} v) {{
                return nb::str("{vec}({"{}, " * (n - 1)}{{}})").format({", ".join(f"v.{c}" for c in fields)});
            }});
            LG_CLASS.def("__len__", []({vec}) {{ return {n}; }});
            LG_CLASS.def("__iter__", [](const {vec}& v) {{
                return nb::make_iterator(nb::type<{vec}>(), "iterator", &v.x, &v.x + {n});
            }}, nb::keep_alive<0, 1>());
            LG_CLASS.def("__getitem__", [](const {vec}& v, int i) {{
                if (i < 0) i += {n};
                if (i < 0 || i >= {n}) throw nb::index_error();
                return (&v.x)[i];
            }});
            LG_CLASS.def("__setitem__", []({vec}& v, int i, float value) {{
                if (i < 0) i += {n};
                if (i < 0 || i >= {n}) throw nb::index_error();
                (&v.x)[i] = value;
            }});
            LG_CLASS.def("__eq__", []({vec} a, {vec} b) {{ return {" && ".join(f"a.{c} == b.{c}" for c in fields)}; }}, nb::is_operator());
            LG_CLASS.def("__neg__", []({vec} a) {{ return {vec}({", ".join(f"-a.{c}" for c in fields)}); }}, nb::is_operator());
            LG_CLASS.def("__add__", []({vec} a, {vec} b) {{ return {vec}({", ".join(f"a.{c} + b.{c}" for c in fields)}); }}, nb::is_operator());
            LG_CLASS.def("__sub__", []({vec} a, {vec} b) {{ return {vec}({", ".join(f"a.{c} - b.{c}" for c in fields)}); }}, nb::is_operator());
            LG_CLASS.def("__mul__", []({vec} a, {vec} b) {{ return {vec}({", ".join(f"a.{c} * b.{c}" for c in fields)}); }}, nb::is_operator());
            LG_CLASS.def("__mul__", []({vec} a, float b) {{ return {vec}({", ".join(f"a.{c} * b" for c in fields)}); }}, nb::is_operator());
            LG_CLASS.def("__truediv__", []({vec} a, {vec} b) {{ return {vec}({", ".join(f"a.{c} / b.{c}" for c in fields)}); }}, nb::is_operator());
            LG_CLASS.def("__truediv__", []({vec} a, float b) {{ return {vec}({", ".join(f"a.{c} / b" for c in fields)}); }}, nb::is_operator());
            ''',
        )

    add_style_colors(options, "ImGuiStyle", "ImGuiCol_COUNT", "Col")

    # The vertices and indices for renderer backends, as numpy views to upload at once
    options.custom_bindings.add_custom_bindings_to_class(
        qualified_class="ImDrawList",
        stub_code='''
        def vtx_buffer_array(self) -> np.ndarray:
            """vtx_buffer as a (size, VERTEX_SIZE) uint8 array, a view that is valid until the next frame"""
            ...
        def idx_buffer_array(self) -> np.ndarray:
            """idx_buffer as a (size,) array of ImDrawIdx (uint16), a view that is valid until the next frame"""
            ...
        ''',
        pydef_code='''
        LG_CLASS.def("vtx_buffer_array", [](nb::handle self) {
            const ImDrawList& l = nb::cast<const ImDrawList&>(self);
            size_t shape[2] = {(size_t)l.VtxBuffer.Size, sizeof(ImDrawVert)};
            return nb::ndarray<nb::numpy, unsigned char, nb::ndim<2>>(l.VtxBuffer.Data, 2, shape, self);
        }, "vtx_buffer as a (size, VERTEX_SIZE) uint8 array, a view that is valid until the next frame");
        LG_CLASS.def("idx_buffer_array", [](nb::handle self) {
            const ImDrawList& l = nb::cast<const ImDrawList&>(self);
            size_t shape[1] = {(size_t)l.IdxBuffer.Size};
            return nb::ndarray<nb::numpy, ImDrawIdx, nb::ndim<1>>(l.IdxBuffer.Data, 1, shape, self);
        }, "idx_buffer as a (size,) array of ImDrawIdx (uint16), a view that is valid until the next frame");
        ''',
    )

    # The pixels for renderer backends, as a numpy view instead of the void* of GetPixels()
    options.custom_bindings.add_custom_bindings_to_class(
        qualified_class="ImTextureData",
        stub_code='''
        def get_pixels_array(self) -> np.ndarray:
            """The pixels as a (height, width, bytes_per_pixel) uint8 array, a view that keeps the texture alive"""
            ...
        ''',
        pydef_code='''
        LG_CLASS.def("get_pixels_array", [](nb::handle self) {
            const ImTextureData& t = nb::cast<const ImTextureData&>(self);
            size_t shape[3] = {(size_t)t.Height, (size_t)t.Width, (size_t)t.BytesPerPixel};
            return nb::ndarray<nb::numpy, unsigned char, nb::ndim<3>>(t.Pixels, 3, shape, self);
        }, "The pixels as a (height, width, bytes_per_pixel) uint8 array, a view that keeps the texture alive");
        ''',
    )

    # ImGuiIO keeps the pointers that it is given for the .ini and .log files:
    # these keep a copy of the string alive for as long as the IO
    options.member_exclude_by_name_and_class__regex["ImGuiIO"] = "^IniFilename$|^LogFilename$"
    filenames = {"ini_filename": ("IniFilename", "imgui.ini", "settings"), "log_filename": ("LogFilename", "imgui_log.txt", "logs")}
    options.custom_bindings.add_custom_bindings_to_class(
        qualified_class="ImGuiIO",
        stub_code="".join(f'''
        @property
        def {name}(self) -> Optional[str]:
            """= "{default}": path of the file for the {what}, relative to the working directory. None to disable it."""
            ...
        @{name}.setter
        def {name}(self, value: Optional[str]) -> None: ...
        ''' for name, (member, default, what) in filenames.items()),
        pydef_code="".join(f'''
        LG_CLASS.def_prop_rw("{name}",
            [](const ImGuiIO& io) {{ return io.{member} ? std::optional<std::string>(io.{member}) : std::nullopt; }},
            [](ImGuiIO& io, std::optional<std::string> value) {{
                static std::unordered_map<const ImGuiIO*, std::string> storage;
                io.{member} = value ? (storage[&io] = *value).c_str() : nullptr;
            }});
        ''' for name, (member, default, what) in filenames.items()),
    )

    options.custom_bindings.add_custom_bindings_to_main_module(
        stub_code='''
        IM_COL32_WHITE: int  # Opaque white = 0xFFFFFFFF
        IM_COL32_BLACK: int  # Opaque black
        IM_COL32_BLACK_TRANS: int  # Transparent black = 0x00000000
        FLT_MIN: float  # Smallest positive float, e.g. set_next_item_width(-FLT_MIN) to fill the width
        FLT_MAX: float  # Largest float, e.g. the mouse position that means "no mouse"
        VERTEX_SIZE: int  # sizeof(ImDrawVert), the stride of ImDrawList.vtx_buffer
        INDEX_SIZE: int  # sizeof(ImDrawIdx), 2 (16-bit indices) or 4
        def IM_COL32(r: int, g: int, b: int, a: int) -> int:
            """Packs a color into an ImU32, as the ImDrawList functions take it"""
            ...
        def color_convert_rgb_to_hsv(r: float, g: float, b: float) -> tuple[float, float, float]:
            """Convert rgb floats ([0-1],[0-1],[0-1]) to hsv floats ([0-1],[0-1],[0-1])"""
            ...
        def color_convert_hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
            """Convert hsv floats ([0-1],[0-1],[0-1]) to rgb floats ([0-1],[0-1],[0-1])"""
            ...
        ''',
        pydef_code='''
        LG_MODULE.attr("IM_COL32_WHITE") = IM_COL32_WHITE;
        LG_MODULE.attr("IM_COL32_BLACK") = IM_COL32_BLACK;
        LG_MODULE.attr("IM_COL32_BLACK_TRANS") = IM_COL32_BLACK_TRANS;
        LG_MODULE.attr("FLT_MIN") = FLT_MIN;
        LG_MODULE.attr("FLT_MAX") = FLT_MAX;
        LG_MODULE.attr("VERTEX_SIZE") = sizeof(ImDrawVert);
        LG_MODULE.attr("INDEX_SIZE") = sizeof(ImDrawIdx);
        LG_MODULE.def("IM_COL32", [](ImU32 r, ImU32 g, ImU32 b, ImU32 a) { return IM_COL32(r, g, b, a); },
            nb::arg("r"), nb::arg("g"), nb::arg("b"), nb::arg("a"),
            "Packs a color into an ImU32, as the ImDrawList functions take it");
        LG_MODULE.def("color_convert_rgb_to_hsv", [](float r, float g, float b) {
            float h, s, v;
            ImGui::ColorConvertRGBtoHSV(r, g, b, h, s, v);
            return std::make_tuple(h, s, v);
        }, nb::arg("r"), nb::arg("g"), nb::arg("b"),
        "Convert rgb floats ([0-1],[0-1],[0-1]) to hsv floats ([0-1],[0-1],[0-1])");
        LG_MODULE.def("color_convert_hsv_to_rgb", [](float h, float s, float v) {
            float r, g, b;
            ImGui::ColorConvertHSVtoRGB(h, s, v, r, g, b);
            return std::make_tuple(r, g, b);
        }, nb::arg("h"), nb::arg("s"), nb::arg("v"),
        "Convert hsv floats ([0-1],[0-1],[0-1]) to rgb floats ([0-1],[0-1],[0-1])");
        ''',
    )


def add_style_colors(options: litgen.LitgenOptions, style: str, count: str, col: str) -> None:
    """style.colors, the Colors array of the `style` class, which litgen does not
    bind (see src/bindings/style_colors.h). `count` is the size of the array, and
    `col` the Python type of its indices."""
    options.custom_bindings.add_custom_bindings_to_class(
        qualified_class=style,
        stub_code=f'''
        class Colors:
            """The colors of a style, indexed by the Col_ enum"""
            def __len__(self) -> int: ...
            def __getitem__(self, idx: {col}) -> ImVec4: ...
            def __setitem__(self, idx: {col}, color: ImVec4Like) -> None: ...
            def __iter__(self) -> Iterator[ImVec4]: ...
        @property
        def colors(self) -> Style.Colors:
            """The colors, indexed by the Col_ enum: style.colors[Col_.text] = (1, 1, 1, 1)"""
            ...
        ''',
        pydef_code=f"bind_style_colors<{count}>(LG_CLASS);",
    )


def in_annotations(stub: str, types: str, by: str) -> str:
    """Replaces the C++ types that the regex `types` matches by `by` in the
    annotations of `stub` (but not in its comments)"""
    return re.sub(rf"(:\s*|->\s*|\[)(?:{types})\b(?!_)", rf"\g<1>{by}", stub)


def backend_options(prefix: str) -> litgen.LitgenOptions:
    """Options for the backend headers, whose functions become those of a
    submodule: ImGui_ImplSDL3_NewFrame() -> imgui.impl_sdl3.new_frame()"""
    options = litgen.LitgenOptions()
    options.use_nanobind()
    options.srcmlcpp_options.functions_api_prefixes = "IMGUI_IMPL_API"
    options.srcmlcpp_options.header_filter_acceptable__regex += "|^IMGUI_DISABLE$"
    options.srcmlcpp_options.flag_show_progress = False
    options.function_names_replacements.add_first_replacement(f"^{prefix}", "")
    # "OpenGL", "SDLGPU" would otherwise become "open_gl", "s_d_l_g_p_u"
    options.function_names_replacements.merge_replacements(RegexReplacementList.from_string(r"""
        OpenGL -> Opengl
        SDLRenderer -> SdlRenderer
        SDLGPU -> SdlGpu
        D3D -> D3d
    """))
    # Also enables `const char* x = nullptr` -> `x: Optional[str] = None`
    options.fn_params_output_modifiable_immutable_to_return__regex = r".*"
    options.type_replacements.add_last_replacement(f"^{prefix}", "")
    options.type_replacements.add_last_replacement(r"ImGui([A-Z][a-zA-Z0-9]*)", r"\1")
    options.original_location_flag_show = False
    options.python_run_black_formatter = True
    options.python_black_formatter_line_length = 120
    return options


def impl_sdl3_options() -> litgen.LitgenOptions:
    options = backend_options("ImGui_ImplSDL3_")
    # The GL context is a void* in the header, which nanobind would only accept as
    # a capsule: declare its actual type (so that the ctypes caster in the
    # bindings applies), and bind through a lambda, which uses that declaration
    options.srcmlcpp_options.code_preprocess_function = lambda code: code.replace(
        "void* sdl_gl_context", "SDL_GLContext sdl_gl_context")
    options.fn_force_lambda__regex = r"^ImGui_ImplSDL3_InitForOpenGL$"
    # Manual gamepad mode takes a C array of SDL_Gamepad*: only the automatic modes are supported
    options.fn_params_exclude_names__regex = r"^manual_gamepads_"
    options.postprocess_stub_function = lambda stub: in_annotations(
        stub, "SDL_Window|SDL_Renderer|SDL_GLContext|SDL_Event", "SDLPointer")
    return options


def impl_sdlgpu3_options() -> litgen.LitgenOptions:
    options = backend_options("ImGui_ImplSDLGPU3_")
    # Render state shared with draw callbacks, which Python cannot install
    options.class_exclude_by_name__regex = r"^ImGui_ImplSDLGPU3_RenderState$"

    # SDL's handles are ctypes objects or addresses, its enums ints (as in pysdl3)
    def postprocess(stub: str) -> str:
        stub = in_annotations(stub, "SDL_GPU(Device|CommandBuffer|RenderPass|GraphicsPipeline)", "SDLPointer")
        stub = in_annotations(stub, "SDL_GPU(TextureFormat|SampleCount|SwapchainComposition|PresentMode)", "int")
        return re.sub(r"= (SDL_GPU_[A-Z0-9_]+)", r"= sdl3.\1", stub)

    options.postprocess_stub_function = postprocess
    return options


def impl_opengl3_options() -> litgen.LitgenOptions:
    options = backend_options("ImGui_ImplOpenGL3_")
    # Render state shared with draw callbacks, which Python cannot install
    options.class_exclude_by_name__regex = r"^ImGui_ImplOpenGL3_RenderState$"
    options.fn_exclude_by_name__regex = r"^ImGui_ImplOpenGL3_GetRenderState$"
    return options


def implot_options() -> litgen.LitgenOptions:
    """Options for implot.h, whose functions become those of a submodule:
    ImPlot::PlotLine() -> imgui.implot.plot_line(), ImPlotFlags_NoTitle ->
    imgui.implot.Flags_.no_title. The plotting functions take numpy arrays."""
    options = litgen.LitgenOptions()
    options.use_nanobind()

    def preprocess(code: str) -> str:
        # printf format attributes, which srcML cannot parse
        code = re.sub(r"IM_FMT(ARGS|LIST)\(\d\)", "", code)
        # Without its constructors, ImPlotSpec gets one with a keyword argument per
        # member: implot.Spec(line_color=..., flags=...). (Only litgen reads this.)
        code = code.replace("    ImPlotSpec() { }\n", "")
        return re.sub(r"template <typename \.\.\.Args>\s*ImPlotSpec\(Args\.\.\. args\) \{.*?\}", "", code, flags=re.S)

    options.srcmlcpp_options.code_preprocess_function = preprocess
    options.srcmlcpp_options.functions_api_prefixes = "IMPLOT_API|IMPLOT_TMP"
    options.srcmlcpp_options.header_filter_acceptable__regex += join("", "^IMGUI_DISABLE$", "^IMGUI_HAS_TEXTURES$")
    options.srcmlcpp_options.flag_show_progress = False

    options.namespaces_root = ["ImPlot"]
    options.python_convert_to_snake_case = True
    options.type_replacements = cpp_to_python.standard_type_replacements()
    options.type_replacements.add_last_replacement(r"ImPlot([A-Z][a-zA-Z0-9]*)", r"\1")
    options.type_replacements.add_last_replacement(r"ImGui([A-Z][a-zA-Z0-9]*)", r"\1")
    options.function_names_replacements.merge_replacements(RegexReplacementList.from_string(r"""
        Histogram2D -> Histogram2d
    """))
    options.var_names_replacements.add_last_replacement(r"^id$", "id_")
    options.enum_flag_remove_values_prefix = True
    options.enum_make_arithmetic__regex = r".*"
    options.enum_make_flag__regex = r".*"

    # The plotting functions, templates over the type of their data, take numpy
    # arrays, of the types that ImPlot is compiled for (IMPLOT_CUSTOM_NUMERIC_TYPES)
    options.fn_params_replace_buffer_by_array__regex = r"^Plot"
    options.fn_params_buffer_types = join(
        "uint8_t", "int8_t", "uint16_t", "int16_t", "uint32_t", "int32_t", "np_uint_l", "np_int_l", "float", "double")
    options.fn_exclude_by_name__regex = join(
        # Functions that take C callbacks for their data, and the va_list variants
        r"G$", r"V$",
        # For an ImPlot in another DLL than Dear ImGui: this extension holds both
        r"^SetImGuiContext$",
        # 2D arrays, and arrays of labels: see add_implot_custom_bindings()
        r"^PlotHeatmap$", r"^PlotBarGroups$", r"^SetupAxisTicks$",
    )
    # C callbacks, and the ImTextureID variant of PlotImage() for Dear ImGui < 1.92
    options.fn_exclude_by_param_type__regex = r"^ImPlotFormatter$|^ImPlotTransform$|^ImTextureID$"
    # Arrays of one ratio per row and column, which the output parameters below would mistake for single values
    options.fn_params_exclude_names__regex = r"^row_ratios$|^col_ratios$"
    options.fn_params_output_modifiable_immutable_to_return__regex = r".*"
    options.fn_params_type_replacements.add_replacements([
        (r"\bImVec2\b", "ImVec2Like"), (r"\bImVec4\b", "ImVec4Like"), (r"\bImPlotPoint\b", "PointLike")])
    options.fn_return_force_policy_reference_for_pointers__regex = r".*"
    options.fn_return_force_policy_reference_for_references__regex = r".*"
    options.fn_force_overload__regex = join(r"^BeginPlot$", r"^PushStyleVar$", r"^PushStyleColor$")

    # ImPlotSpec's arrays of per-point colors and sizes are pointers, which Python cannot hold
    options.member_exclude_by_name_and_class__regex["ImPlotSpec"] = join(
        r"^LineColors$", r"^FillColors$", r"^MarkerSizes$", r"^MarkerLineColors$", r"^MarkerFillColors$", r"^SetProp$")
    options.member_exclude_by_name_and_class__regex["ImPlotPoint"] = r"^operator\[\]$"  # see the custom bindings
    options.class_copy__regex = r"^ImPlotPoint$|^ImPlotRange$|^ImPlotRect$|^ImPlotSpec$|^ImPlotStyle$"
    options.srcmlcpp_options.ignored_warning_parts += ["ImVec4  Colors[ImPlotCol_COUNT]"]
    add_implot_custom_bindings(options)
    add_style_colors(options, "ImPlotStyle", "ImPlotCol_COUNT", "Col")

    options.cpp_indent_size = 4
    options.python_max_line_length = -1
    options.original_location_flag_show = False
    options.original_signature_flag_show = True
    options.python_run_black_formatter = True
    options.python_black_formatter_line_length = 120

    def postprocess(stub: str) -> str:
        # litgen reads the "1f" of "%.1f" as a float literal
        stub = stub.replace('"%.1"', '"%.1f"')
        # Enum values that litgen leaves as in C++: Bin_Sturges -> Bin_.sturges
        stub = re.sub(r"(=\s*)([A-Z][a-z]\w*_)([A-Z]\w*)",
                      lambda m: m.group(1) + m.group(2) + "." + re.sub(r"(?<!^)(?=[A-Z])", "_", m.group(3)).lower(), stub)
        return single_overloads_undecorated(grouped_overloads(stub))

    options.postprocess_stub_function = postprocess
    return options


def add_implot_custom_bindings(options: litgen.LitgenOptions) -> None:
    """Hand-written additions: tuple conversions for ImPlotPoint, the IMPLOT_AUTO
    constants, and the functions that take 2D arrays or labels, which
    bind_array_functions() in src/bindings/implot_bindings.cpp binds"""
    options.custom_bindings.add_custom_bindings_to_class(
        qualified_class="ImPlotPoint",
        stub_code='''
        @overload
        def __init__(self, xy: tuple[float, float] | list[float]) -> None:
            """From a tuple or list of 2 floats, which are also accepted wherever a Point is"""
            ...
        def __repr__(self) -> str: ...
        def __len__(self) -> int: ...
        def __iter__(self) -> Iterator[float]: ...
        def __getitem__(self, index: int) -> float: ...
        ''',
        pydef_code='''
        LG_CLASS.def("__init__", [](ImPlotPoint* self, nb::sequence s) {
            if (nb::len(s) != 2)
                throw nb::value_error("Point needs a sequence of 2 floats");
            new (self) ImPlotPoint(nb::cast<double>(s[0]), nb::cast<double>(s[1]));
        }, nb::arg("xy"));
        nb::implicitly_convertible<nb::tuple, ImPlotPoint>();
        nb::implicitly_convertible<nb::list, ImPlotPoint>();
        LG_CLASS.def("__repr__", [](ImPlotPoint p) { return nb::str("Point({}, {})").format(p.x, p.y); });
        LG_CLASS.def("__len__", [](ImPlotPoint) { return 2; });
        LG_CLASS.def("__iter__", [](const ImPlotPoint& p) {
            return nb::make_iterator(nb::type<ImPlotPoint>(), "iterator", &p.x, &p.x + 2);
        }, nb::keep_alive<0, 1>());
        LG_CLASS.def("__getitem__", [](const ImPlotPoint& p, int i) {
            if (i < 0) i += 2;
            if (i < 0 || i >= 2) throw nb::index_error();
            return i == 0 ? p.x : p.y;
        });
        ''',
    )
    options.custom_bindings.add_custom_bindings_to_main_module(
        stub_code='''
        IMPLOT_AUTO: int  # Deduce the value automatically: the next axis, marker, colormap... = -1
        IMPLOT_AUTO_COL: ImVec4  # Deduce the color automatically (from the colormap or the item) = ImVec4(0, 0, 0, -1)
        def plot_heatmap(
            label_id: str, values: np.ndarray, scale_min: float = 0, scale_max: float = 0,
            label_fmt: Optional[str] = "%.1f", bounds_min: PointLike = (0, 0), bounds_max: PointLike = (1, 1),
            spec: Spec = Spec(),
        ) -> None:
            """Plots a 2D heatmap of the (rows, cols) array `values`. Leave `scale_min` and `scale_max` both at 0
            for automatic color scaling, or set them to a predefined range. `label_fmt` can be None for no labels."""
            ...
        def plot_bar_groups(
            label_ids: list[str], values: np.ndarray, group_size: float = 0.67, shift: float = 0, spec: Spec = Spec()
        ) -> None:
            """Plots a group of bars: `values` is a (len(label_ids), group count) array"""
            ...
        @overload
        def setup_axis_ticks(axis: ImAxis, values: list[float] | np.ndarray, labels: Optional[list[str]] = None, keep_default: bool = False) -> None:
            """Sets an axis' ticks and optionally the labels. To keep the default ticks, set `keep_default`=True."""
            ...
        @overload
        def setup_axis_ticks(axis: ImAxis, v_min: float, v_max: float, n_ticks: int, labels: Optional[list[str]] = None, keep_default: bool = False) -> None:
            """Sets an axis' ticks and optionally the labels for the next plot. To keep the default ticks, set `keep_default`=True."""
            ...
        ''',
        pydef_code="bind_array_functions(LG_MODULE);",
    )


def imguizmo_options() -> litgen.LitgenOptions:
    """Options for ImGuizmo.h, whose functions become those of a submodule:
    ImGuizmo::Manipulate() -> imgui.imguizmo.manipulate(). The functions that take
    matrices are bound by hand (see src/bindings/imguizmo_bindings.cpp)."""
    options = litgen.LitgenOptions()
    options.use_nanobind()
    options.srcmlcpp_options.functions_api_prefixes = "IMGUI_API"
    options.srcmlcpp_options.code_preprocess_function = lambda code: code.replace("IMGUIZMO_NAMESPACE", "ImGuizmo")
    options.srcmlcpp_options.flag_show_progress = False

    options.namespaces_root = ["ImGuizmo"]
    options.python_convert_to_snake_case = True
    options.type_replacements = cpp_to_python.standard_type_replacements()
    options.type_replacements.add_last_replacement(r"ImGui([A-Z][a-zA-Z0-9]*)", r"\1")
    options.enum_make_arithmetic__regex = r".*"
    options.enum_make_flag__regex = r"^ImGuizmo::OPERATION$"  # combined with |: TRANSLATE | ROTATE

    # Matrices and 3D vectors are float pointers: see add_imguizmo_matrix_functions()
    options.fn_exclude_by_param_type__regex = r"float\s*\*"
    options.fn_exclude_by_name__regex = join(
        r"^SetID$",  # deprecated
        r"^SetAlternativeWindow$",  # takes an ImGuiWindow, which imgui.h does not define
        r"^SetImGuiContext$",  # for an ImGuizmo in another DLL than Dear ImGui: this extension holds both
    )
    options.fn_params_type_replacements.add_replacements([(r"\bImVec2\b", "ImVec2Like"), (r"\bImVec4\b", "ImVec4Like")])
    options.fn_return_force_policy_reference_for_references__regex = r".*"
    options.fn_force_overload__regex = join(r"^IsOver$", r"^PushID$", r"^GetID$")
    options.srcmlcpp_options.ignored_warning_parts += ["ImVec4 Colors[COLOR::COUNT]"]
    add_imguizmo_matrix_functions(options)
    add_style_colors(options, "ImGuizmo::Style", "ImGuizmo::COLOR::COUNT", "COLOR")

    options.cpp_indent_size = 4
    options.python_max_line_length = -1
    options.original_location_flag_show = False
    options.original_signature_flag_show = True
    options.python_run_black_formatter = True
    options.python_black_formatter_line_length = 120
    options.postprocess_stub_function = grouped_overloads
    return options


def add_imguizmo_matrix_functions(options: litgen.LitgenOptions) -> None:
    """The functions of ImGuizmo.h that take matrices, which bind_matrix_functions()
    in src/bindings/imguizmo_bindings.cpp binds by hand"""
    options.custom_bindings.add_custom_bindings_to_main_module(
        stub_code='''
        def manipulate(
            view: Matrix, projection: Matrix, operation: OPERATION, mode: MODE, matrix: Matrix,
            delta_matrix: Optional[Matrix] = None, snap: Optional[Vec3] = None,
            local_bounds: Optional[tuple[float, float, float, float, float, float]] = None,
            bounds_snap: Optional[Vec3] = None,
        ) -> bool:
            """Draws the gizmo for `matrix`, and applies what the user does with it to
            `matrix` (and `delta_matrix`, the change alone). Returns whether it changed.
            `snap`: the steps of the translation (x, y, z), or the rotation (degrees) or
            scale (both in snap[0]). `local_bounds`: (min x, y, z, max x, y, z) for BOUNDS."""
            ...
        @overload
        def view_manipulate(view: Matrix, length: float, position: ImVec2Like, size: ImVec2Like, background_color: ImU32) -> None:
            """Draws a cube that rotates the camera: `view` is updated"""
            ...
        @overload
        def view_manipulate(
            view: Matrix, projection: Matrix, operation: OPERATION, mode: MODE, matrix: Matrix, length: float,
            position: ImVec2Like, size: ImVec2Like, background_color: ImU32,
        ) -> None:
            """The same, for when manipulate() is not called"""
            ...
        def decompose_matrix_to_components(matrix: Matrix) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
            """The translation, rotation (Euler angles in degrees) and scale of `matrix`"""
            ...
        def recompose_matrix_from_components(translation: Vec3, rotation: Vec3, scale: Vec3, matrix: Matrix) -> None:
            """Writes the matrix of a translation, rotation (Euler angles in degrees) and scale into `matrix`"""
            ...
        def draw_axes(view: Matrix, projection: Matrix, matrices: np.ndarray) -> None:
            """Draws the coordinate axes of each of the (n, 4, 4) `matrices` (red X, green Y, blue Z)"""
            ...
        def draw_cubes(view: Matrix, projection: Matrix, matrices: np.ndarray) -> None:
            """Draws a cube for each of the (n, 4, 4) `matrices`, its faces colored by their normals"""
            ...
        def draw_grid(view: Matrix, projection: Matrix, matrix: Matrix, grid_size: float) -> None: ...
        def draw_grid_custom(view: Matrix, projection: Matrix, matrix: Matrix, grid_size: float, major_step: float, subdivision: int) -> None:
            """A grid with a major line every `major_step`, and `subdivision` minor lines in between"""
            ...
        def draw_grid_custom_color(
            view: Matrix, projection: Matrix, matrix: Matrix, grid_size: float, major_step: float, subdivision: int,
            major_col: ImU32, minor_col: ImU32, center_col: ImU32,
        ) -> None: ...
        @overload
        def is_over(position: Vec3, pixel_radius: float) -> bool:
            """Whether the mouse is within `pixel_radius` of the point `position`, projected by the last manipulate()"""
            ...
        def compute_mouse_ray(
            view: Matrix, projection: Matrix, mouse_position: ImVec2Like, rect_position: ImVec2Like, rect_size: ImVec2Like,
        ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
            """The origin and direction of the ray under `mouse_position`, in world space"""
            ...
        ''',
        pydef_code="bind_matrix_functions(LG_MODULE);",
    )


def generate(headers: dict[Path, litgen.LitgenOptions], bindings: str, stub: str) -> None:
    """Writes the bindings of the headers, each parsed with its own options, into
    src/bindings/`bindings` and src/imgui/`stub`"""
    generator = litgen.LitgenGenerator(next(iter(headers.values())))
    for header, options in headers.items():
        print(f"Processing {header.name}")
        generator.lg_context.options = options
        generator.process_cpp_file(str(header))
    generator.write_generated_code(str(BINDINGS / bindings), str(STUBS / stub))


def main() -> None:
    imgui = sources("ocornut/imgui", "IMGUI_GIT_TAG")
    generate({imgui / "imgui.h": imgui_options(), imgui / "misc/cpp/imgui_stdlib.h": imgui_options(stdlib=True)},
             "imgui_bindings.cpp", "__init__.pyi")
    generate({imgui / "backends/imgui_impl_sdl3.h": impl_sdl3_options()},
             "imgui_impl_sdl3_bindings.cpp", "impl_sdl3.pyi")
    generate({imgui / "backends/imgui_impl_sdlgpu3.h": impl_sdlgpu3_options()},
             "imgui_impl_sdlgpu3_bindings.cpp", "impl_sdlgpu3.pyi")
    generate({imgui / "backends/imgui_impl_opengl3.h": impl_opengl3_options()},
             "imgui_impl_opengl3_bindings.cpp", "impl_opengl3.pyi")
    implot = sources("epezent/implot", "IMPLOT_GIT_TAG")
    generate({implot / "implot.h": implot_options()}, "implot_bindings.cpp", "implot.pyi")
    imguizmo = sources("CedricGuillemet/ImGuizmo", "IMGUIZMO_GIT_TAG")
    generate({imguizmo / "src/ImGuizmo.h": imguizmo_options()}, "imguizmo_bindings.cpp", "imguizmo.pyi")


if __name__ == "__main__":
    main()
