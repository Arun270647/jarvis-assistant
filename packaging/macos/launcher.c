/* Jarvis.app/Contents/MacOS/Jarvis: runs Contents/Resources/app/jarvis_gui.pyw on the bundled Python, inside this
 * process. Because the interpreter runs in the app's own executable (not a python started from it), macOS treats
 * Jarvis.app as the app: its Info.plist (the microphone and automation texts), its Dock icon, its permissions.
 *
 * Built by packaging/build_macos.py against Contents/Resources/runtime (python-build-standalone's CPython):
 *   cc -O2 -I runtime/include/python3.13 launcher.c -L runtime/lib -lpython3.13 -o Jarvis
 * then pointed at the bundled libpython with install_name_tool.
 *
 * The interpreter is isolated (no PYTHON* variables, no user site-packages), writes no .pyc files (the build
 * compiled them; writing into the app would break its signature), and reports the runtime's python3 as
 * sys.executable, so helpers such as pip run on the same Python.
 */
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void fail(const char *what) {
    fprintf(stderr, "Jarvis: %s\n", what);
    exit(1);
}

static void check(PyStatus status, PyConfig *config) {
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(config);
        Py_ExitStatusException(status);
    }
}

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX], contents[PATH_MAX], home[PATH_MAX], python[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL)
        fail("can't find its own folder");
    char *slash = strrchr(real, '/');                         /* .../Jarvis.app/Contents/MacOS/Jarvis */
    if (slash == NULL)
        fail("unexpected path");
    *slash = '\0';
    slash = strrchr(real, '/');
    if (slash == NULL)
        fail("unexpected path");
    *slash = '\0';                                            /* .../Jarvis.app/Contents */
    snprintf(contents, sizeof(contents), "%s", real);
    snprintf(home, sizeof(home), "%s/Resources/runtime", contents);
    snprintf(python, sizeof(python), "%s/bin/python3", home);
    snprintf(script, sizeof(script), "%s/Resources/app/jarvis_gui.pyw", contents);

    /* sys.argv: the script, then the arguments Jarvis was started with (old macOS adds -psn_..., dropped). */
    char **args = calloc((size_t)argc + 1, sizeof(char *));
    if (args == NULL)
        fail("out of memory");
    int count = 0;
    args[count++] = script;
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "-psn_", 5) != 0)
            args[count++] = argv[i];

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.write_bytecode = 0;
    config.site_import = 1;
    check(PyConfig_SetBytesString(&config, &config.home, home), &config);
    check(PyConfig_SetBytesString(&config, &config.executable, python), &config);
    check(PyConfig_SetBytesString(&config, &config.program_name, python), &config);
    check(PyConfig_SetBytesString(&config, &config.run_filename, script), &config);
    check(PyConfig_SetBytesArgv(&config, count, args), &config);
    check(Py_InitializeFromConfig(&config), &config);
    PyConfig_Clear(&config);
    free(args);
    return Py_RunMain();
}
