/* Jarvis-Updater.exe: puts a downloaded update in place after Jarvis has quit, then starts Jarvis again.

       Jarvis-Updater.exe --install <Jarvis folder> --pid <Jarvis's process id> --result <file>

   Jarvis has already checked the update against its SHA-256 and unpacked it into <Jarvis folder>\.update\Jarvis
   (src/app/updates.py), and runs this program from a copy in the temporary folder, so nothing it replaces is in
   use. For each item of the new version (Jarvis.exe, runtime, app, ...), the current one moves to
   <Jarvis folder>\.old and the new one moves into place. These are renames on the same drive, so the swap is quick.
   If a step fails, everything moves back. The result ("updated", or "failed: ..." with the reason) goes to the
   result file for Jarvis to show; Jarvis deletes .old and .update the next time it starts. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <wchar.h>

#define PATH_CHARS 1024
#define MAX_ITEMS 64

static wchar_t install[PATH_CHARS], staged[PATH_CHARS], backup[PATH_CHARS], result_file[PATH_CHARS];
static wchar_t items[MAX_ITEMS][MAX_PATH];
static int old_moved[MAX_ITEMS], new_moved[MAX_ITEMS];

static int exists(const wchar_t *path)
{
    return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES;
}

static void result(const wchar_t *text)
{
    FILE *f;
    if (result_file[0] && _wfopen_s(&f, result_file, L"w, ccs=UTF-8") == 0) {
        fwprintf(f, L"%ls\n", text);
        fclose(f);
    }
}

/* Up to 10 seconds: antivirus scanners briefly hold files that were just written. */
static BOOL move(const wchar_t *from, const wchar_t *to)
{
    int attempt;
    for (attempt = 0; attempt < 50; attempt++) {
        if (MoveFileExW(from, to, MOVEFILE_WRITE_THROUGH))
            return TRUE;
        Sleep(200);
    }
    return FALSE;
}

/* Deletes a folder left by an earlier update (paths need a double NUL for SHFileOperation). */
static void delete_tree(const wchar_t *path)
{
    wchar_t from[PATH_CHARS + 2] = {0};
    SHFILEOPSTRUCTW op = {0};
    wcscpy_s(from, PATH_CHARS, path);
    op.wFunc = FO_DELETE;
    op.pFrom = from;
    op.fFlags = FOF_NO_UI;
    SHFileOperationW(&op);
}

static void join(wchar_t *out, const wchar_t *folder, const wchar_t *name)
{
    swprintf_s(out, PATH_CHARS, L"%ls\\%ls", folder, name);
}

static void relaunch(void)
{
    wchar_t exe[PATH_CHARS], command[PATH_CHARS + 2];
    STARTUPINFOW startup = {sizeof startup};
    PROCESS_INFORMATION process;
    join(exe, install, L"Jarvis.exe");
    swprintf_s(command, PATH_CHARS + 2, L"\"%ls\"", exe);
    if (CreateProcessW(exe, command, NULL, NULL, FALSE, 0, NULL, install, &startup, &process)) {
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
    }
}

static int collect_items(void)
{
    wchar_t pattern[PATH_CHARS];
    WIN32_FIND_DATAW found;
    HANDLE search;
    int count = 0;
    join(pattern, staged, L"*");
    search = FindFirstFileW(pattern, &found);
    if (search == INVALID_HANDLE_VALUE)
        return 0;
    do {
        if (wcscmp(found.cFileName, L".") && wcscmp(found.cFileName, L"..") && count < MAX_ITEMS)
            wcscpy_s(items[count++], MAX_PATH, found.cFileName);
    } while (FindNextFileW(search, &found));
    FindClose(search);
    return count;
}

/* Moves every item of the new version into place; on a failure, moves back what moved. */
static BOOL swap(int count, wchar_t *why)
{
    wchar_t current[PATH_CHARS], fresh[PATH_CHARS], kept[PATH_CHARS];
    int i, j;
    for (i = 0; i < count; i++) {
        join(current, install, items[i]);
        join(fresh, staged, items[i]);
        join(kept, backup, items[i]);
        if (exists(current)) {
            if (!move(current, kept))
                break;
            old_moved[i] = 1;
        }
        if (!move(fresh, current))
            break;
        new_moved[i] = 1;
    }
    if (i == count)
        return TRUE;
    swprintf_s(why, 512, L"failed: couldn't replace %ls (Windows error %lu). Nothing was changed.", items[i],
               GetLastError());
    for (j = i; j >= 0; j--) {
        join(current, install, items[j]);
        join(fresh, staged, items[j]);
        join(kept, backup, items[j]);
        if (new_moved[j] && !move(current, fresh))
            swprintf_s(why, 512, L"failed: couldn't replace %ls, and couldn't put %ls back. Extract the zip again.",
                       items[i], items[j]);
        if (old_moved[j] && !move(kept, current))
            swprintf_s(why, 512, L"failed: couldn't replace %ls, and couldn't put %ls back. Extract the zip again.",
                       items[i], items[j]);
    }
    return FALSE;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR cmdline, int show)
{
    int argc, i, count;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    wchar_t check[PATH_CHARS], why[512];
    DWORD pid = 0;
    HANDLE app;
    (void)instance; (void)previous; (void)cmdline; (void)show;

    for (i = 1; argv && i + 1 < argc; i += 2) {
        if (!wcscmp(argv[i], L"--install"))
            wcscpy_s(install, PATH_CHARS, argv[i + 1]);
        else if (!wcscmp(argv[i], L"--pid"))
            pid = (DWORD)_wtol(argv[i + 1]);
        else if (!wcscmp(argv[i], L"--result"))
            wcscpy_s(result_file, PATH_CHARS, argv[i + 1]);
    }
    if (!install[0] || !pid)
        return 1;
    join(staged, install, L".update\\Jarvis");
    join(backup, install, L".old");

    app = OpenProcess(SYNCHRONIZE, FALSE, pid);
    if (app) {
        DWORD waited = WaitForSingleObject(app, 120000);
        CloseHandle(app);
        if (waited == WAIT_TIMEOUT) {
            result(L"failed: Jarvis didn't quit, so the update wasn't installed.");
            return 2;
        }
    }
    Sleep(500);

    join(check, staged, L"runtime\\pythonw.exe");
    if (!exists(check) || (join(check, staged, L"app\\jarvis_gui.pyw"), !exists(check))
        || (join(check, staged, L"Jarvis.exe"), !exists(check))) {
        result(L"failed: the downloaded update is incomplete.");
        relaunch();
        return 3;
    }
    if (exists(backup))
        delete_tree(backup);
    if (!CreateDirectoryW(backup, NULL)) {
        swprintf_s(why, 512, L"failed: couldn't create %ls (Windows error %lu).", backup, GetLastError());
        result(why);
        relaunch();
        return 4;
    }
    count = collect_items();
    if (swap(count, why))
        result(L"updated");
    else
        result(why);
    relaunch();
    return 0;
}
