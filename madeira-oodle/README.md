# Native Oodle decoding

This optional ARM64EC Wine builtin replaces the six Oodle exports imported by
Elden Ring. `OodleLZ_Decompress` tries the native decoder for whole-buffer,
unthreaded calls without callbacks, external history or CRC verification.
Unsupported calls and rejected native results use the game's original DLL.
Compression and the other four helper functions always use the original DLL.

Enable it per game while preserving other overrides:

```ini
env.WINEDLLOVERRIDES = dinput8=n,b;oo2core_6_win64=b
env.MADEIRA_OODLE_NATIVE = 1
```

Madeira's iOS loader otherwise keeps the first app-local PE even with a builtin
override. The explicit native flag makes its section-query hook decline only
the original exact `oo2core_6_win64.dll` basename, so the loader continues to the
bundled ARM64EC file in system32. Renamed temp copies remain loadable.

`env.MADEIRA_OODLE_NATIVE = 0` disables that routing and makes the original
app-local DLL load normally. It also disables native decoding if the shim is
loaded explicitly by a full path. Remove both configuration lines to stop
using this feature entirely, preserving `dinput8=n,b` and any other existing
overrides. Changes apply after relaunching the game.

The shim finds the original `oo2core_6_win64.dll` beside the running executable,
copies it into a unique process-specific name in the Windows temp directory and
loads that full path. This avoids the builtin override resolving back to itself.
It does not replace or write any game-folder file. If copying or loading fails,
unsupported calls fail rather than loading a stale copy. A readable original
DLL is still required. No proprietary DLL belongs in the repository or IPA.

The native decoder receives private input and output allocations with 64 bytes
of tail padding. Only a result equal to the requested output length is copied
back to the caller. A native failure leaves the caller's buffers available for
the original decoder. This isolation does not establish that every malformed
stream is safe: decoder validation and sanitizer tests remain necessary.

Build with `build/madeira-oodle/build-pe.sh`. Decoder correctness checks live in
`tests/host/check-oodle-native.py`. The Windows runtime ABI and forwarding check
builds a synthetic original DLL and exercises the real shim:

```sh
python tests/host/check-oodle-shim.py --compiler /path/to/x86_64-w64-mingw32-clang++
```

Its optional `--original /private/path/oo2core_6_win64.dll` argument additionally
checks all bundled synthetic streams through the real original-DLL fallback.
The original is read only, copied exclusively into the test's temporary folder,
and removed after the subprocess exits. `MADEIRA_OODLE_ORIGINAL_PATH` is the shim's
test override for that path and should remain unset in normal use.

`tests/host/check-oodle-routing.py` compiles the production section-query helper
with controlled mapping data and checks exact activation, names, machines and
the renamed-original exemption.

Host checks prove byte equivalence and Windows ABI behavior for the tested cases.
They do not prove ARM64EC/Wine integration, device memory headroom, faster loading
or sustained frame rate. Those need an installed build and device measurements.
