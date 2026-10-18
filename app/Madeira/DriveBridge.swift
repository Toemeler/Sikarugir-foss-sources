//
//  DriveBridge.swift
//  Madeira
//
//  The external drive over the iPad's file service (owner's request, 2026-10-18).
//  ExternalDrive gives Windows programs a folder on a USB-C drive as D:, but that
//  folder lies outside Madeira's container: a PC that reads and writes Madeira's
//  Documents over USB or Wi-Fi (house_arrest) cannot see it. While Madeira runs,
//  it answers requests a PC leaves in Documents/drive-bridge/:
//
//    requests/<id>.json   {"op": "ls" | "stat" | "hash" | "pull" | "push", "path": "ELDEN RING/regulation.bin"}
//    in/<id>              the new contents, for "push"
//    responses/<id>.json  {"id", "ok", "error", "op", "path", "size", "mtime", "dir", "sha256", "entries", "backup"}
//    out/<id>             the file's contents, for "pull"
//    backup/              every file a "push" replaced, as it was: <path with / as __>.<first 16 of its SHA-256>
//
//  Paths are relative to the drive's folder and never leave it (no "..", no
//  absolute paths, symbolic links resolved and checked). Nothing on the drive is
//  ever deleted: "push" writes the new contents next to the old file, renames it
//  over the old one and hashes it again; a folder that does not exist is not made.
//  Answered once a second while Madeira runs; off with madeira.cfg drive-bridge = 0.
//

import CryptoKit
import Foundation

enum DriveBridgeRules {
    static let idCharacters = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

    /// A request's id: its file name without .json, 1-64 letters, digits, _ or -.
    static func requestID(fileName: String) -> String? {
        guard fileName.hasSuffix(".json") else { return nil }
        let id = String(fileName.dropLast(5))
        guard !id.isEmpty, id.count <= 64, id.unicodeScalars.allSatisfy({ idCharacters.contains($0) }) else { return nil }
        return id
    }

    /// "ELDEN RING/regulation.bin" (or with backslashes) under `root`; nil for anything that could leave it.
    static func resolve(_ relative: String, root: String) -> String? {
        let rel = relative.replacingOccurrences(of: "\\", with: "/")
        if rel.hasPrefix("/") || rel.contains("\0") || rel.contains(":") { return nil }
        let parts = rel.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        if parts.contains(where: { $0 == ".." || $0 == "." }) { return nil }
        let base = (root as NSString).standardizingPath
        let full = parts.isEmpty ? base : (base as NSString).appendingPathComponent(parts.joined(separator: "/"))
        // A symbolic link inside the folder must not lead out of it: compare with links resolved
        // (an existing file's own path; for a new file, its folder's).
        let rbase = (base as NSString).resolvingSymlinksInPath
        let probe = FileManager.default.fileExists(atPath: full) ? full : (full as NSString).deletingLastPathComponent
        let rprobe = (probe as NSString).resolvingSymlinksInPath
        guard rprobe == rbase || rprobe.hasPrefix(rbase + "/") else { return nil }
        return full
    }

    /// backup/ name of a replaced file.
    static func backupName(_ relative: String, sha256: String) -> String {
        let flat = relative.replacingOccurrences(of: "\\", with: "/").split(separator: "/").joined(separator: "__")
        return flat + "." + sha256.prefix(16)
    }

    static let maxEntries = 4000
    static let chunk = 4 << 20
}

/// The request loop, without the app around it (tests/host/check-drive-bridge.py runs it on a Mac).
final class DriveBridgeCore {
    let dir: URL
    private let root: () -> URL?
    private let log: (String) -> Void
    private let fm = FileManager.default
    private var lastSweep = Date.distantPast

    init(documents: URL, root: @escaping () -> URL?, log: @escaping (String) -> Void) {
        dir = documents.appendingPathComponent("drive-bridge", isDirectory: true)
        self.root = root
        self.log = log
    }

    private func sub(_ name: String) -> URL { dir.appendingPathComponent(name, isDirectory: true) }

    func makeFolders() {
        for name in ["requests", "in", "responses", "out", "backup"] {
            try? fm.createDirectory(at: sub(name), withIntermediateDirectories: true)
        }
    }

    /// Answers every waiting request; returns how many. Also drops old answers and pulled copies.
    @discardableResult
    func pollOnce(now: Date = Date()) -> Int {
        let requests = sub("requests")
        let names = (try? fm.contentsOfDirectory(atPath: requests.path)) ?? []
        var handled = 0
        defer {   // after answers, and once a minute when idle (old pulled copies must not stay in Documents)
            if handled > 0 || now.timeIntervalSince(lastSweep) > 60 { sweep(now: now); lastSweep = now }
        }
        for name in names.sorted() {
            let file = requests.appendingPathComponent(name)
            guard let id = DriveBridgeRules.requestID(fileName: name) else {
                if name.hasSuffix(".json") { try? fm.removeItem(at: file) }   // a malformed name: nothing to answer to
                continue
            }
            guard let data = try? Data(contentsOf: file) else { continue }   // still being written
            var answer: [String: Any] = ["id": id]
            if let request = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
                answer.merge(handle(id: id, request: request)) { _, new in new }
            } else {
                // Perhaps still arriving (a PC writes <id>.json.part and renames it, but not every client
                // does): a file younger than 3 s is read again on the next round.
                if let m = (try? fm.attributesOfItem(atPath: file.path))?[.modificationDate] as? Date, now.timeIntervalSince(m) < 3 { continue }
                answer["ok"] = false; answer["error"] = "the request is not a JSON object"
            }
            write(answer, id: id)
            try? fm.removeItem(at: file)
            handled += 1
        }
        return handled
    }

    private func write(_ answer: [String: Any], id: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: answer, options: [.sortedKeys]) else { return }
        let tmp = sub("responses").appendingPathComponent(id + ".json.tmp")
        let out = sub("responses").appendingPathComponent(id + ".json")
        do {
            try data.write(to: tmp)
            try? fm.removeItem(at: out)
            try fm.moveItem(at: tmp, to: out)
        } catch { log("[drive-bridge] \(id): the answer could not be written: \(error)") }
    }

    /// Answers older than an hour and pulled copies older than 15 minutes go.
    private func sweep(now: Date) {
        for (name, age) in [("responses", 3600.0), ("out", 900.0)] {
            let folder = sub(name)
            for f in (try? fm.contentsOfDirectory(atPath: folder.path)) ?? [] {
                let p = folder.appendingPathComponent(f).path
                if let m = (try? fm.attributesOfItem(atPath: p))?[.modificationDate] as? Date, now.timeIntervalSince(m) > age {
                    try? fm.removeItem(atPath: p)
                }
            }
        }
    }

    private func info(_ path: String) -> (size: UInt64, mtime: Double, dir: Bool)? {
        guard let a = try? fm.attributesOfItem(atPath: path) else { return nil }
        let dir = (a[.type] as? FileAttributeType) == .typeDirectory
        return ((a[.size] as? NSNumber)?.uint64Value ?? 0, (a[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0, dir)
    }

    /// Streams `from` (into `to` when given) and returns its SHA-256 and size.
    private func stream(_ from: String, to: String? = nil) throws -> (sha: String, size: UInt64) {
        guard let input = FileHandle(forReadingAtPath: from) else { throw CocoaError(.fileReadNoSuchFile) }
        defer { try? input.close() }
        var output: FileHandle?
        if let to {
            guard fm.createFile(atPath: to, contents: nil), let h = FileHandle(forWritingAtPath: to) else { throw CocoaError(.fileWriteUnknown) }
            output = h
        }
        defer { try? output?.close() }
        var hash = SHA256()
        var size: UInt64 = 0
        while true {
            let data = try input.read(upToCount: DriveBridgeRules.chunk) ?? Data()
            if data.isEmpty { break }
            hash.update(data: data)
            try output?.write(contentsOf: data)
            size += UInt64(data.count)
        }
        if let output { try output.synchronize() }
        return (hash.finalize().map { String(format: "%02x", $0) }.joined(), size)
    }

    private func handle(id: String, request: [String: Any]) -> [String: Any] {
        let op = request["op"] as? String ?? ""
        let rel = request["path"] as? String ?? ""
        var a: [String: Any] = ["op": op, "path": rel, "ok": false]
        guard let folder = root() else { a["error"] = "no external drive folder (Settings › External drive), or it is not connected"; return a }
        guard let path = DriveBridgeRules.resolve(rel, root: folder.path) else { a["error"] = "the path leaves the drive's folder"; return a }
        do {
            switch op {
            case "ls":
                guard let i = info(path), i.dir else { a["error"] = "not a folder"; return a }
                var entries: [[String: Any]] = []
                for name in try fm.contentsOfDirectory(atPath: path).sorted().prefix(DriveBridgeRules.maxEntries) {
                    if let e = info((path as NSString).appendingPathComponent(name)) {
                        entries.append(["name": name, "size": e.size, "mtime": e.mtime, "dir": e.dir])
                    }
                }
                a["entries"] = entries
            case "stat":
                guard let i = info(path) else { a["error"] = "not found"; return a }
                a["size"] = i.size; a["mtime"] = i.mtime; a["dir"] = i.dir
            case "hash":
                guard let i = info(path), !i.dir else { a["error"] = "not a file"; return a }
                let s = try stream(path)
                a["size"] = s.size; a["mtime"] = i.mtime; a["sha256"] = s.sha
            case "pull":
                guard let i = info(path), !i.dir else { a["error"] = "not a file"; return a }
                let s = try stream(path, to: sub("out").appendingPathComponent(id).path)
                a["size"] = s.size; a["mtime"] = i.mtime; a["sha256"] = s.sha
            case "push":
                let incoming = sub("in").appendingPathComponent(id).path
                guard let n = info(incoming), !n.dir else { a["error"] = "in/\(id) is missing"; return a }
                let parent = (path as NSString).deletingLastPathComponent
                guard let p = info(parent), p.dir else { a["error"] = "the folder does not exist on the drive"; return a }
                if let old = info(path) {
                    guard !old.dir else { a["error"] = "a folder of that name exists"; return a }
                    let backup = sub("backup").appendingPathComponent(DriveBridgeRules.backupName(rel, sha256: "pending")).path
                    let saved = try stream(path, to: backup)
                    let named = sub("backup").appendingPathComponent(DriveBridgeRules.backupName(rel, sha256: saved.sha)).path
                    if fm.fileExists(atPath: named) { try? fm.removeItem(atPath: backup) } else { try fm.moveItem(atPath: backup, toPath: named) }
                    a["backup"] = (named as NSString).lastPathComponent
                    a["old_sha256"] = saved.sha
                }
                let tmp = path + ".madeira-new"
                let wrote = try stream(incoming, to: tmp)
                guard rename(tmp, path) == 0 else {
                    let err = String(cString: strerror(errno))
                    try? fm.removeItem(atPath: tmp)
                    a["error"] = "rename over the old file failed: \(err)"; return a
                }
                let check = try stream(path)
                guard check.sha == wrote.sha else { a["error"] = "the file reads back differently (\(check.sha.prefix(16)))"; return a }
                try? fm.removeItem(atPath: incoming)
                a["size"] = check.size; a["sha256"] = check.sha; a["mtime"] = info(path)?.mtime ?? 0
            default:
                a["error"] = "unknown op (ls, stat, hash, pull, push)"; return a
            }
        } catch {
            a["error"] = "\(error)"; return a
        }
        a["ok"] = true
        log("[drive-bridge] \(op) \(rel)" + ((a["sha256"] as? String).map { " sha256 \($0.prefix(16))" } ?? "") +
            ((a["backup"] as? String).map { " (old one kept as backup/\($0))" } ?? ""))
        return a
    }
}

/// The app's bridge: started with the app, answering once a second.
final class DriveBridge {
    static let shared = DriveBridge()
    private let queue = DispatchQueue(label: "madeira.drive-bridge", qos: .utility)
    private var timer: DispatchSourceTimer?
    private var core: DriveBridgeCore?

    func start() {
        guard timer == nil, MadeiraConfig.bool("drive-bridge", default: true),   // the external drive over USB / Wi-Fi: requests in Documents/drive-bridge (0 = off)
              let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first else { return }
        let core = DriveBridgeCore(documents: documents,
                                   root: { try? ExternalDrive.shared.root().get() },
                                   log: { LogStore.shared.log($0) })
        core.makeFolders()
        self.core = core
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 2, repeating: 1.0, leeway: .milliseconds(250))
        t.setEventHandler { [weak core] in core?.pollOnce() }
        t.resume()
        timer = t
    }
}
