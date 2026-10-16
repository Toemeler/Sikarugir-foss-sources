# Elden Ring on Madeira (iPad Pro M4, 8 GB): phase 1 measurements

No behaviour was changed. Two runs on build 40, same game config
(fastsync, 4 reported CPUs, 30 FPS cap, 800x600 fill, pso-lazy/-libs,
swap-mode 2 / swap-min-mb 1, LowMemorySetup + boot_boost + stored-stream test):

- **cold**: 20:47-20:58, first run of build 40 (shader cache 556 misses / 444 hits), the other session's route
- **warm**: 22:32-22:42, second run, this route: title, Continue, stand, walk/run + camera, death + respawn, map, fast travel, stand

Frame data come from the 250 ms `[xp]` samples (presents per sample) and the 5 s
`[perf]` windows; per-frame 1%/0.1% lows arrive with build 41 (frame-time logging).
Thermal state stayed `nominal` in both runs.

## Times

| | cold (20:47) | warm (22:32) |
|---|---|---|
| Play to eldenring.exe spawned | 12.4 s | 12.9 s |
| Play to first frame | 24.7 s | 25.0 s |
| Play to title menu | ~145 s | ~163 s |
| Continue to playable | ~105 s | ~70 s |
| Death to respawned | - | ~60 s |
| Fast travel to steady 30 fps | - | ~110 s (30 s loading screen + 80 s streaming at 4-29 fps) |

## Frame rate per phase (warm run)

| Phase | s | avg fps | 1% of 250 ms samples | 5% | longest stall | main thread | busiest others |
|---|---|---|---|---|---|---|---|
| stand | 33 | 29.1 | 10.9 fps | 23.1 fps | - | 66% | GXWorker 44%, job workers ~40% |
| walk/run + camera | 50 | **18.1** | **0 fps** | 3.8 fps | 0.64 s | 66% | Decomp 27-65%, LoadProcess 22-76%, CSChrBackInit 18-49%, GXWorker 27% |
| respawn load | 60 | 6.7 | 0 | 0 | 12.5 s | 64% | |
| fast travel loading screen | 30 | 25.6 | 0 | 0 | 2.1 s | 25% | **Decomp 75-98%** |
| arrival streaming | 80 | 15.6 | 0 | 0 | 6.3 s | 68% | Decomp 60%, CSChrBackInit 25% |
| stand after arrival | 53 | 29.8 | 23.4 | 27.4 | - | 56% | GXWorker 32% |

The owner's overlay agrees: 33.3 ms standing, 100-250 ms frames (10-14 fps) running.

## Resources

| | cold | warm |
|---|---|---|
| CPU, whole process | 1.0-3.0 cores of 9, ~90% on P cores at 3.9 GHz | same |
| GPU busy (in the world) | 17-25% | 23-36% |
| phys_footprint peak | 4.4 GB | 3.6 GB |
| Metal allocated peak | 2.2 GB | 1.5 GB |
| swap file (file-backed) | 3.5 GB | 3.5-4.5 GB |
| JIT pool | 624 MB (896 asked, shrunk at launch) | same |
| FEX code buffer | 128 MB, no flush after start-up, 99% hit rate | same |
| pipelines built | 473 in 4.2 s (+1.3 s lazy at first draw) | 223 at load (1.6 s), 60 while walking (0.65 s + 0.58 s lazy) |
| wineserver requests | 33-48 k/s | 40-50 k/s |

## Top 5 causes

1. **Wineserver round trips on the main thread.** With fastsync the game's kernel
   mutexes, events and waits still go to the wineserver: 40-50 k requests/s, of
   which the main thread alone makes 14-20 k/s while moving (select ~5-9 k/s,
   release_mutex ~4-9 k/s, event_op, create_event + close_handle ~2 k/s each), i.e.
   ~1,000 round trips per frame at 15 fps. The wineserver thread itself uses
   10-14% of a core. While running, the main thread is only 62-74% busy at 10-18 fps:
   a third of each frame it waits.
2. **Streaming decompression on one thread.** The game reports 4 CPUs and creates
   one `CR2.Decomp_0` (Oodle). It runs 75-98% through every loading screen and
   27-65% while walking; loads take 60-110 s.
3. **Main-thread game code under emulation.** Standing at 30 fps already costs the
   main thread 55-73% of a core (~20 of 33 ms), so any extra streaming work pushes
   frames past 33 ms. The hottest guest function is a per-thread-data getter that
   calls `TlsGetValue` through kernel32 on every use.
4. **Pipelines built at first use.** Even warm, walking built 60 pipelines
   (0.65 s + 0.58 s rebuilt at first draw); the title built 5 pipelines in 3.6 s
   (Metal compile); cold, 473 pipelines cost 5.5 s. Single hitches of 100-700 ms.
5. **Start-up stalls.** Play to title 145-163 s: 25 s logo movies, 25-30 s archive
   header stall (`CR2.Device(HDD)` 100% while the main thread spins on a lock),
   15-20 s main-thread-only work, a further 15 s main stall after the title loads.
   Also once per cold run: GXWorker stuck 20 s on one store while recording a
   command list (madeira_d3d12 `mad_list_push`), no presents.

Not causes: GPU (17-36% busy), JIT pool / FEX code cache (no flushes),
thermals (nominal), memory limit (3.6-4.4 GB of 6.1 GB).

## Phase 2 results (builds 40/43, same route, game-config A/B)

| Test | Result | Kept |
|---|---|---|
| madsync (`inproc-sync = 1`) | crash in YAFSML.dll after 7 min; ~98 M x64->native calls/s (a spin) | no |
| `env.MADEIRA_SRV_DIRECT = 2` | walking 14 fps vs 18; no gain | no |
| `pso-warm = 2` + `env.MADEIRA_CPU_COUNT = 9` | 318/324 pipelines from Metal's cache (0.4 ms); standing 27-30 fps with the main thread ~40% (was ~60%) | **yes** (pso-warm in preset v8) |
| `env.FEX_TSOENABLED = 0` | endless loading screen | no |
| `env.FEX_EXTENDEDVOLATILEMETADATA = oo2core_6_win64.dll` (TSO off in Oodle only) | prepared, not run | untested |

Remaining cause (measured): moving into a new area stalls 5-20 s while the game waits on its own
streaming chain (one Oodle decompression thread at 50-98%, loading and character set-up threads), all
emulated; the CPU is 2-2.5 of 9 cores busy, the disk idle.

## Next step: a native Oodle decoder (in progress)

`madeira-oodle/src/ooz_compat.h` gives ooz's kraken.cpp (GPL-3.0-or-later, github.com/powzix/ooz)
the x86 intrinsics it uses with exact semantics on ARM64. Plan:

1. Vendor ooz `kraken.cpp` lines 1-4151 as `madeira-oodle/src/kraken.cpp` (include `ooz_compat.h`;
   stub LZNA/BitKnit to fail) plus a whole-buffer decode that accepts trailing input.
2. `oo2core_6_win64.dll` (ARM64EC, llvm-mingw like build/madeira-d3d12/build-pe.sh, "Wine builtin DLL"
   written at 0x40): `OodleLZ_Decompress` decodes natively into its own buffer (ooz writes up to 64
   bytes past the end) and copies; decBufBase != rawBuf, threadPhase != 3, a callback or any failure
   goes to the original, loaded from a temp copy of the game's DLL under another name. The other five
   imports (Compress, GetCompressedBufferSizeNeeded, CompressOptions_GetDefault,
   GetCompressedStepForRawStep, LZDecoder_MemorySizeNeeded) forward to that copy.
3. Game config: `env.WINEDLLOVERRIDES = dinput8=n,b;oo2core_6_win64=b` (dinput8 must stay).
4. Host check: build kraken.cpp natively on the macOS runner and decode `tests/host/data/oodle/*.oz`
   (52 streams made by the game's own DLL with `tests/host/gen-oodle-vectors.py`; manifest has sizes
   and SHA-256) before shipping.
