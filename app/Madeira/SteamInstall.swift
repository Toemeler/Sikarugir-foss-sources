// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation

// Where Madeira's own Steam downloads live, and how one is read and removed
// (docs/STEAM_LIBRARY.md). Foundation only, so tests/host/check-steam-library.py
// compiles this file as it is.
//
// A download goes into Madeira Dock's own Steam library folder,
// C:\Program Files (x86)\Steam\steamapps, as `common/<installdir>` plus Steam's
// `appmanifest_<appid>.acf` install record. That is exactly the layout
// Madeira Dock's discovery (MadeiraDock.games) reads and Valve's client, which
// Dock starts the game through, understands: a game is "installed" for Dock
// once its record says so, and the record is written last.

enum SteamInstallPaths {
    /// The library folder, relative to drive_c (the value `DockGame.library` has).
    static let libraryRelative = "Program Files (x86)/Steam/steamapps"

    static func steamApps(drive: URL) -> URL { drive.appendingPathComponent(libraryRelative, isDirectory: true) }
    static func common(drive: URL) -> URL { steamApps(drive: drive).appendingPathComponent("common", isDirectory: true) }

    /// Downloads made while an external drive is connected (ExternalDrive.swift,
    /// docs/EXTERNAL_DRIVE.md) go into a Steam library on the drive, SteamLibrary in
    /// its chosen folder. It is reached through the link the app keeps in drive_c
    /// (ExternalDrive), so its games keep drive_c paths for Madeira, Madeira Dock and
    /// Wine alike, and Valve's client knows it from libraryfolders.vdf.
    static let externalRelative = "ExternalDrive/SteamLibrary/steamapps"
    static let externalWindowsRoot = "C:\\ExternalDrive\\SteamLibrary"
    /// The client's own library as Valve's libraryfolders.vdf names it ("C:\Program Files (x86)\Steam").
    static var clientWindowsRoot: String {
        "C:\\" + libraryRelative.dropLast("/steamapps".count).replacingOccurrences(of: "/", with: "\\")
    }

    /// Whether an install (its drive-relative library folder) is in a library
    /// Madeira downloads into, and so can be updated or removed here.
    static func isManaged(library: String) -> Bool {
        library.caseInsensitiveCompare(libraryRelative) == .orderedSame ||
            library.caseInsensitiveCompare(externalRelative) == .orderedSame
    }
}

/// Valve's list of Steam library folders (`<client>/steamapps/libraryfolders.vdf`),
/// which its client keeps and reads at start. The external drive's library is
/// added once so the client finds the games downloaded there.
enum SteamLibraryFolders {
    /// `text` with `windowsPath` added as the next library, or nil when it lists
    /// it already or cannot be read as a library list (a damaged file is left as
    /// it is). Without a file (`text` nil) the list is the client's own library
    /// and this one.
    static func adding(_ windowsPath: String, clientPath: String, to text: String?, contentID: UInt64) -> String? {
        func block(_ index: Int, _ path: String, _ id: UInt64) -> String {
            let escaped = path.replacingOccurrences(of: "\\", with: "\\\\")
            return ["\t\"\(index)\"", "\t{", "\t\t\"path\"\t\t\"\(escaped)\"", "\t\t\"label\"\t\t\"\"",
                    "\t\t\"contentid\"\t\t\"\(id)\"", "\t\t\"totalsize\"\t\t\"0\"",
                    "\t\t\"update_clean_bytes_tally\"\t\t\"0\"", "\t\t\"time_last_update_verified\"\t\t\"0\"",
                    "\t\t\"apps\"", "\t\t{", "\t\t}", "\t}"].joined(separator: "\n")
        }
        guard let text else {
            return "\"libraryfolders\"\n{\n" + block(0, clientPath, contentID &+ 1) + "\n" + block(1, windowsPath, contentID) + "\n}\n"
        }
        guard let data = text.data(using: .utf8), data.count <= 1 << 20, var parser = try? SteamKeyValues(data),
              let root = try? parser.read(), let folders = root["libraryfolders"]?.fields,
              let close = text.lastIndex(of: "}") else { return nil }
        let listed = folders.values.compactMap { $0["path"]?.string?.replacingOccurrences(of: "\\\\", with: "\\") }
        guard !listed.contains(where: { $0.caseInsensitiveCompare(windowsPath) == .orderedSame }) else { return nil }
        let next = (folders.keys.compactMap { Int($0) }.max() ?? -1) + 1
        return String(text[..<close]) + block(next, windowsPath, contentID) + "\n" + String(text[close...])
    }

    /// The record Steam keeps in each library's own folder (`libraryfolder.vdf`).
    static func folderRecord(contentID: UInt64) -> String {
        "\"libraryfolder\"\n{\n\t\"contentid\"\t\t\"\(contentID)\"\n\t\"label\"\t\t\"\"\n}\n"
    }

    /// Adds the library at `libraryRoot` (Windows path `windowsPath`) to the client's
    /// list in `steamApps`, with its own record. True when the list changed.
    @discardableResult
    static func register(_ windowsPath: String, libraryRoot: URL, steamApps: URL) -> Bool {
        let fm = FileManager.default
        let id = UInt64.random(in: 1...UInt64(Int64.max))
        let record = libraryRoot.appendingPathComponent("libraryfolder.vdf")
        if !fm.fileExists(atPath: record.path) { try? folderRecord(contentID: id).write(to: record, atomically: true, encoding: .utf8) }
        let file = steamApps.appendingPathComponent("libraryfolders.vdf")
        var current: String?
        if fm.fileExists(atPath: file.path) {
            guard let text = try? String(contentsOf: file, encoding: .utf8) else { return false }
            current = text
        }
        guard let next = adding(windowsPath, clientPath: SteamInstallPaths.clientWindowsRoot, to: current, contentID: id) else { return false }
        do {
            try fm.createDirectory(at: steamApps, withIntermediateDirectories: true)
            try next.write(to: file, atomically: true, encoding: .utf8)
            return true
        } catch { return false }
    }
}

enum SteamInstallFiles {
    /// The `buildid` the install record of an app states, or nil when there is
    /// no readable record.
    static func buildID(appID: Int, steamApps: URL) -> Int? {
        guard let state = record(appID: appID, steamApps: steamApps) else { return nil }
        return state["buildid"]?.string.flatMap { Int($0) }
    }

    /// The install size the record states (`SizeOnDisk`, bytes), or nil.
    static func sizeOnDisk(appID: Int, steamApps: URL) -> Int64? {
        guard let state = record(appID: appID, steamApps: steamApps),
              let size = state["SizeOnDisk"]?.string.flatMap({ Int64($0) }), size > 0 else { return nil }
        return size
    }

    /// Removes an app's install: its folder under `common`, its record, its
    /// resume journal and the records of the apps that own its shared depots
    /// (those describe the same folder). Only paths strictly inside
    /// `steamApps/common` are removed.
    nonisolated static func delete(appID: Int, folderName: String, steamApps: URL) {
        let fm = FileManager.default
        let common = steamApps.appendingPathComponent("common", isDirectory: true).resolvingSymlinksInPath().standardizedFileURL
        let folder = common.appendingPathComponent(safeFolderName(folderName)).resolvingSymlinksInPath().standardizedFileURL
        if folder.path.hasPrefix(common.path + "/"), folder.deletingLastPathComponent().path == common.path {
            try? fm.removeItem(at: folder)
        }
        let recordURL = steamApps.appendingPathComponent("appmanifest_\(appID).acf")
        if let state = record(appID: appID, steamApps: steamApps) {
            let shared = (state["SharedDepots"]?.fields ?? [:]).sorted(by: { $0.key < $1.key }).prefix(64)
            for (_, owner) in shared {
                guard let ownerID = owner.string.flatMap({ Int($0) }), ownerID > 0, ownerID != appID,
                      let ownerState = record(appID: ownerID, steamApps: steamApps),
                      let dir = ownerState["installdir"]?.string
                else { continue }
                // An owner in another folder (its own installdir) goes too when
                // it holds nothing but depots shared into this app.
                let mine = Set(shared.filter { $0.value.string == owner.string }.map { $0.key })
                let held = Set((ownerState["InstalledDepots"]?.fields ?? [:]).keys)
                guard safeFolderName(dir).caseInsensitiveCompare(safeFolderName(folderName)) == .orderedSame ||
                      held.isSubset(of: mine) else { continue }
                try? fm.removeItem(at: steamApps.appendingPathComponent("appmanifest_\(ownerID).acf"))
            }
        }
        try? fm.removeItem(at: recordURL)
        try? fm.removeItem(at: steamApps.appendingPathComponent("downloading/\(appID)", isDirectory: true))
    }

    /// Validates a manifest path and folds directory spelling to the first
    /// one seen (case-insensitively). Returns nil for anything that could
    /// escape the install folder.
    nonisolated static func safeRelativePath(_ name: String, folded: inout [String: String]) -> String? {
        let parts = name.replacingOccurrences(of: "\\", with: "/").split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        guard !parts.isEmpty, parts.count <= 64, name.utf8.count < 1024,
              !parts.contains(where: { $0 == "." || $0 == ".." || $0.contains(":") ||
                  $0.unicodeScalars.contains(where: { $0.value < 0x20 }) }) else { return nil }
        var built: [String] = []
        for (index, part) in parts.enumerated() {
            if index == parts.count - 1 { built.append(part); break }
            let key = (built + [part]).joined(separator: "/").lowercased()
            if let existing = folded[key] {
                built = existing.split(separator: "/").map(String.init)
            } else {
                built.append(part)
                folded[key] = built.joined(separator: "/")
            }
        }
        return built.joined(separator: "/")
    }

    /// Install folders come from app metadata; keep them to one safe component.
    nonisolated static func safeFolderName(_ name: String) -> String {
        let cleaned = name.replacingOccurrences(of: "\\", with: "/").split(separator: "/").last.map(String.init) ?? ""
        guard !cleaned.isEmpty, cleaned != ".", cleaned != "..", !cleaned.contains(":"),
              !cleaned.unicodeScalars.contains(where: { $0.value < 0x20 }) else { return "app" }
        return cleaned
    }

    /// The `AppState` block of `appmanifest_<appid>.acf` when the file is
    /// readable, bounded, and names this app.
    private static func record(appID: Int, steamApps: URL) -> SteamValue? {
        let url = steamApps.appendingPathComponent("appmanifest_\(appID).acf")
        guard let data = try? Data(contentsOf: url), data.count <= 1 << 20,
              var parser = try? SteamKeyValues(data), let root = try? parser.read(),
              let state = root["AppState"], state["appid"]?.string == String(appID) else { return nil }
        return state
    }
}
