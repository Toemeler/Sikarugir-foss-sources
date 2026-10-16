#!/usr/bin/env python3
"""Compile the iOS Oodle routing helper with controlled mapping/environment data.

Madeira's iOS loader keeps the first matching PE, so WINEDLLOVERRIDES=b alone
does not replace an app-local x64 DLL. Check the exact opt-in/name/machine gate
that lets the loader continue into the system32 bundle without rejecting the
renamed original used for fallback.
"""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/virtual_ios.c').read_text(encoding='utf-8')
start = source.index('static BOOL ios_oodle_native_shadow_refused(')
helper = source[start:source.index('\n}', start) + 2]
assert 'ios_oodle_native_shadow_refused( handle, &image_info )))' in source
assert source.index('/* outside the request block:') < source.index('ios_oodle_native_shadow_refused( handle, &image_info )))')

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
typedef int BOOL;
typedef uint16_t WCHAR, USHORT;
typedef uintptr_t HANDLE, mem_size_t;
typedef struct { USHORT Length; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { unsigned short Length; char *Buffer; } ANSI_STRING;
struct pe_image_info { unsigned machine; int is_hybrid, wine_builtin; };
#define TRUE 1
#define FALSE 0
#define IMAGE_FILE_MACHINE_AMD64 0x8664
#define SECTION_QUERY 1
static int ec = 1, status, missing_info, map_calls, closed, freed, logs, checks;
static const char *setting;
static WCHAR mapped_name[512];
static size_t mapped_length;
static int ios_is_arm64ec_cur(void) { return ec; }
static const char *test_getenv(const char *name) {
    if (strcmp(name, "MADEIRA_OODLE_NATIVE")) abort();
    return setting;
}
static int get_mapping_info(HANDLE handle, unsigned access, unsigned *flags,
                            mem_size_t *size, HANDLE *file,
                            struct pe_image_info **info, UNICODE_STRING *name,
                            ANSI_STRING *export_name) {
    (void)handle; (void)access; (void)flags; (void)size; (void)export_name;
    map_calls++;
    if (status) return status;
    *file = 0x1234;
    *info = missing_info ? NULL : (struct pe_image_info *)calloc(1, sizeof(**info));
    name->Buffer = mapped_name;
    name->Length = (USHORT)(mapped_length * sizeof(WCHAR));
    return 0;
}
static void NtClose(HANDLE handle) { if (handle != 0x1234) abort(); closed++; }
static void test_free(void *p) { if (p) freed++; free(p); }
static const char *debugstr_us(const UNICODE_STRING *name) { (void)name; return "test.dll"; }
static int test_log(int fd, const char *fmt, ...) { (void)fd; (void)fmt; logs++; return 0; }
#define getenv test_getenv
#define free test_free
#define dprintf test_log
''' + helper + r'''
#undef free
static void check(const char *name, const struct pe_image_info *pi, int expected,
                  int expected_mapping) {
    int before = map_calls, before_free = freed, before_closed = closed;
    mapped_length = strlen(name);
    for (size_t i = 0; i < mapped_length; i++) mapped_name[i] = (unsigned char)name[i];
    // Deliberately not terminated: only UNICODE_STRING.Length is authoritative.
    mapped_name[mapped_length] = 'x';
    BOOL result = ios_oodle_native_shadow_refused(0x5678, pi);
    if (result != expected || map_calls - before != expected_mapping) {
        fprintf(stderr, "routing failed name=%s env=%s machine=%x hybrid=%d builtin=%d result=%d map=%d\n",
                name, setting ? setting : "(unset)", pi->machine, pi->is_hybrid,
                pi->wine_builtin, result, map_calls - before);
        exit(1);
    }
    if (expected_mapping && !status) {
        if (closed - before_closed != 1 || freed - before_free != !missing_info) abort();
    } else if (closed != before_closed || freed != before_free) abort();
    checks++;
}
int main(void) {
    struct pe_image_info original = { IMAGE_FILE_MACHINE_AMD64, 0, 0 };
    struct pe_image_info hybrid = { IMAGE_FILE_MACHINE_AMD64, 1, 0 };
    struct pe_image_info builtin = { IMAGE_FILE_MACHINE_AMD64, 0, 1 };
    struct pe_image_info arm64ec = { 0xa641, 1, 1 };
    struct pe_image_info arm64 = { 0xaa64, 0, 0 };
    struct pe_image_info x86 = { 0x14c, 0, 0 };
    const char *path = "\\??\\C:\\ELDEN RING\\oo2core_6_win64.dll";
    const char *settings[] = { NULL, "", "0", "01", "10", "true", "1garbage", "1 " };
    for (size_t i = 0; i < sizeof(settings) / sizeof(settings[0]); i++) {
        setting = settings[i]; check(path, &original, 0, 0);
    }
    setting = "1";
    check(path, &original, 1, 1);
    check("C:/ELDEN RING/OO2CORE_6_WIN64.DLL", &original, 1, 1);
    check("oo2core_6_win64.dll", &original, 1, 1);
    check("C:\\windows\\temp\\madeira-oo2core6-234-123456.dll", &original, 0, 1);
    check("C:\\ELDEN RING\\other-oo2core_6_win64.dll", &original, 0, 1);
    check("C:\\ELDEN RING\\oo2core_6_win64.dll.backup", &original, 0, 1);
    check("C:\\ELDEN RING\\oo2core_6_win64.dl", &original, 0, 1);
    check("C:\\ELDEN RING\\dinput8.dll", &original, 0, 1);
    check("C:\\ELDEN RING\\ucrtbase.dll", &original, 0, 1);
    check("", &original, 0, 1);
    check(path, &hybrid, 0, 0);
    check(path, &builtin, 0, 0);
    check(path, &arm64ec, 0, 0);
    check(path, &arm64, 0, 0);
    check(path, &x86, 0, 0);
    ec = 0; check(path, &original, 0, 0); ec = 1;
    status = 1; check(path, &original, 0, 1); status = 0;
    missing_info = 1; check(path, &original, 0, 1); missing_info = 0;
    printf("PASS: %d production routing cases; exact opt-in, exact basename, native bundle and renamed original exempt\n", checks);
    return 0;
}
'''
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--compiler')
args = parser.parse_args()
compiler = args.compiler or os.environ.get('CC') or shutil.which('clang') or shutil.which('cc')
if not compiler:
    raise SystemExit('A C compiler is required; routing checks were not run')
with tempfile.TemporaryDirectory(prefix='madeira-oodle-routing-') as directory:
    directory = Path(directory)
    c = directory / 'routing.c'
    c.write_text(harness)
    exe = directory / ('routing.exe' if os.name == 'nt' else 'routing')
    subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
