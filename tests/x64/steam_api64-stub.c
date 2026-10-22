/* steam_api64-stub.c — a steam_api64.dll that only satisfies ersc.dll's import table for
 * ersc-load-x64 (ersc.dll imports SteamAPI_GetHSteamPipe; Themida resolves the rest at
 * run time). Every export reports "no Steam" (0 / NULL / false), unless the environment
 * has STEAMSTUB_RUNNING=1: then the init and is-running calls return true and the pipe
 * and user handles are 1 (interfaces stay NULL).
 *
 * Build: x86_64-w64-mingw32-clang -O2 -shared -o steam_api64.dll steam_api64-stub.c
 */

#include <windows.h>

static int running(void)
{
    char v[4];
    DWORD n = GetEnvironmentVariableA("STEAMSTUB_RUNNING", v, sizeof(v));
    return n == 1 && v[0] == '1';
}

#define ZERO(name) __declspec(dllexport) ULONG_PTR name(void) { return 0; }
#define RUNNING(name) __declspec(dllexport) ULONG_PTR name(void) { return running(); }

RUNNING(SteamAPI_GetHSteamPipe)
RUNNING(SteamAPI_GetHSteamUser)
RUNNING(SteamAPI_Init)
RUNNING(SteamAPI_InitSafe)
ZERO(SteamAPI_InitFlat)   /* ESteamAPIInitResult: 0 = OK */
RUNNING(SteamAPI_IsSteamRunning)
ZERO(SteamAPI_RestartAppIfNecessary)
ZERO(SteamAPI_RunCallbacks)
ZERO(SteamAPI_Shutdown)
ZERO(SteamAPI_RegisterCallback)
ZERO(SteamAPI_UnregisterCallback)
ZERO(SteamAPI_RegisterCallResult)
ZERO(SteamAPI_UnregisterCallResult)
ZERO(SteamInternal_ContextInit)
ZERO(SteamInternal_CreateInterface)
ZERO(SteamInternal_FindOrCreateUserInterface)
ZERO(SteamInternal_FindOrCreateGameServerInterface)

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved)
{
    return TRUE;
}
