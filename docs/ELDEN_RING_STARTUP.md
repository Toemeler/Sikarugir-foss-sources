# Elden Ring startup on an M4 iPad (16 October 2026)

Two independent startup problems were observed with Madeira build 30, using
the direct `eldenring.exe` entry on an 8 GB M4 iPad Pro.

1. The game constructs a `Japanese` C++ locale before creating its window.
   Wine requested NLS code page 932, but the bundle contained no `c_932.nls`.
   The game threw `std::runtime_error("bad locale name")` and exited. Adding
   the missing table to the prefix's `windows/system32` fallback path let it
   create its D3D12 device and present frames. The bundled copy in `app/Madeira/nls`
   is the unmodified table from the pinned Wine submodule:
   `9129e9684daaeae70da9435e7c6060e59392634e`, `nls/c_932.nls` (162,850 bytes,
   SHA256 `dab57ece81509bb4d9f1d8df5c39e7b2ffaffda0a2d6e1028ddf822a04315d10`).
2. The resulting run played audio but showed a black screen. Its log said
   `D3D12SerializeRootSignature: 36 parameters exceeds the 32 this build handles`
   and `root parameter index 33 is beyond this build's limit`. The runtime's
   root-parameter arrays now accommodate 64 parameters. Existing bounds still
   reject oversized descriptions. This does not change the separate static
   sampler or descriptor-range bounds.

The extracted serializer/deserializer tests reproduce the 36-parameter failure
with the old limit and pass with the new limit. They also exercise 64 tables,
preserving index 33, reject counts above the new bound, and retain the existing
malformed-blob coverage. Local x64 Windows results: 8,928 checks, zero failures;
the opt-out path: two checks, zero failures. This is component validation;
successful rendered gameplay still requires an iPad test of the rebuilt runtime.

Release CI now rebuilds the ARM64EC D3D12 runtime and its Metal helper kernels,
so source fixes are not silently shipped with the older committed DLLs. When
DXMT's PE import library is absent, it is reconstructed from the bundled
`winemetal.dll` exports, using the pinned llvm-mingw toolchain.
