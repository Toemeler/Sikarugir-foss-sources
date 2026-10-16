#!/usr/bin/env python3
"""Require the native Oodle builtin, or its identical copy in the final IPA."""
import argparse
import hashlib
from pathlib import Path
import struct
import zipfile

EXPORTS = {
    'OodleLZ_Compress', 'OodleLZ_Decompress',
    'OodleLZ_GetCompressedBufferSizeNeeded', 'OodleLZ_CompressOptions_GetDefault',
    'OodleLZ_GetCompressedStepForRawStep', 'OodleLZDecoder_MemorySizeNeeded',
}


def verify(data):
    def u16(offset):
        return struct.unpack_from('<H', data, offset)[0]

    def u32(offset):
        return struct.unpack_from('<I', data, offset)[0]

    assert data[:2] == b'MZ', 'not a PE file'
    pe = u32(0x3c)
    assert data[pe:pe + 4] == b'PE\0\0', 'missing PE signature'
    # ARM64EC images retain AMD64 in the on-disk COFF header for compatibility;
    # CHPE metadata describes native code and redirects the x64 entry thunks.
    assert u16(pe + 4) in (0x8664, 0xa641), 'unexpected PE machine'
    assert data[0x40:0x51] == b'Wine builtin DLL\0', 'missing Wine builtin marker'
    optional = pe + 24
    assert u16(optional) == 0x20b, 'expected PE32+'
    assert u16(pe + 22) & 0x2000, 'not a DLL'
    sections = optional + u16(pe + 20)

    def file_offset(rva):
        for i in range(u16(pe + 6)):
            section = sections + i * 40
            start, raw_size, raw = u32(section + 12), u32(section + 16), u32(section + 20)
            if start <= rva < start + raw_size:
                return raw + rva - start
        raise AssertionError('unmapped RVA %#x' % rva)

    def name(rva):
        start = file_offset(rva)
        return data[start:data.index(b'\0', start)].decode('ascii')

    load_rva, load_size = u32(optional + 192), u32(optional + 196)
    assert load_rva and load_size >= 208, 'missing ARM64EC load configuration'
    load = file_offset(load_rva)
    image_base = struct.unpack_from('<Q', data, optional + 24)[0]
    chpe_va = struct.unpack_from('<Q', data, load + 200)[0]
    assert chpe_va > image_base, 'missing ARM64EC metadata'
    metadata = file_offset(chpe_va - image_base)
    assert u32(metadata) == 1, 'unsupported ARM64EC metadata version'
    maps = file_offset(u32(metadata + 4))
    native_ranges = []
    for i in range(u32(metadata + 8)):
        encoded, length = u32(maps + i * 8), u32(maps + i * 8 + 4)
        if encoded & 3 == 1:  # ARM64EC (rather than the x64 import thunks).
            native_ranges.append((encoded & ~3, (encoded & ~3) + length))
    assert native_ranges, 'no ARM64EC code ranges'
    redirects = file_offset(u32(metadata + 16))
    native_targets = {}
    for i in range(u32(metadata + 52)):
        thunk, native = u32(redirects + i * 8), u32(redirects + i * 8 + 4)
        if any(start <= native < end for start, end in native_ranges):
            native_targets[thunk] = native

    export_rva, export_size = u32(optional + 112), u32(optional + 116)
    assert export_rva and export_size, 'missing exports'
    export = file_offset(export_rva)
    names = file_offset(u32(export + 32))
    actual = {name(u32(names + i * 4)) for i in range(u32(export + 24))}
    assert actual == EXPORTS, 'unexpected exports: %r' % sorted(actual)
    # Native entry points must be code, not forwarders to the emulated DLL.
    functions = file_offset(u32(export + 28))
    for i in range(u32(export + 20)):
        target = u32(functions + i * 4)
        assert target and not export_rva <= target < export_rva + export_size, 'forwarded or missing native entry point'
        assert target in native_targets, 'export does not redirect to ARM64EC code'
    imports = []
    import_rva = u32(optional + 120)
    if import_rva:
        item = file_offset(import_rva)
        while any(data[item:item + 20]):
            imported = name(u32(item + 12)).lower()
            assert not any(part in imported for part in ('oo2core', 'libstdc++', 'libc++', 'libgcc')), \
                'unexpected runtime dependency: ' + imported
            imports.append(imported)
            item += 20
    print('PASS: ARM64EC Wine builtin, six native exports; imports: ' + ', '.join(imports))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dll', nargs='?', type=Path)
    parser.add_argument('--ipa', type=Path)
    parser.add_argument('--source', type=Path, help='require the IPA DLL to equal this build output')
    args = parser.parse_args()
    if args.ipa:
        with zipfile.ZipFile(args.ipa) as ipa:
            paths = [p for p in ipa.namelist()
                     if p.startswith('Payload/') and p.endswith('.app/arm64ec-windows/oo2core_6_win64.dll')]
            assert len(paths) == 1, 'IPA must contain one native Oodle DLL'
            data = ipa.read(paths[0])
            notice = paths[0].rsplit('/', 1)[0] + '/oodle-notices.txt'
            assert b'GNU GENERAL PUBLIC LICENSE' in ipa.read(notice), 'IPA lacks the decoder license'
        if args.source:
            assert data == args.source.read_bytes(), 'packaged native Oodle DLL differs from build output'
    elif args.dll:
        data = args.dll.read_bytes()
    else:
        parser.error('provide a DLL path or --ipa')
    verify(data)
    print('SHA-256: ' + hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main()
