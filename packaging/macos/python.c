/* Jarvis.app/Contents/Resources/runtime/bin/python3.13: the runtime's python3 command (pip runs on it when Jarvis
 * installs an add-on), as a small program on the bundled libpython instead of the second 18 MB copy of the
 * interpreter that python-build-standalone ships. Python finds its standard library from this program's place
 * (bin/../lib/python3.13), as with the original.
 *
 * Built by packaging/build_macos.py: cc -I runtime/include/python3.13 python.c -L runtime/lib -lpython3.13, then
 * pointed at the bundled libpython with install_name_tool.
 */
#include <Python.h>

int main(int argc, char **argv) {
    return Py_BytesMain(argc, argv);
}
