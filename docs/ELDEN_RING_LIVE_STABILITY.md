# Elden Ring live investigation, 2026-10-16

The observed device runs Madeira 0.1.3.35 inside LiveContainer on an iPad16,5,
iPadOS 27.0. The library entry starts `eldenring.exe` directly at 800x600,
four reported CPUs, fast synchronization on, a 30 FPS cap and frame generation
off. Its own file adds ring-share, lazy pipelines/libraries and a 1024 MB VRAM
hint. The existing texture, geometry, shader and pipeline-cache overrides stay
in place. This is an observed profile, not a new performance guarantee.

## Controller

The app reports a connected physical controller in XInput slot 0. The owner
reports no response from any physical button or stick with Game's own support.
Changing Session > Controller to Keyboard and mouse restores physical input,
confirmed by the owner. The saved entry contains `controllerMode: "keys"`.
This bypasses native XInput. Movement uses the keyboard template's WASD sectors,
the right stick becomes mouse motion and triggers become mouse buttons; it does
not establish native analogue input or rumble. The empty touch layout supplied
no virtual controller and must not be counted as a physical-pad test.

Preset version 7 makes that mode the recommendation for a new entry or an
explicit Reset to Recommended. Existing player settings remain untouched.
The inherited version 6 display/program recommendation is not applied to the
already configured direct-launch entry during this investigation.

## Performance and crash

The launch log measures `os_proc_available_memory=5506 MB` plus a 637 MB
physical footprint, giving the process an effective limit of 6144 MB (MiB units).
Physical footprint during the mixed session is approximately 4004-4493 MiB,
with a recorded session peak of 4659 MiB. The thermal state changes to serious
at 19:17:09 +02:00.

Standing can reach about 30 presentations/s. Movement and loading produce long
zero-present intervals; CPU samples repeatedly find `CR2.Decomp_0` executing
`oo2core_6_win64.dll` at roughly 70-80% of one core during stalls. Some intervals
also show code-buffer protection warnings. This points to a CPU/streaming
bottleneck but does not prove decompression is its only cause. The owner changed
graphics to Low during the session; a pre-existing High XML and a separate Low
XML were both present in the prefix, so file presence alone cannot prove the
running graphics settings.

At 19:29:03 +02:00 the app exits. The native trace faults in `objc_retain` and
command-buffer completion in `IOGPUMetalCommandBufferStorageReset`; its release
recorder identifies an `AGXG16GFamilyBuffer` freed by the bridge 39 releases
earlier. Last sampled footprint is 4350 MiB. This is a native Metal object
lifetime failure, with no evidence in this trace of a jetsam memory kill.

The investigated code records non-owning resource pointers for every root
descriptor, then declares the last 64 pointers resident on every draw and
dispatch. Overwritten bindings, inactive bind points and resources released
before replay remain in that list. The candidate correction resolves only the
active bind point's current root addresses at replay, declaring CBV/SRV reads
and UAV reads/writes. It removes the stale pointer history and the redundant
recording-time address lookups. No indefinite resource retention is added.

`tests/host/check-root-residency.py` compiles the real collector and exercises
VA reuse after free, overwritten/unmapped roots, graphics/compute separation,
all 64 parameters, descriptor kinds and merged usage. The isolated host checks
also compile the real recommendation/apply code with the Swift fixture.

These are candidate runtime changes until a freshly installed build completes
moving gameplay, combat, inventory, map and a 10-15 minute outdoor test. The
mixed menu/loading/live trace is not a valid sustained-gameplay FPS result.
