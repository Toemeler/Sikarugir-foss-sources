# Simulator harness

Runs guest Windows exes through Madeira's own launch path (WineServerBridge.m,
WineProcessBridge.m, the ntdll/wineserver/win32u unix sides, the shipped
`arm64ec-windows` PE set and the release's `xtajit64.dll`) inside an iOS
Simulator, headless, on GitHub Actions: `.github/workflows/sim-seh.yml`.

Push to `codex/sim-seh-20261022` and the workflow builds the unix side with
`MAD_SDK=iphonesimulator` (cached), links `madeira-sim`, boots an iPad Pro
13-inch (M4) on the newest iOS runtime the runner has, and runs every exe in
`EXES` (the harness job's `env`). Each run's whole session log, the guest's own
`*.log` files and the stub/rename lists are in the `harness-logs` artifact; the
tfstep verdict and DIFF lines are in the job summary.

| File | |
|---|---|
| `harness_main.m` | entry point: JIT pool, wineserver, one guest exe, log to stdout |
| `txm_shim.c` | refuses EXECUTE outside the JIT pool, as TXM does on a device |
| `harness_glue.c` | `fex_get_jit_write_offset` (FEXBridge.mm is not linked) |
| `build.sh` | compiles and links `out/MadeiraSim.app`, stubs and renames as needed |
| `boot.sh`, `run.sh` | create/boot the simulator; run one exe with `simctl spawn` |

To run another exe: build it into `app/Madeira/arm64ec-windows` (x64) before
the "Build the harness" step and add its name to `EXES`.
`workflow_dispatch` only works once this workflow is on the default branch.

## How it differs from a device

- **JIT pool**: an anonymous region made RX plus a `vm_remap`'d RW alias, in the
  early placeholder above `0x140000000` (where `StikJITHelper` puts it), instead
  of StikDebug's. Same `WINE_IOS_JIT_*` environment.
- **TXM**: the macOS kernel under the Simulator grants `mprotect(PROT_EXEC)`.
  Without `txm_shim.c`, `mprotect_exec` takes its non-TXM fast path and PE images
  run in place without their pool copy or the x18 patcher (`SIM_TXM=0` shows
  that: fib-x64 dies in ntdll init). The shim logs every refusal as `[sim-txm]`.
- **Low pages**: a device reads `[0, 0x2000)` as zero; the Simulator faults and
  the Mach handler emulates the x18-relative access (`[x18-emul3]`).
- **Linking**: what only the Swift layer or DXMT defines gets a logging stub
  (`out/stubs.txt`); globals both ntdll's unix side and the wineserver define
  are renamed to `ws_*` in the harness's copy of libwineserver.a
  (`out/renamed.txt`): `native_machine`, `supported_machines_count`,
  `server_start_time`.
- No UI, Metal, DXMT, audio output or Steam; x86 (WoW64) needs the i386 farm,
  which is not staged yet.

A result that matters should still get one confirming run on a real iPad.

## Experiments: plan.json

Each push runs `tests/sim-harness/plan.json`. Every entry of its `variants` is a
job of its own (its own simulator, all in parallel):

```json
{"name": "inject-1.9.8", "ersc": "1.9.8", "run": ["ersc-load-x64.exe inject"],
 "env": {"MADEIRA_DEBUG_VERBOSE": "1"}, "timeout": 240}
```

- `run`: guest command lines (exe in `arm64ec-windows`, then its arguments), run
  one after the other; `timeout`: seconds per line (default 240).
- `env`: exported to the harness process (`SIMCTL_CHILD_*`), so Madeira's own
  `MADEIRA_*`/`FEX_*` switches work.
- `cfg_preset`: a `GameRecommendations.swift` id (e.g. `elden-ring-ersc`); that
  game's config is written to the simulator's `Documents/madeira.cfg`, as the app
  applies it on a device (its `env.*` lines are exported, `local-write` etc. read).
  `cfg`: lines after it (later keys win), also usable alone.
- `pe_release`: a release tag (e.g. `build-76`); its `Madeira.ipa` supplies
  `pe_files` (default `ntdll.dll` and `xtajit64.dll`) instead of this checkout's,
  to run the PE side a device actually had (the unix side stays this checkout's).
- `ersc`: a key of `ersc_builds`; that ersc.dll is downloaded into the bundle
  (system32) and checked against its sha256. It is never committed here.

The `Digest` step of each job (`digest.py`) prints what matters: the result, the
guest's log, the `[guest-seh]` lines, an exception census (each SINGLE_STEP as
module+offset), the `[exec-req]` protection requests, and host faults with their
pc resolved to a function in `xtajit64.dll`/`ntdll.dll`. The job's
`harness-logs-<name>` artifact has the full logs and those two DLLs.

To change the emulator as well, put patches in `fex-patches/` (applied to FEX
after `build/fex-arm64ec/patches`; the fex-ec job rebuilds xtajit64.dll, with
ccache). For Wine's PE ntdll, `ntdll_builds` in plan.json names builds, e.g.
`"p3": {"patches": ["0001", "0002", "0003"]}` (the `build/wine-pe/patches` to keep;
no `patches` = all), each built by a pe-ntdll job with `wine-patches/` on top; a
variant picks one with `"ntdll": "p3"`. Otherwise the release's xtajit64.dll and
the checked-in ntdll.dll are used. Changes to the unix sides (`build/ntdll-unix` and friends)
rebuild the cached simulator libraries by themselves.

`ersc-load-x64 inject` is how ersc_launcher.exe loads ersc.dll on a device: it
starts `eldenring.exe` (a copy of the loader) suspended and loads ersc.dll into
it with a remote thread at LoadLibraryA.
