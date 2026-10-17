#!/usr/bin/env python3
"""Games on an external drive (docs/EXTERNAL_DRIVE.md).

1. C, every host with a C compiler: build/ntdll-unix/virtual_ios.c's
   "file-map core" (between its markers) against small stand-ins for Wine's
   NTSTATUS values:
   - after mmap() refused a read-only or copy-on-write view, every errno reads
     instead (upstream failed ENOTSUP, EIO, ENXIO... with STATUS_NO_MEMORY);
     a shared writable view keeps upstream's statuses;
   - the read copy is read in pieces (the chunk is made small here), resumes
     at the right offsets, stops at end of file leaving the rest as it was,
     and reports a read error instead of ignoring it;
   - a file is "external" when it is on another volume than config_dir, and
     only then does a failed read fail the mapping;
   - each kind of event is logged once per errno.
2. C: app/Madeira/WineProcessBridge.m's madeira_dos_to_unix (C: exactly as the
   launch always mapped it, other letters through dosdevices) on every host,
   and its external probe (between its markers) on macOS: a real file read
   and mapped privately and shared, a mapping that faults (a file truncated
   under its mapping) reported as fault(SIGBUS) without crashing and with
   the previous handler restored, and the failure lines.
3. Swift (macOS; --require-swift turns a missing compiler into a failure):
   the real ExternalDrive.swift with stand-ins for MadeiraConfig, LogStore
   and the probe: the letter, path and link rules; a real security-scoped-
   style bookmark of a temporary folder resolved, followed when the folder
   is renamed (as a drive mounted again elsewhere), and reported as not
   connected once it is gone and as stale when the data is damaged; the
   dosdevices link made, kept, moved to another letter and removed, a
   non-link left alone, a session's loss reported once.
4. Source checks of the wiring: the library entry's drive, the launch's
   preflight, the link before the wineserver, the session poll, Settings,
   the Xcode project, the bridge's D: paths and the mapping call sites.
Device runs are still required: this proves the rules, not iPadOS's volumes.
"""
from pathlib import Path
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
args = parser.parse_args()

root = Path(__file__).resolve().parents[2]
virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text(encoding='utf-8')
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text(encoding='utf-8')
bridge_h = (root / 'app/Madeira/WineProcessBridge.h').read_text(encoding='utf-8')
drive_swift = (root / 'app/Madeira/ExternalDrive.swift').read_text(encoding='utf-8')
library = (root / 'app/Madeira/Library.swift').read_text(encoding='utf-8')
content = (root / 'app/Madeira/ContentView.swift').read_text(encoding='utf-8')
project = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text(encoding='utf-8')
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text(encoding='utf-8')
generator = (root / 'build/tools/gen-config-catalog.py').read_text(encoding='utf-8')
workflow = (root / '.github/workflows/host-stability.yml').read_text(encoding='utf-8')

failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        failures.append(what)


def between(src, begin, end):
    i = src.index(begin)
    return src[i:src.index(end, i)]


def body_of(src, signature):
    start = src.index(signature)
    brace = src.index('{', start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError('unterminated body: ' + signature)


cc = shutil.which(os.environ.get('CC', 'cc')) or shutil.which('clang') or shutil.which('gcc')
work = Path(tempfile.mkdtemp(prefix='madeira-external-'))


def run_c(name, source, extra=()):
    src = work / (name + '.c')
    exe = work / name
    src.write_text(source, encoding='utf-8')
    built = subprocess.run([cc, '-std=gnu11', '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(src), *extra],
                           capture_output=True, text=True)
    if built.returncode:
        print(built.stderr[-4000:])
        return None
    ran = subprocess.run([str(exe), str(work)], capture_output=True, text=True, timeout=120)
    print(ran.stdout, end='')
    if ran.returncode:
        print(ran.stderr[-4000:])
    return ran


# ---------------------------------------------------------------- 1. file-map core
core = between(virt, '/* file-map core begin', '/* file-map core end */')
fmap_test = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/stat.h>
typedef unsigned int NTSTATUS;
#define STATUS_SUCCESS            0x00000000u
#define STATUS_INVALID_PARAMETER  0xC000000Du
#define STATUS_NOT_SUPPORTED      0xC00000BBu
#define STATUS_ACCESS_DENIED      0xC0000022u
#define STATUS_NO_MEMORY          0xC0000017u
#define IOS_FMAP_READ_CHUNK ((size_t)4096)   /* small, so a few KB cross several pieces */
static const char *config_dir;
''' + core + r'''
static int failed;
#define EXPECT(c, what) do { if (c) printf("ok   %s\n", what); else { printf("FAIL %s\n", what); failed++; } } while (0)

int main(int argc, char **argv)
{
    char path[4096], logpath[4096], line[1024];
    static unsigned char data[3 * 4096 + 123], buf[3 * 4096 + 2000];
    NTSTATUS st;
    size_t i;
    int fd, devnull, saved, logfd, lines = 0;
    FILE *log;

    /* after mmap() refused */
    {
        int errs[] = { EINVAL, ENOEXEC, ENODEV, EACCES, EPERM, ENOTSUP, EIO, ENXIO, ENOMEM, EAGAIN };
        int all_read = 1;
        for (i = 0; i < sizeof(errs) / sizeof(errs[0]); i++)
        {
            st = 0x1234;
            if (ios_fmap_after_mmap_error( errs[i], 0, &st ) != IOS_FMAP_READ || st != 0x1234) all_read = 0;
        }
        EXPECT(all_read, "a read-only or copy-on-write view reads after any refusal (ENOTSUP, EIO, ENXIO, ENOMEM included)");
    }
    st = 0; EXPECT(ios_fmap_after_mmap_error( EINVAL, 1, &st ) == IOS_FMAP_FAIL && st == STATUS_INVALID_PARAMETER,
                   "shared writable, EINVAL: the unaligned shared mapping status");
    st = 0; EXPECT(ios_fmap_after_mmap_error( ENODEV, 1, &st ) == IOS_FMAP_FAIL && st == STATUS_NOT_SUPPORTED &&
                   ios_fmap_after_mmap_error( ENOEXEC, 1, &st ) == IOS_FMAP_FAIL && st == STATUS_NOT_SUPPORTED,
                   "shared writable, ENODEV/ENOEXEC: STATUS_NOT_SUPPORTED");
    st = 0; EXPECT(ios_fmap_after_mmap_error( EACCES, 1, &st ) == IOS_FMAP_FAIL && st == STATUS_ACCESS_DENIED &&
                   ios_fmap_after_mmap_error( EPERM, 1, &st ) == IOS_FMAP_FAIL && st == STATUS_ACCESS_DENIED,
                   "shared writable, EACCES/EPERM: STATUS_ACCESS_DENIED");
    st = 0; EXPECT(ios_fmap_after_mmap_error( ENOTSUP, 1, &st ) == IOS_FMAP_FAIL && st == STATUS_NO_MEMORY,
                   "shared writable, anything else: STATUS_NO_MEMORY, as upstream");

    /* the read copy */
    snprintf(path, sizeof(path), "%s/data.bin", argv[1]);
    for (i = 0; i < sizeof(data); i++) data[i] = (unsigned char)(i * 7 + 3);
    fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    EXPECT(fd >= 0 && write(fd, data, sizeof(data)) == (ssize_t)sizeof(data), "test file written");
    memset(buf, 0xAA, sizeof(buf));
    EXPECT(ios_fmap_read( fd, buf, sizeof(buf), 0 ) == (ssize_t)sizeof(data), "a read past the end returns the file's size");
    EXPECT(!memcmp(buf, data, sizeof(data)), "every piece lands at its offset (4 KB pieces)");
    {
        int tail = 1;
        for (i = sizeof(data); i < sizeof(buf); i++) if (buf[i] != 0xAA) tail = 0;
        EXPECT(tail, "past the end the memory is left as it was");
    }
    memset(buf, 0, sizeof(buf));
    EXPECT(ios_fmap_read( fd, buf, 5000, 4000 ) == 5000 && !memcmp(buf, data + 4000, 5000), "a read at an offset");
    EXPECT(ios_fmap_read( fd, buf, 0, 0 ) == 0, "an empty read");
    {
        int dead = dup(fd);
        close(dead);
        errno = 0;
        EXPECT(ios_fmap_read( dead, buf, 100, 0 ) == -1 && errno == EBADF, "a read that fails is reported (-1, errno kept)");
    }

    /* which volume */
    config_dir = argv[1];
    devnull = open("/dev/null", O_RDONLY);
    EXPECT(ios_fmap_external( fd ) == 0, "a file next to the prefix is on the prefix's volume");
    EXPECT(ios_fmap_external( devnull ) == 1, "a file on another file system (/dev) is external");
    EXPECT(ios_fmap_read_fails( 1 ) && !ios_fmap_read_fails( 0 ) && !ios_fmap_read_fails( -1 ),
           "only a failed read on another volume fails the mapping");

    /* logged once per kind and errno */
    snprintf(logpath, sizeof(logpath), "%s/stderr.txt", argv[1]);
    fflush(stderr);
    saved = dup(2);
    logfd = open(logpath, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    dup2(logfd, 2);
    ios_fmap_note( IOS_FMAP_MMAP_REFUSED, ENOTSUP, fd, "an image section", 0x4000 );
    ios_fmap_note( IOS_FMAP_MMAP_REFUSED, ENOTSUP, fd, "an image section", 0x8000 );
    ios_fmap_note( IOS_FMAP_MMAP_REFUSED, EIO, devnull, "a file view", 0x10000 );
    ios_fmap_note( IOS_FMAP_READ_FAILED, EIO, devnull, "a file view", 0x10000 );
    ios_fmap_note( IOS_FMAP_READ_FAILED, EIO, devnull, "a file view", 0x10000 );
    ios_fmap_note( IOS_FMAP_READ_FAILED, EBADF, fd, "an image header", 0x400 );
    fflush(stderr);
    dup2(saved, 2);
    close(logfd);
    log = fopen(logpath, "r");
    {
        int prefix_line = 0, external_line = 0, fails_line = 0, kept_line = 0;
        while (log && fgets(line, sizeof(line), log))
        {
            lines++;
            if (strstr(line, "[file-map] mmap refused for an image section") && strstr(line, "the prefix's volume")) prefix_line++;
            if (strstr(line, "mmap refused for a file view") && strstr(line, "another volume (an external drive)")) external_line++;
            if (strstr(line, "read failed") && strstr(line, "the mapping fails")) fails_line++;
            if (strstr(line, "read failed for an image header") && strstr(line, "kept as read, as before")) kept_line++;
        }
        if (log) fclose(log);
        EXPECT(lines == 4, "four lines: each kind and errno once");
        EXPECT(prefix_line == 1 && external_line == 1 && fails_line == 1 && kept_line == 1,
               "each line names the volume and what happens next");
    }
    for (i = 0; i < 40; i++) ios_fmap_note( IOS_FMAP_MMAP_REFUSED, 1000 + (int)i, fd, "a file view", 1 );   /* to /dev/stderr: bounded */
    close(fd); close(devnull);
    printf(failed ? "FAILED file-map core\n" : "PASS file-map core\n");
    return failed != 0;
}
'''

# ---------------------------------------------------------------- 2. bridge: paths and probe
dos_to_unix = body_of(bridge, 'static int madeira_dos_to_unix(const char *dos, const char *prefix, char *out, size_t outlen)\n{')
path_test = r'''
#include <stdio.h>
#include <string.h>
#include <stddef.h>
''' + dos_to_unix + r'''
static int failed;
#define EXPECT(c, what) do { if (c) printf("ok   %s\n", what); else { printf("FAIL %s\n", what); failed++; } } while (0)
int main(void)
{
    char out[256], tiny[24];
    const char *p = "/var/mobile/Containers/Data/Application/X/Documents/wine";
    EXPECT(!madeira_dos_to_unix("C:\\Games\\Some Game\\game.exe", p, out, sizeof(out)) &&
           !strcmp(out, "/var/mobile/Containers/Data/Application/X/Documents/wine/drive_c/Games/Some Game/game.exe"),
           "C: maps onto drive_c, exactly as the launch always did");
    EXPECT(!madeira_dos_to_unix("c:\\x", p, out, sizeof(out)) && strstr(out, "/drive_c/x"), "c: in lower case too");
    EXPECT(!madeira_dos_to_unix("D:\\ELDEN RING\\eldenring.exe", p, out, sizeof(out)) &&
           !strcmp(out, "/var/mobile/Containers/Data/Application/X/Documents/wine/dosdevices/d:/ELDEN RING/eldenring.exe"),
           "D: goes through dosdevices/d:, the link the app makes for the external folder");
    EXPECT(!madeira_dos_to_unix("E:\\", p, out, sizeof(out)) && !strcmp(out + strlen(p), "/dosdevices/e:/"), "a drive's root");
    EXPECT(madeira_dos_to_unix("\\\\server\\share", p, out, sizeof(out)) == -1 &&
           madeira_dos_to_unix("D:relative", p, out, sizeof(out)) == -1 &&
           madeira_dos_to_unix("game.exe", p, out, sizeof(out)) == -1 &&
           madeira_dos_to_unix("1:\\x", p, out, sizeof(out)) == -1 &&
           madeira_dos_to_unix(NULL, p, out, sizeof(out)) == -1, "other forms are refused");
    EXPECT(madeira_dos_to_unix("D:\\a long folder name\\x.exe", "/p", tiny, sizeof(tiny)) == -1, "a path that does not fit is refused");
    printf(failed ? "FAILED drive paths\n" : "PASS drive paths\n");
    return failed != 0;
}
'''

probe = between(bridge, '/* external-probe begin', '/* external-probe end */')
probe_test = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <setjmp.h>
#include <pthread.h>
#include <sys/stat.h>
''' + probe + r'''
static int failed;
#define EXPECT(c, what) do { if (c) printf("ok   %s\n", what); else { printf("FAIL %s\n", what); failed++; } } while (0)
int main(int argc, char **argv)
{
    char file[4096], gone[4096], out[1024], how[64];
    static unsigned char data[3 * 16384 + 77];
    struct sigaction now;
    size_t i;
    int fd;

    snprintf(file, sizeof(file), "%s/game.exe", argv[1]);
    for (i = 0; i < sizeof(data); i++) data[i] = (unsigned char)(i ^ (i >> 8));
    fd = open(file, O_RDWR | O_CREAT | O_TRUNC, 0600);
    EXPECT(fd >= 0 && write(fd, data, sizeof(data)) == (ssize_t)sizeof(data), "test program written");
    close(fd);

    madeira_external_probe_ex(argv[1], NULL, 1, out, sizeof(out));
    printf("     %s\n", out);
    EXPECT(!strncmp(out, "fs=", 3) && strstr(out, " free=") && !strstr(out, "program"), "the volume alone without a program");
    madeira_external_probe_ex(argv[1], file, 1, out, sizeof(out));
    printf("     %s\n", out);
    EXPECT(strstr(out, " read=0.0MB in ") && strstr(out, " mmap-private=ok mmap-shared=ok"),
           "the program read, then mapped privately and shared with the same bytes");
    madeira_external_probe_ex(argv[1], file, 0, out, sizeof(out));
    EXPECT(strstr(out, " mmap-private=mapped mmap-shared=mapped") != NULL, "after Wine ran: mapped, not touched");
    snprintf(gone, sizeof(gone), "%s/missing.exe", argv[1]);
    madeira_external_probe_ex(argv[1], gone, 1, out, sizeof(out));
    EXPECT(strstr(out, " program: open failed (") != NULL, "a missing program is reported");
    madeira_external_probe_ex("/no/such/volume", NULL, 1, out, sizeof(out));
    EXPECT(!strncmp(out, "statfs failed (", 15), "a missing folder is reported");
    madeira_external_probe_ex(argv[1], file, 1, out, 12);
    EXPECT(strlen(out) == 11, "the line is cut at the buffer, NUL-terminated");

    /* A mapping whose page cannot be read: the file is truncated under it. */
    fd = open(file, O_RDWR);
    EXPECT(ftruncate(fd, 0) == 0, "test program truncated");
    madeira_probe_map(fd, 16384, MAP_SHARED, 1, data, how, sizeof(how));
    printf("     unbacked page: %s\n", how);
    EXPECT(!strcmp(how, "fault(SIGBUS)") || !strcmp(how, "fault(SIGSEGV)"), "a page-in fault is reported, not fatal");
    sigaction(SIGBUS, NULL, &now);
    EXPECT(now.sa_handler == SIG_DFL, "the previous SIGBUS handler is back");
    madeira_probe_map(fd, 16384, MAP_PRIVATE, 1, data, how, sizeof(how));
    EXPECT(!strncmp(how, "fault(", 6), "the same for a private mapping");
    close(fd);
    madeira_probe_map(-1, 16384, MAP_PRIVATE, 1, data, how, sizeof(how));
    EXPECT(!strncmp(how, "refused(", 8), "a refused mapping names its errno");
    printf(failed ? "FAILED external probe\n" : "PASS external probe\n");
    return failed != 0;
}
'''

if cc:
    for name, source in (('fmap', fmap_test), ('paths', path_test)):
        ran = run_c(name, source)
        check(ran is not None and ran.returncode == 0, name + ': C checks')
    if sys.platform == 'darwin':
        ran = run_c('probe', probe_test)
        check(ran is not None and ran.returncode == 0, 'probe: C checks (macOS)')
    else:
        print('note: the external probe needs BSD statfs; it runs on the macOS job')
else:
    check(False, 'a host C compiler (cc, clang or gcc)')

# ---------------------------------------------------------------- 3. Swift
swift_main = r'''
import Foundation
var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "ok   " : "FAIL ") + what); if !cond { failed += 1 } }

typealias R = ExternalDriveRules
expect(R.letter(nil) == "d" && R.letter("") == "d" && R.letter("E") == "e" && R.letter("f:") == "f" && R.letter("G:\\") == "g",
       "letters: D: by default, case and a trailing colon or backslash ignored")
expect(R.letter("c") == "d" && R.letter("z") == "d" && R.letter("dd") == "d" && R.letter("1") == "d" && R.letter("y") == "y",
       "C:, Z: and anything else are D:")
expect(R.windowsPath(letter: "d", relative: "ELDEN RING/eldenring.exe") == "D:\\ELDEN RING\\eldenring.exe", "the Windows path")
expect(R.relativePath(of: "/v/SSD/ELDEN RING/eldenring.exe", under: "/v/SSD") == "ELDEN RING/eldenring.exe" &&
       R.relativePath(of: "/v/SSD/x.exe", under: "/v/SSD/") == "x.exe", "a path inside the folder")
expect(R.relativePath(of: "/v/SSD", under: "/v/SSD") == nil && R.relativePath(of: "/v/SSD2/x.exe", under: "/v/SSD") == nil &&
       R.relativePath(of: "/v/SSD/../x.exe", under: "/v/SSD") == nil && R.relativePath(of: "/x", under: "/") == nil &&
       R.relativePath(of: "/v/SSD//x", under: "/v/SSD") == nil, "not inside: the folder itself, a sibling, '..', the root, empty parts")
expect(R.hostPath(ofWindowsPath: "D:\\ELDEN RING\\eldenring.exe", letter: "d", root: "/v/SSD/") == "/v/SSD/ELDEN RING/eldenring.exe" &&
       R.hostPath(ofWindowsPath: " d:\\Game ", letter: "d", root: "/v/SSD") == "/v/SSD/Game" &&
       R.hostPath(ofWindowsPath: "D:\\", letter: "d", root: "/v/SSD") == "/v/SSD", "D:\\ paths on the host")
expect(R.hostPath(ofWindowsPath: "C:\\x", letter: "d", root: "/v") == nil && R.hostPath(ofWindowsPath: "D:\\..\\x", letter: "d", root: "/v") == nil &&
       R.hostPath(ofWindowsPath: "D:", letter: "d", root: "/v") == nil, "another drive, '..' or no root")
expect(R.linkPlan(.missing, target: nil) == .keep && R.linkPlan(.missing, target: "/a") == .make &&
       R.linkPlan(.link("/a"), target: "/a") == .keep && R.linkPlan(.link("/a"), target: "/b") == .replace &&
       R.linkPlan(.link("/a"), target: nil) == .remove && R.linkPlan(.other, target: "/a") == .leave &&
       R.linkPlan(.other, target: nil) == .leave, "link plans: only a link is replaced or removed")
expect(R.isExternalLink(name: "e:", target: "/v/SSD") && !R.isExternalLink(name: "c:", target: "../drive_c") &&
       !R.isExternalLink(name: "c:", target: "/x") && !R.isExternalLink(name: "z:", target: "/") &&
       !R.isExternalLink(name: "d::", target: "/dev/disk2") && !R.isExternalLink(name: "e:", target: "relative"),
       "external links: D: to Y:, absolute; never c:, z: or a device link")
let enoent = NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoSuchFileError,
                     userInfo: [NSUnderlyingErrorKey: NSError(domain: NSPOSIXErrorDomain, code: Int(ENOENT))])
expect(R.failure(for: enoent, name: "G") == .notConnected("G") &&
       R.failure(for: NSError(domain: NSPOSIXErrorDomain, code: Int(ENXIO)), name: "G") == .notConnected("G") &&
       R.failure(for: NSError(domain: NSPOSIXErrorDomain, code: Int(EIO)), name: "G") == .notConnected("G") &&
       R.failure(for: NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError), name: "G") == .unreadable("G") &&
       R.failure(for: NSError(domain: NSCocoaErrorDomain, code: NSFileReadCorruptFileError), name: "G") == .stale("G"),
       "errors: missing is not connected, permission is unreadable, anything else stale")
expect(R.message(.notConnected("Games"), letter: "d").contains("“Games” (drive D:) is not connected") &&
       R.message(.noFolder, letter: "e").contains("Settings › External drive") &&
       R.message(.stale("Games"), letter: "d").contains("Choose the folder again") &&
       R.disconnectedMessage(letter: "d").contains("(D:) was disconnected"), "messages name the folder, the letter and what to do")

let fm = FileManager.default
let base = URL(fileURLWithPath: CommandLine.arguments[1]).resolvingSymlinksInPath()
let games = base.appendingPathComponent("SSD/Games", isDirectory: true)
try! fm.createDirectory(at: games.appendingPathComponent("ELDEN RING"), withIntermediateDirectories: true)
try! Data("MZ".utf8).write(to: games.appendingPathComponent("ELDEN RING/eldenring.exe"))
let store = ExternalDriveStore(directory: base.appendingPathComponent("support/ExternalDrive"))
if case .failed(.noFolder) = store.resolve() { expect(true, "no folder chosen: noFolder") } else { expect(false, "no folder chosen: noFolder") }
try! store.save(try! ExternalDriveStore.record(for: games))
let saved = store.load()
expect(saved?.name == "Games" && saved?.path == games.path && !(saved?.bookmark.isEmpty ?? true), "the record keeps a full bookmark, the name and the path")
var started: [URL] = [], stopped: [URL] = []
let start: (URL) -> Bool = { started.append($0); return true }
let stop: (URL) -> Void = { stopped.append($0) }
if case .ready(let url, let scoped, _) = store.resolve(startAccess: start, stopAccess: stop) {
    expect(url.resolvingSymlinksInPath().path == games.path && scoped && started.count == 1 && stopped.isEmpty,
           "the bookmark resolves to the folder and its access is started and kept")
} else { expect(false, "the bookmark resolves") }
// The drive mounted again under another name: the bookmark follows the folder.
let moved = base.appendingPathComponent("SSD2", isDirectory: true)
try! fm.moveItem(at: base.appendingPathComponent("SSD"), to: moved)
if case .ready(let url, _, _) = store.resolve(startAccess: start, stopAccess: stop) {
    expect(url.resolvingSymlinksInPath().path == moved.appendingPathComponent("Games").path &&
           store.load()?.path == url.path, "a moved folder is followed and its new place recorded")
} else { expect(false, "a moved folder is followed") }
// Unplugged: gone.
let away = base.appendingPathComponent("away", isDirectory: true)
try! fm.moveItem(at: moved, to: away)
try! fm.removeItem(at: away)
started = []; stopped = []
switch store.resolve(startAccess: start, stopAccess: stop) {
case .failed(.notConnected("Games")): expect(started.count == stopped.count, "a folder that is gone: not connected, no access left started")
default: expect(false, "a folder that is gone: not connected")
}
var damaged = store.load()!
damaged.bookmark = Data("not a bookmark".utf8)
try! store.save(damaged)
if case .failed(.stale("Games")) = store.resolve() { expect(true, "damaged bookmark data: stale") } else { expect(false, "damaged bookmark data: stale") }

// Sessions: the dosdevices link.
let folder = base.appendingPathComponent("Drive/Games", isDirectory: true)
try! fm.createDirectory(at: folder.appendingPathComponent("ELDEN RING"), withIntermediateDirectories: true)
let prefix = base.appendingPathComponent("wine").path
let dosdevices = prefix + "/dosdevices"
try! fm.createDirectory(atPath: dosdevices, withIntermediateDirectories: true)
try! fm.createSymbolicLink(atPath: dosdevices + "/c:", withDestinationPath: "../drive_c")
try! fm.createSymbolicLink(atPath: dosdevices + "/e:", withDestinationPath: "/old/drive")
let drive = ExternalDrive()
drive.store = ExternalDriveStore(directory: base.appendingPathComponent("support2"))
drive.link(prefix: prefix, program: nil)
expect((try? fm.destinationOfSymbolicLink(atPath: dosdevices + "/c:")) == "../drive_c" &&
       (try? fm.destinationOfSymbolicLink(atPath: dosdevices + "/e:")) == nil &&
       (try? fm.destinationOfSymbolicLink(atPath: dosdevices + "/d:")) == nil,
       "no folder: no D:, a link another letter left is removed, c: untouched")
try! drive.choose(folder)
drive.link(prefix: prefix, program: "D:\\ELDEN RING\\eldenring.exe")
expect((try? fm.destinationOfSymbolicLink(atPath: dosdevices + "/d:")) == folder.path, "a chosen folder is linked as D:")
expect(probes.last?.0 == folder.path && probes.last?.1 == folder.path + "/ELDEN RING/eldenring.exe",
       "the session's program on the drive is probed")
drive.link(prefix: prefix, program: "C:\\x.exe")
expect(LogStore.shared.lines.last?.contains("(keep)") == true && probes.last?.1 == nil, "the next session keeps the link; a C: program is not probed")
expect(drive.hostPath(ofWindowsPath: "D:\\ELDEN RING") == folder.resolvingSymlinksInPath().path + "/ELDEN RING" &&
       drive.hostPath(ofWindowsPath: "E:\\x") == nil, "a D: working folder on the host")
MadeiraConfig.values["external-drive-letter"] = "e"
drive.link(prefix: prefix, program: nil)
expect((try? fm.destinationOfSymbolicLink(atPath: dosdevices + "/d:")) == nil &&
       (try? fm.destinationOfSymbolicLink(atPath: dosdevices + "/e:")) == folder.path, "another letter moves the link")
MadeiraConfig.values["external-drive-letter"] = "f"
try! fm.createDirectory(atPath: dosdevices + "/f:", withIntermediateDirectories: true)
drive.link(prefix: prefix, program: nil)
var isDir: ObjCBool = false
expect(fm.fileExists(atPath: dosdevices + "/f:", isDirectory: &isDir) && isDir.boolValue &&
       LogStore.shared.lines.contains { $0.contains("is not a link; left as it is") }, "a folder at the letter is left alone")
MadeiraConfig.values["external-drive-letter"] = nil
drive.link(prefix: prefix, program: nil)
expect(drive.sessionCheck() == nil, "a connected drive: nothing to say")
let unplugged = base.appendingPathComponent("Drive-unplugged")
try! fm.moveItem(at: base.appendingPathComponent("Drive"), to: unplugged)
let lost = drive.sessionCheck()
_ = drive.sessionCheck()
expect(lost == R.disconnectedMessage(letter: "d") &&
       LogStore.shared.lines.filter { $0.contains("stopped answering during the session") }.count == 1,
       "unplugged mid-session: the message, logged once")
if case .success(let again) = drive.prepare() {
    expect(again.resolvingSymlinksInPath().path == unplugged.appendingPathComponent("Games").path,
           "the next start resolves the bookmark again (a drive connected again elsewhere)")
} else { expect(false, "the next start resolves the bookmark again") }
drive.forget()
drive.link(prefix: prefix, program: nil)
expect(drive.record == nil && (try? fm.destinationOfSymbolicLink(atPath: dosdevices + "/d:")) == nil, "forgotten: no record, no link")
if let status = { () -> ExternalDrive.Status? in try! drive.choose(unplugged.appendingPathComponent("Games")); return drive.status() }() {
    expect(status.failure == nil && status.name == "Games" && status.fileSystem != nil && status.freeBytes != nil, "Settings status: name, file system, free space")
} else { expect(false, "Settings status") }
print(failed == 0 ? "PASS ExternalDrive.swift" : "FAILED ExternalDrive.swift")
exit(failed == 0 ? 0 : 1)
'''
swift_stubs = r'''
import Foundation
enum MadeiraConfig {
    static var values: [String: String] = [:]
    static func get(_ key: String) -> String? { values[key] }
}
final class LogStore {
    static let shared = LogStore()
    enum Level { case info, error }
    var lines: [String] = []
    func log(_ message: String, level: Level = .info) { lines.append(message) }
}
var probes: [(String, String?)] = []
func madeira_external_probe(_ root: UnsafePointer<CChar>?, _ file: UnsafePointer<CChar>?, _ out: UnsafeMutablePointer<CChar>?, _ outlen: Int) {
    probes.append((String(cString: root!), file.map { String(cString: $0) }))
    _ = "fs=test".withCString { strncpy(out!, $0, outlen) }
}
'''
swiftc = shutil.which('swiftc')
if swiftc and sys.platform == 'darwin':
    sdir = work / 'swift'
    sdir.mkdir()
    (sdir / 'Stubs.swift').write_text(swift_stubs, encoding='utf-8')
    (sdir / 'main.swift').write_text(swift_main, encoding='utf-8')
    exe = sdir / 'check'
    built = subprocess.run([swiftc, '-O', '-o', str(exe), str(root / 'app/Madeira/ExternalDrive.swift'),
                            str(sdir / 'Stubs.swift'), str(sdir / 'main.swift')], capture_output=True, text=True)
    if built.returncode:
        print(built.stderr[-6000:])
        check(False, 'ExternalDrive.swift compiles with the stand-ins')
    else:
        folder = work / 'swift-run'
        folder.mkdir()
        ran = subprocess.run([str(exe), str(folder)], capture_output=True, text=True, timeout=120)
        print(ran.stdout, end='')
        if ran.returncode:
            print(ran.stderr[-4000:])
        check(ran.returncode == 0, 'ExternalDrive.swift: bookmarks, paths and links')
elif args.require_swift:
    check(False, 'swiftc on macOS (--require-swift)')
else:
    print('note: the Swift checks run on macOS with swiftc (security-scoped bookmarks are Apple Foundation)')

# ---------------------------------------------------------------- 4. source checks
entry = body_of(library, 'struct LibraryEntry: Codable, Identifiable {')
check('var drive: String?' in entry and 'var driveRoot: String { (drive ?? "c").uppercased() + ":\\\\" }' in entry and
      'var windowsPath: String { driveRoot + relativePath' in entry and 'var launchWindowsPath: String { driveRoot + launchRelativePath' in entry,
      'an entry keeps its drive (optional, so older files decode); its paths start with it')
check('"defghijklmnopqrstuvwxy".contains(drive) || steamAppID != nil' in entry and
      'if p.count >= 3, p.prefix(3).uppercased() != "C:\\\\", let host = externalHostPath(p)' in entry,
      'validate: only D: to Y:, never a Steam game; a working folder may be on the drive')
entry_code = '\n'.join(line.split('//')[0] for line in entry.split('\n'))
check('ExternalDrive' not in entry_code,
      'LibraryEntry stays free of the app types (host checks compile it alone)')
model = body_of(library, 'final class LibraryModel: ObservableObject {')
check('LibraryEntry.externalHostPath = { ExternalDrive.shared.hostPath(ofWindowsPath: $0) }' in model, 'the model connects the hook')
executable = body_of(library, '    static func executable(_ relative: String, drive letter: String? = nil) throws -> URL {')
check('switch external.root()' in executable and 'ExternalDriveRules.message(failure, letter: letter)' in executable and
      'url.path.hasPrefix(root.path + "/")' in executable and 'guard letter == external.letter' in executable,
      "a program on the drive: the folder is resolved (a missing drive's message), and the program must be inside it")
inspect = body_of(library, '    static func inspect(_ url: URL) throws -> LibraryEntry {')
check(inspect.count('entry.drive = letter') == 2 and 'root = external; letter = ExternalDrive.shared.letter' in inspect,
      'a program added from the drive gets its letter (executables and batch files)')
browser = body_of(library, 'struct ExecutableBrowser: View {')
check('ExecutableBrowser(folder: external, root: external, select: select)' in browser and
      'ExecutableBrowser(folder: file, root: root, select: select)' in browser and
      '.hasPrefix(root.path + "/")' in browser, "the + browser lists the external folder from drive_c's top level")
poll = body_of(model, '    private func poll() {')
check('if activeEntry?.drive != nil, pollTicks % 4 == 0, let lost = ExternalDrive.shared.sessionCheck()' in poll,
      'a session on the drive checks it every 2 s')
finish = body_of(model, '    private func finish() {')
check('if let lost = driveLost { error = error.map { lost + "\\n\\n" + $0 } ?? lost }' in finish and 'driveLost = nil' in finish,
      "the session's end says the drive was disconnected")
check('ExternalDriveSettings()' in library and 'struct ExternalDriveSettings: View' in library and
      '.fileImporter(isPresented: $importing, allowedContentTypes: [.folder])' in body_of(library, 'struct ExternalDriveSettings: View {'),
      'Settings › External drive picks a folder')
start = body_of(content, '    private func startLibraryEntry(_ entry: LibraryEntry) {')
check(start.index('LibraryModel.executable(entry.launchRelativePath, drive: entry.drive)') < start.index('try entry.validate()') <
      start.index('runWineFullSequence(profile: entry)'), "the launch's preflight resolves the drive first")
server = body_of(content, '    private func startWineserver() {')
check(server.index('ExternalDrive.shared.link(prefix: winePrefixPath, program:') < server.index('wineserver_start(winePrefixPath)'),
      'the letter is linked before the wineserver and every Wine process')
check(project.count('ExternalDrive.swift in Sources') == 2 and project.count('path = ExternalDrive.swift;') == 1,
      'ExternalDrive.swift is in the app target')
check('MadeiraConfig.get("external-drive-letter")' in drive_swift and '"external-drive-letter": {' in generator,
      'the letter is a madeira.cfg key with a Settings catalog title')
check('key: "external-drive-letter"' in catalog, 'the generated catalog lists external-drive-letter (regenerated on Linux)')
check('.bookmarkData(options: [],' in drive_swift and '.minimalBookmark' not in drive_swift,
      'a full bookmark, not a minimal one')

machine = body_of(bridge, 'static uint16_t madeira_target_machine(const char *exe, const char *prefix, NSString *bundle)\n{')
check('madeira_dos_to_unix(exe, prefix, probe, sizeof(probe))' in machine, "the 32/64-bit probe finds a program on D:")
wine_thread = bridge[bridge.index('const char *launch_workdir = getenv("MADEIRA_WORKDIR");'):bridge.index('LOG("Calling __wine_main...");')]
check('madeira_dos_to_unix(workdir, g_prefix_path, unix_dir, sizeof(unix_dir))' in wine_thread and
      'snprintf(drive_link, sizeof(drive_link), "dosdevices/%c:", madeira_exe[0] | 0x20);' in wine_thread and
      'madeira_wine_ran = 1;' in wine_thread, "the working folder and the program's folder on D: go through dosdevices")
check('void madeira_external_probe(const char *root, const char *file, char *out, size_t outlen);' in bridge_h and
      'madeira_external_probe_ex(root, file, !madeira_wine_ran, out, outlen);' in bridge,
      'the probe touches mapped pages only before Wine has run in this app run')

view = body_of(virt, 'static NTSTATUS map_file_into_view_ex( struct file_view *view, int fd, size_t start, size_t size,\n'
                     '                                       off_t offset, unsigned int vprot, BOOL removable,\n'
                     '                                       BOOL for_image )\n{')
header = body_of(virt, 'static NTSTATUS map_pe_header( void *ptr, size_t size, size_t map_size, int fd, BOOL *removable )\n{')
check('pread(' not in view and 'pread(' not in header and view.count('ios_fmap_read(') == 1 and header.count('ios_fmap_read(') == 2,
      'every read copy of a file view or image goes through ios_fmap_read')
check('ios_fmap_after_mmap_error( err, (vprot & VPROT_WRITE) != 0, &fail ) == IOS_FMAP_READ' in view and
      'return STATUS_NO_MEMORY' not in header and 'ios_fmap_note( IOS_FMAP_MMAP_REFUSED, errno, fd, "an image header", map_size );' in header,
      'every refusal of a private or read-only view reads; the header too')
check(view.count('if (ios_fmap_read_fails( ios_fmap_external( fd ) )) return errno_to_status( err );') == 1 and
      header.count('if (ios_fmap_read_fails( ios_fmap_external( fd ) )) return errno_to_status( err );') == 2,
      'a failed read on the drive fails the mapping with its status')
check('ios_map_ring[slot].external = fd >= 0 && ios_fmap_external( fd ) == 1;' in virt and 'ON THE EXTERNAL DRIVE' in virt,
      'a fault report says when the mapping was on the external drive')
check('python3 tests/host/check-external-drive.py' in workflow and 'codex/external-ssd-games-20261017' in workflow,
      'host-stability runs this check on this branch')

shutil.rmtree(work, ignore_errors=True)
print('PASS' if not failures else 'FAILED: ' + '; '.join(failures))
sys.exit(1 if failures else 0)
