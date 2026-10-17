//
//  ExternalDrive.swift
//  Madeira
//
//  Games on an external drive (docs/EXTERNAL_DRIVE.md). Settings › External
//  drive picks a folder on a USB-C drive (APFS or exFAT) once; Madeira keeps a
//  security-scoped bookmark to it (Application Support/ExternalDrive/folder.json)
//  and, before every session, resolves it, starts its access for the rest of the
//  app's run and links the prefix's dosdevices/d: to it, so Windows programs see
//  the folder as drive D:. A library entry on the drive keeps its path relative
//  to that folder (LibraryEntry.drive). Saves, AppData and the prefix stay on
//  the iPad: nothing Madeira writes goes to the drive.
//
//  Wine runs inside this process (README: even the wineserver is a thread), so
//  the access this process holds covers every Windows program of every session.
//
//  Foundation only: tests/host/check-external-drive.py compiles this file with
//  small stand-ins for MadeiraConfig and LogStore and runs the bookmark, path
//  and link rules on a real folder.
//

import Foundation

/// The path, letter, link and message rules, without any file access.
enum ExternalDriveRules {
    static let defaultLetter = "d"
    /// The letters an external folder may take: C: is the prefix's drive_c, and
    /// Z: is Wine's root-of-the-filesystem letter on other hosts.
    static let letters = "defghijklmnopqrstuvwxy"

    /// madeira.cfg `external-drive-letter` ("d", "E", "f:"): one of D: to Y:,
    /// lower case; anything else is D:.
    static func letter(_ raw: String?) -> String {
        var value = (raw ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        if value.hasSuffix("\\") { value.removeLast() }
        if value.hasSuffix(":") { value.removeLast() }
        guard value.count == 1, letters.contains(value) else { return defaultLetter }
        return value
    }

    /// "D:\ELDEN RING\eldenring.exe" for ("d", "ELDEN RING/eldenring.exe").
    static func windowsPath(letter: String, relative: String) -> String {
        letter.uppercased() + ":\\" + relative.replacingOccurrences(of: "/", with: "\\")
    }

    /// `path` relative to `root` ("ELDEN RING/eldenring.exe"), or nil when it is
    /// not strictly inside it. Callers pass resolved, standardized paths of the
    /// same form (URL.resolvingSymlinksInPath().standardizedFileURL.path).
    static func relativePath(of path: String, under root: String) -> String? {
        var base = root
        while base.count > 1 && base.hasSuffix("/") { base.removeLast() }
        guard base.hasPrefix("/"), base != "/", path.hasPrefix(base + "/") else { return nil }
        let rest = String(path.dropFirst(base.count + 1))
        let parts = rest.split(separator: "/", omittingEmptySubsequences: false)
        guard !rest.isEmpty, !parts.contains(where: { $0.isEmpty || $0 == "." || $0 == ".." }) else { return nil }
        return rest
    }

    /// D:\a\b -> <root>/a/b, and D:\ -> <root>; nil for another drive or a "..".
    static func hostPath(ofWindowsPath path: String, letter: String, root: String) -> String? {
        let p = path.trimmingCharacters(in: .whitespaces)
        guard p.count >= 3, p.prefix(3).lowercased() == letter + ":\\" else { return nil }
        var base = root
        while base.count > 1 && base.hasSuffix("/") { base.removeLast() }
        let parts = p.dropFirst(3).split(separator: "\\").map(String.init)
        guard !base.isEmpty, !parts.contains("..") else { return nil }
        return ([base] + parts).joined(separator: "/")
    }

    /// What is at dosdevices/<letter>: in the prefix.
    enum LinkState: Equatable { case missing, link(String), other }
    enum LinkPlan: Equatable { case keep, make, replace, remove, leave }
    /// What to do with dosdevices/<letter>: given what is there and the folder it
    /// should name (nil: no external folder this session). Only a symbolic link
    /// is replaced or removed; anything else at that name is left alone.
    static func linkPlan(_ state: LinkState, target: String?) -> LinkPlan {
        switch (state, target) {
        case (.missing, nil): return .keep
        case (.missing, .some): return .make
        case (.link(let current), .some(let wanted)): return current == wanted ? .keep : .replace
        case (.link, nil): return .remove
        case (.other, _): return .leave
        }
    }
    /// A link Madeira made for an external folder: "<letter>:" for D: to Y:,
    /// pointing at an absolute path. dosdevices/c: is relative (../drive_c), and
    /// Wine's "x::" device links have another name, so neither ever matches.
    static func isExternalLink(name: String, target: String) -> Bool {
        name.count == 2 && name.hasSuffix(":") && letters.contains(String(name.prefix(1))) && target.hasPrefix("/")
    }

    enum Failure: Error, Equatable {
        /// Settings › External drive has no folder.
        case noFolder
        /// The bookmark resolves to nothing, or its folder does not answer: the
        /// drive is not connected (or was unplugged).
        case notConnected(String)
        /// The folder is there but cannot be read (permission).
        case unreadable(String)
        /// The bookmark cannot be used any more (damaged, or made for another install).
        case stale(String)
    }

    static func message(_ failure: Failure, letter: String) -> String {
        let drive = letter.uppercased() + ":"
        switch failure {
        case .noFolder:
            return "This game is on drive \(drive), and no external folder is chosen. Connect the drive and choose its folder in Settings › External drive."
        case .notConnected(let name):
            return "The drive with “\(name)” (drive \(drive)) is not connected. Connect it, wait until it shows in the Files app, then try again."
        case .unreadable(let name):
            return "Madeira cannot read “\(name)” (drive \(drive)). Choose the folder again in Settings › External drive."
        case .stale(let name):
            return "Madeira lost access to “\(name)” (drive \(drive)). Choose the folder again in Settings › External drive."
        }
    }

    /// The message shown during a session whose drive stopped answering.
    static func disconnectedMessage(letter: String) -> String {
        "The external drive (\(letter.uppercased()):) was disconnected. Saves are on the iPad and safe. Close the game, reconnect the drive and start it again."
    }

    /// A bookmark resolution or folder listing error, classified.
    static func failure(for error: Error, name: String) -> Failure {
        var errors: [NSError] = [error as NSError]
        if let underlying = (error as NSError).userInfo[NSUnderlyingErrorKey] as? NSError { errors.append(underlying) }
        for ns in errors {
            if ns.domain == NSPOSIXErrorDomain {
                if [ENOENT, ENXIO, ENODEV, EIO, ENOTDIR].contains(Int32(ns.code)) { return .notConnected(name) }
                if [EPERM, EACCES].contains(Int32(ns.code)) { return .unreadable(name) }
            }
            if ns.domain == NSCocoaErrorDomain {
                if [NSFileNoSuchFileError, NSFileReadNoSuchFileError].contains(ns.code) { return .notConnected(name) }
                if [NSFileReadNoPermissionError].contains(ns.code) { return .unreadable(name) }
            }
        }
        return .stale(name)
    }
}

/// The chosen folder, as Application Support/ExternalDrive/folder.json stores it.
struct ExternalDriveRecord: Codable, Equatable {
    /// A full (not minimal) bookmark made while the picked URL was accessed:
    /// resolving it in a later run gives the folder with its access again.
    var bookmark: Data
    /// The folder's name and where it was last found, for messages and the log.
    var name: String
    var path: String
    var chosen: Date
}

final class ExternalDriveStore {
    let file: URL
    init(directory: URL) { file = directory.appendingPathComponent("folder.json") }

    static var standard: ExternalDriveStore? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            .map { ExternalDriveStore(directory: $0.appendingPathComponent("ExternalDrive", isDirectory: true)) }
    }

    func load() -> ExternalDriveRecord? {
        guard let data = try? Data(contentsOf: file) else { return nil }
        return try? JSONDecoder().decode(ExternalDriveRecord.self, from: data)
    }
    func save(_ record: ExternalDriveRecord) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(record).write(to: file, options: .atomic)
    }
    func forget() { try? FileManager.default.removeItem(at: file) }

    /// A record for `folder`, which the caller is accessing (a URL the folder
    /// picker returned, or one a bookmark resolved).
    static func record(for folder: URL) throws -> ExternalDriveRecord {
        let bookmark = try folder.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil)
        return ExternalDriveRecord(bookmark: bookmark, name: folder.lastPathComponent, path: folder.path, chosen: Date())
    }

    enum Resolution {
        /// The folder, readable, with the access `startAccess` reported (false
        /// when the folder needed no grant, as on a Mac outside a sandbox), and
        /// whether the bookmark was stale and has been made again.
        case ready(URL, scoped: Bool, refreshed: Bool)
        case failed(ExternalDriveRules.Failure)
    }

    /// Resolves the bookmark, starts the folder's access and checks the folder
    /// answers. A stale bookmark (the folder moved or the volume was mounted
    /// again) that still resolves is made again while the access is held, as
    /// Apple asks. On a failure no access is left started.
    func resolve(startAccess: (URL) -> Bool = { $0.startAccessingSecurityScopedResource() },
                 stopAccess: (URL) -> Void = { $0.stopAccessingSecurityScopedResource() }) -> Resolution {
        guard var record = load() else { return .failed(.noFolder) }
        var stale = false
        let url: URL
        do {
            url = try URL(resolvingBookmarkData: record.bookmark, options: [], relativeTo: nil, bookmarkDataIsStale: &stale)
        } catch {
            return .failed(ExternalDriveRules.failure(for: error, name: record.name))
        }
        let scoped = startAccess(url)
        do {
            var isFolder: ObjCBool = false
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isFolder) else {
                throw NSError(domain: NSPOSIXErrorDomain, code: Int(ENOENT))
            }
            guard isFolder.boolValue else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(ENOTDIR)) }
            _ = try FileManager.default.contentsOfDirectory(atPath: url.path)
        } catch {
            if scoped { stopAccess(url) }
            return .failed(ExternalDriveRules.failure(for: error, name: record.name))
        }
        var refreshed = false
        if stale, let renewed = try? Self.record(for: url) {
            record.bookmark = renewed.bookmark; refreshed = true
        }
        if refreshed || record.path != url.path || record.name != url.lastPathComponent {
            record.path = url.path; record.name = url.lastPathComponent
            try? save(record)
        }
        return .ready(url, scoped: scoped, refreshed: refreshed)
    }
}

/// The external folder for this app run: resolved and accessed once, kept for
/// every session, linked as a drive letter in the prefix before each session.
final class ExternalDrive {
    static let shared = ExternalDrive()
    private let lock = NSLock()
    /// The folder whose access this run holds, and whether a grant was started for it.
    private var folder: URL?
    private var scoped = false
    private var reportedLoss = false

    /// Application Support/ExternalDrive (the host check gives it a folder of its own).
    var store: ExternalDriveStore? = ExternalDriveStore.standard
    /// Settings: the drive letter Windows programs see the folder as.
    var letter: String { ExternalDriveRules.letter(MadeiraConfig.get("external-drive-letter")) }
    var record: ExternalDriveRecord? { store?.load() }

    /// The folder with its access held: resolved the first time, and again
    /// after it stopped answering (a drive unplugged and connected again comes
    /// back at a new mount). The access is kept until the folder is changed or
    /// forgotten in Settings, or the app ends; nothing stops it during a session.
    func prepare() -> Result<URL, ExternalDriveRules.Failure> {
        lock.lock(); defer { lock.unlock() }
        if let folder, FileManager.default.fileExists(atPath: folder.path) { return .success(folder) }
        releaseLocked()
        guard let store else { return .failure(.noFolder) }
        switch store.resolve() {
        case .ready(let url, let scoped, let refreshed):
            folder = url; self.scoped = scoped; reportedLoss = false
            LogStore.shared.log("[external-drive] \(letter.uppercased()): folder \(url.path) scoped=\(scoped ? 1 : 0)" +
                                (refreshed ? " (stale bookmark made again)" : ""))
            return .success(url)
        case .failed(let failure):
            LogStore.shared.log("[external-drive] \(letter.uppercased()): \(failure)", level: .error)
            return .failure(failure)
        }
    }

    /// The folder with its symbolic links resolved, the form the library compares paths in
    /// (LibraryModel.drive is resolved the same way).
    func root() -> Result<URL, ExternalDriveRules.Failure> {
        prepare().map { $0.resolvingSymlinksInPath().standardizedFileURL }
    }

    /// D:\a\b -> the folder's a/b while it answers; nil for another letter
    /// (LibraryEntry.externalHostPath: a working folder on the drive).
    func hostPath(ofWindowsPath path: String) -> String? {
        let letter = self.letter
        guard path.trimmingCharacters(in: .whitespaces).lowercased().hasPrefix(letter + ":\\"), record != nil,
              case .success(let folder) = root() else { return nil }
        return ExternalDriveRules.hostPath(ofWindowsPath: path, letter: letter, root: folder.path)
    }

    /// Settings › External drive › Choose folder: `picked` is the folder picker's URL.
    func choose(_ picked: URL) throws {
        let scoped = picked.startAccessingSecurityScopedResource()
        do {
            let record = try ExternalDriveStore.record(for: picked)
            guard let store else { throw CocoaError(.fileWriteUnknown) }
            try store.save(record)
        } catch {
            if scoped { picked.stopAccessingSecurityScopedResource() }
            throw error
        }
        lock.lock(); defer { lock.unlock() }
        releaseLocked()
        folder = picked; self.scoped = scoped; reportedLoss = false
        LogStore.shared.log("[external-drive] chose \(picked.path) scoped=\(scoped ? 1 : 0)")
    }

    /// Settings › External drive › Forget: the bookmark goes, the access stops
    /// and the next session starts without the drive letter.
    func forget() {
        lock.lock(); defer { lock.unlock() }
        releaseLocked()
        store?.forget()
        LogStore.shared.log("[external-drive] forgot the folder")
    }

    private func releaseLocked() {
        if let folder, scoped { folder.stopAccessingSecurityScopedResource() }
        folder = nil; scoped = false
    }

    /// Before a session (ContentView.startWineserver, ahead of the wineserver
    /// and every Wine process): dosdevices/<letter>: names the folder when one is
    /// chosen and connected, and is removed otherwise, as is a link left for
    /// another letter. Wine resolves D:\… through that link for every file, and
    /// publishes the letter at start-up (server_ios.c ios_create_drive_symlinks).
    /// `program`: the session's MADEIRA_EXE, probed (madeira_external_probe) when it is on the drive.
    func link(prefix: String, program: String?) {
        let letter = self.letter
        var target: String?
        if record != nil, case .success(let url) = prepare() { target = url.path }
        let dosdevices = (prefix as NSString).appendingPathComponent("dosdevices")
        let fm = FileManager.default
        try? fm.createDirectory(atPath: dosdevices, withIntermediateDirectories: true)
        // A link another letter setting left behind.
        for name in (try? fm.contentsOfDirectory(atPath: dosdevices)) ?? [] where name != letter + ":" {
            let path = (dosdevices as NSString).appendingPathComponent(name)
            if let old = try? fm.destinationOfSymbolicLink(atPath: path), ExternalDriveRules.isExternalLink(name: name, target: old) {
                try? fm.removeItem(atPath: path)
                LogStore.shared.log("[external-drive] removed \(name) -> \(old) (the letter is \(letter.uppercased()): now)")
            }
        }
        let path = (dosdevices as NSString).appendingPathComponent(letter + ":")
        let state: ExternalDriveRules.LinkState
        if let current = try? fm.destinationOfSymbolicLink(atPath: path) { state = .link(current) }
        else if (try? fm.attributesOfItem(atPath: path)) != nil { state = .other }
        else { state = .missing }
        let plan = ExternalDriveRules.linkPlan(state, target: target)
        switch plan {
        case .keep: break
        case .make, .replace:
            if plan == .replace { try? fm.removeItem(atPath: path) }
            if let target {
                do { try fm.createSymbolicLink(atPath: path, withDestinationPath: target) }
                catch { LogStore.shared.log("[external-drive] could not link \(letter.uppercased()): \(error.localizedDescription)", level: .error) }
            }
        case .remove: try? fm.removeItem(atPath: path)
        case .leave:
            LogStore.shared.log("[external-drive] dosdevices/\(letter): is not a link; left as it is", level: .error)
        }
        guard let target else {
            if plan == .remove { LogStore.shared.log("[external-drive] \(letter.uppercased()): removed (no folder this session)") }
            return
        }
        var report = [CChar](repeating: 0, count: 1024)
        if let program, let file = ExternalDriveRules.hostPath(ofWindowsPath: program, letter: letter, root: target) {
            madeira_external_probe(target, file, &report, report.count)
        } else {
            madeira_external_probe(target, nil, &report, report.count)
        }
        LogStore.shared.log("[external-drive] \(letter.uppercased()): -> \(target) (\(plan)) \(String(cString: report))")
    }

    /// During a session (LibraryModel.poll): nil while the folder answers, or
    /// the message to show once it stopped (the drive was unplugged).
    func sessionCheck() -> String? {
        lock.lock(); defer { lock.unlock() }
        guard let folder else { return nil }
        if access(folder.path, F_OK) == 0 { return nil }
        let err = errno
        if !reportedLoss {
            reportedLoss = true
            LogStore.shared.log("[external-drive] \(letter.uppercased()): \(folder.path) stopped answering during the session (errno \(err))", level: .error)
        }
        return ExternalDriveRules.disconnectedMessage(letter: letter)
    }

    /// Settings: the folder's state, read now.
    struct Status {
        var name: String
        var path: String?
        var failure: ExternalDriveRules.Failure?
        var fileSystem: String?
        var freeBytes: Int64?
        var readOnly = false
    }
    func status() -> Status? {
        guard let record else { return nil }
        switch prepare() {
        case .failure(let failure): return Status(name: record.name, path: nil, failure: failure)
        case .success(let url):
            var status = Status(name: url.lastPathComponent, path: url.path, failure: nil)
            var fs = statfs()
            if statfs(url.path, &fs) == 0 {
                status.fileSystem = withUnsafeBytes(of: fs.f_fstypename) { String(cString: $0.bindMemory(to: CChar.self).baseAddress!) }
                status.freeBytes = Int64(fs.f_bavail) * Int64(fs.f_bsize)
                status.readOnly = (fs.f_flags & UInt32(MNT_RDONLY)) != 0
            }
            return status
        }
    }
}
