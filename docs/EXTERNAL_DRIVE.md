# Games on an external drive

A game's files can live on a USB-C drive connected to the iPad (APFS or exFAT)
instead of in Madeira's own storage. Windows programs see a folder on the drive
as drive **D:**. Wine, the prefix, saves, AppData and every setting stay on the
iPad: nothing Madeira writes goes to the drive.

## Using it

1. Format the drive in the Files app (long-press it under Locations › Erase),
   as **APFS** or **ExFAT**. Skip this if it already has one of them.
2. In Files, make a folder on the drive (*SSD › Games*) and copy the game's
   whole folder into it (for example *On My iPad › Madeira › wine › drive_c ›
   ELDEN RING* to *SSD › Games*).
3. In Madeira: **Settings › External drive › Choose Folder…**, open the folder
   that holds the games (*SSD › Games*) and tap **Open**. Settings then shows
   the folder, its file system and free space.
4. In the library, tap **+**. At the top of drive_c's list is **External drive
   (D:)**; open it and choose the game's `.exe`. The new entry starts
   `D:\ELDEN RING\eldenring.exe`.

Keep the drive connected while a game runs. If it is not connected when a game
on D: starts, the start stops with a message naming the folder. If it is
unplugged during a game, the game's screen says so, the end of the session
reports it, and nothing in the prefix is lost; reconnect the drive and start the
game again.

**Forget Folder** removes the bookmark; entries on D: stay in the library and on
the drive but cannot start until a folder is chosen again. The letter is
`external-drive-letter` in madeira.cfg (Settings › All settings: D: by default,
D: to Y:); entries keep the letter they were added with.

A game on D: is a new library entry with its own settings: its Game details,
its game config (`Application Support/GameConfigs/<hash of its Windows path>.cfg`)
and, for a game Madeira knows (Elden Ring), its recommended settings, which are
applied on first sight as for any new entry.

## How it works

- The folder picker is `app/Madeira/FolderPicker.m`, not SwiftUI's `fileImporter`.
  Inside LiveContainer, its TweakLoader swaps every document picker's types for
  "any item + folder", so a folder picker can open files and enter folders but
  never choose one (build 56 on the iPad: Open did nothing, by pointer or touch).
  The hook is installed by exchanging implementations, so FolderPicker.m calls
  UIKit's original initializer through the hook's selector, for folders only and
  in place (not a copy), when that implementation lies in UIKit; otherwise the
  ordinary initializer. The drive's top level could not be chosen either; choose
  a folder on the drive (for example *Games*) and keep the games in it.

- `app/Madeira/ExternalDrive.swift`: the folder picker's URL is accessed and a
  full (not minimal) bookmark is stored in
  `Application Support/ExternalDrive/folder.json`. Before every session it is
  resolved, its security-scoped access started and kept for the rest of the
  app's run (Wine runs inside Madeira's process, so that access covers every
  Windows program), and `wine/dosdevices/d:` is linked to the folder before the
  wineserver starts. Wine resolves `D:\…` through that link and publishes the
  letter at start-up (`server_ios.c ios_create_drive_symlinks`). A stale
  bookmark that still resolves (a drive mounted again) is made again; one that
  does not is reported as not connected, unreadable or stale. Without a folder,
  or with the drive missing, the link is removed. While a game on D: runs, the
  library checks the folder every 2 s.
- `LibraryEntry.drive` holds the letter of an entry on the drive; its
  `relativePath` is relative to the folder. `LibraryModel.executable` and
  `inspect` resolve it; the + browser lists the folder. Both a direct start
  and a start with Steam running (Madeira Dock, which Elden Ring's
  recommended settings choose for a copy added by hand; Dock accepts a program
  on any local drive) check the drive before anything starts.
- `WineProcessBridge.m`: the program's folder, a working folder and the
  32/64-bit probe map `X:\…` through `dosdevices/x:` for letters other than C:
  (`madeira_dos_to_unix`; C: is mapped exactly as before).
  `madeira_external_probe` writes one `[external-drive]` log line per session:
  the volume's file system, mount flags and free space, and for the program
  being started a timed 8 MB read and whether the kernel maps it privately and
  shared (a page that cannot be read is caught and reported as a fault).
- `build/ntdll-unix/virtual_ios.c`, "file-map core": a game's files are read
  through `NtReadFile` (`pread`), which works on any volume, and mapped by
  `map_file_into_view_ex` (file views and aligned image sections) and
  `map_pe_header`. When mmap is refused, every errno now falls back to a read
  copy for read-only and copy-on-write views (upstream failed anything but five
  errnos with STATUS_NO_MEMORY), the copy is read in pieces of at most 1 GB
  (one `pread` above 2 GB failed on Darwin and left zeros), and a read that
  fails on another volume than the prefix's (an unplugged drive) fails the
  mapping with its status instead of handing the game zeros. Each kind of
  event is logged once per errno as `[file-map]`. Behaviour on drive_c is
  unchanged. A fault report for a mapping on the drive says so.

Checks: `tests/host/check-external-drive.py` (host-stability workflow: Linux
for the C parts, macOS for the probe and the Swift bookmark and link checks).

## Device results

See the commit that records the device run for the measured `[external-drive]`
and `[file-map]` lines.
