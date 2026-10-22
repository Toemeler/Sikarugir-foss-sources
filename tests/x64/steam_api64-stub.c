/* steam_api64-stub.c — a steam_api64.dll that only satisfies ersc.dll's import table for
 * ersc-load-x64 (ersc.dll imports SteamAPI_GetHSteamPipe; Themida resolves the rest at
 * run time). Every export reports "no Steam" (0 / NULL / false).
 *
 * Build: x86_64-w64-mingw32-clang -O2 -shared -o steam_api64.dll steam_api64-stub.c
 */

#include <windows.h>

#define ZERO(name) __declspec(dllexport) ULONG_PTR name(void) { return 0; }

ZERO(SteamAPI_GetHSteamPipe)
ZERO(SteamAPI_GetHSteamUser)
ZERO(SteamAPI_Init)
ZERO(SteamAPI_InitSafe)
ZERO(SteamAPI_InitFlat)
ZERO(SteamAPI_IsSteamRunning)
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
