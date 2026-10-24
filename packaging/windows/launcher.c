/* Jarvis.exe: starts the app with the Python runtime next to it, without a console window.

       Jarvis\Jarvis.exe             this program
       Jarvis\runtime\pythonw.exe    the embedded Python (packaging/build_windows.py)
       Jarvis\app\jarvis_gui.pyw     the app

   Its arguments are passed on (the start-up shortcut uses --minimized). Double-clicking Jarvis.exe inside the
   zip makes Explorer copy it alone into a temporary folder, and zip tools unpack the whole zip into one: either
   way it says how to extract the zip instead of starting from a folder that will disappear. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

#define PATH_CHARS 1024

static const wchar_t EXTRACT_FIRST[] =
    L"Jarvis is still inside the zip file, so it can't start from here.\n\n"
    L"1. Close this message.\n"
    L"2. Right-click the zip file and choose Extract All, then Extract.\n"
    L"3. Open the extracted Jarvis folder and double-click Jarvis.exe.";

static int exists(const wchar_t *path)
{
    return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES;
}

/* A folder under %TEMP%, or one Explorer made for a program run from a zip (...\Temp1_Jarvis-0.1.0.zip\...). */
static int in_temp_folder(const wchar_t *dir)
{
    wchar_t temp[PATH_CHARS], longtemp[PATH_CHARS], longdir[PATH_CHARS];
    DWORD n = GetTempPathW(PATH_CHARS, temp);
    if (wcsstr(dir, L"\\Temp1_") || wcsstr(dir, L".zip\\"))
        return 1;
    if (n == 0 || n >= PATH_CHARS)
        return 0;
    if (!GetLongPathNameW(temp, longtemp, PATH_CHARS))
        wcscpy_s(longtemp, PATH_CHARS, temp);
    if (!GetLongPathNameW(dir, longdir, PATH_CHARS))
        wcscpy_s(longdir, PATH_CHARS, dir);
    return _wcsnicmp(longdir, longtemp, wcslen(longtemp)) == 0;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show)
{
    wchar_t dir[PATH_CHARS], python[PATH_CHARS], script[PATH_CHARS], message[512];
    wchar_t *slash, *command;
    size_t length;
    STARTUPINFOW startup = {sizeof startup};
    PROCESS_INFORMATION process;
    DWORD n = GetModuleFileNameW(NULL, dir, PATH_CHARS);
    (void)instance; (void)previous; (void)show;

    if (n == 0 || n >= PATH_CHARS || (slash = wcsrchr(dir, L'\\')) == NULL)
        return 1;
    *slash = 0;
    swprintf_s(python, PATH_CHARS, L"%ls\\runtime\\pythonw.exe", dir);
    swprintf_s(script, PATH_CHARS, L"%ls\\app\\jarvis_gui.pyw", dir);
    if (in_temp_folder(dir) || !exists(python) || !exists(script)) {
        MessageBoxW(NULL, EXTRACT_FIRST, L"Please extract the zip first", MB_OK | MB_ICONINFORMATION);
        return 2;
    }

    length = wcslen(python) + wcslen(script) + wcslen(args) + 8;
    command = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, length * sizeof(wchar_t));
    if (command == NULL)
        return 1;
    swprintf_s(command, length, L"\"%ls\" \"%ls\" %ls", python, script, args);
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, 0, NULL, dir, &startup, &process)) {
        swprintf_s(message, 512, L"Jarvis couldn't start its Python runtime (Windows error %lu).\n\n"
                                 L"The folder may be incomplete. Extract the zip again.", GetLastError());
        MessageBoxW(NULL, message, L"Jarvis", MB_OK | MB_ICONERROR);
        return 3;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
