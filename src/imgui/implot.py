# The module is compiled into _imgui; this file pairs implot.pyi with a source for type checkers
import sys

from ._imgui import implot

sys.modules[__name__] = implot
