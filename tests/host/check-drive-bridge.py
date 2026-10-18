#!/usr/bin/env python3
"""The external drive over the iPad's file service (app/Madeira/DriveBridge.swift).

1. Source: the bridge is in the Xcode project's sources, starts with the app and
   is on unless madeira.cfg drive-bridge = 0.
2. Swift (macOS; --require-swift turns a missing compiler into a failure):
   DriveBridgeCore against a temporary folder as the drive and another as
   Documents: ls / stat / hash / pull answer with sizes and SHA-256; push keeps
   the replaced file in backup/ and reads the new one back; nothing leaves the
   drive's folder (.., absolute paths, a drive letter, a symbolic link out);
   malformed requests are answered or dropped, one still arriving is waited for.
"""
import argparse, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
args = parser.parse_args()
failed = 0


def check(cond, what):
    global failed
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        failed += 1


# ---------------------------------------------------------------- 1. source
src = (root / 'app/Madeira/DriveBridge.swift').read_text(encoding='utf-8')
app = (root / 'app/Madeira/MadeiraApp.swift').read_text(encoding='utf-8')
pbx = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text(encoding='utf-8')
check('DriveBridge.swift in Sources' in pbx and 'path = DriveBridge.swift;' in pbx, 'DriveBridge.swift is built into the app')
check('DriveBridge.shared.start()' in app, 'the bridge starts with the app')
check('MadeiraConfig.bool("drive-bridge", default: true)' in src, 'on unless madeira.cfg drive-bridge = 0')
check('removeItem(atPath: path)' not in src and 'removeItem(at: URL(fileURLWithPath: path' not in src,
      'nothing on the drive is removed by path (only its own .madeira-new on a failed rename)')

# ---------------------------------------------------------------- 2. Swift
swift_stubs = r'''
import Foundation
enum MadeiraConfig { static func bool(_ key: String, default dflt: Bool = false) -> Bool { dflt } }
final class LogStore {
    static let shared = LogStore()
    var lines: [String] = []
    func log(_ message: String) { lines.append(message) }
}
enum ExternalDriveRules { enum Failure: Error { case noFolder } }
final class ExternalDrive {
    static let shared = ExternalDrive()
    var folder: URL?
    func root() -> Result<URL, ExternalDriveRules.Failure> { folder.map { .success($0) } ?? .failure(.noFolder) }
}
'''
swift_main = r'''
import CryptoKit
import Foundation
var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "ok   " : "FAIL ") + what); if !cond { failed += 1 } }
func sha(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }

let work = URL(fileURLWithPath: CommandLine.arguments[1])
let fm = FileManager.default
let drive = work.appendingPathComponent("Games", isDirectory: true)
let docs = work.appendingPathComponent("Documents", isDirectory: true)
let outside = work.appendingPathComponent("outside", isDirectory: true)
for d in [drive.appendingPathComponent("ELDEN RING"), docs, outside] { try! fm.createDirectory(at: d, withIntermediateDirectories: true) }
let old = Data((0..<300_000).map { UInt8($0 % 251) })
try! old.write(to: drive.appendingPathComponent("ELDEN RING/regulation.bin"))
try! Data("[mod]\r\nx=1\r\n".utf8).write(to: drive.appendingPathComponent("ELDEN RING/YAFSML.ini"))
try! Data("secret".utf8).write(to: outside.appendingPathComponent("secret.txt"))
try! fm.createSymbolicLink(atPath: drive.appendingPathComponent("out").path, withDestinationPath: outside.path)

var logs: [String] = []
let core = DriveBridgeCore(documents: docs, root: { drive }, log: { logs.append($0) })
core.makeFolders()
let bridge = docs.appendingPathComponent("drive-bridge")
func ask(_ id: String, _ json: String, payload: Data? = nil) -> [String: Any] {
    if let payload { try! payload.write(to: bridge.appendingPathComponent("in/\(id)")) }
    try! Data(json.utf8).write(to: bridge.appendingPathComponent("requests/\(id).json"))
    core.pollOnce()
    let r = bridge.appendingPathComponent("responses/\(id).json")
    guard let d = try? Data(contentsOf: r), let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return [:] }
    return o
}

var r = ask("ls1", #"{"op":"ls","path":""}"#)
let names = (r["entries"] as? [[String: Any]] ?? []).compactMap { $0["name"] as? String }
expect(r["ok"] as? Bool == true && names.contains("ELDEN RING"), "ls of the drive's folder")
r = ask("ls2", #"{"op":"ls","path":"ELDEN RING"}"#)
let entries = r["entries"] as? [[String: Any]] ?? []
let reg = entries.first { $0["name"] as? String == "regulation.bin" }
expect((reg?["size"] as? NSNumber)?.intValue == old.count && reg?["dir"] as? Bool == false, "ls lists files with sizes")
r = ask("st", #"{"op":"stat","path":"ELDEN RING\\regulation.bin"}"#)
expect(r["ok"] as? Bool == true && (r["size"] as? NSNumber)?.intValue == old.count, "stat, backslashes accepted")
r = ask("h1", #"{"op":"hash","path":"ELDEN RING/regulation.bin"}"#)
expect(r["sha256"] as? String == sha(old), "hash is the file's SHA-256")
r = ask("p1", #"{"op":"pull","path":"ELDEN RING/regulation.bin"}"#)
let pulled = try? Data(contentsOf: bridge.appendingPathComponent("out/p1"))
expect(r["ok"] as? Bool == true && pulled == old && r["sha256"] as? String == sha(old), "pull copies the file to out/<id>")

let new = Data((0..<200_000).map { UInt8(($0 * 7) % 253) })
r = ask("u1", #"{"op":"push","path":"ELDEN RING/regulation.bin"}"#, payload: new)
let now = try? Data(contentsOf: drive.appendingPathComponent("ELDEN RING/regulation.bin"))
let backupName = r["backup"] as? String ?? ""
let kept = try? Data(contentsOf: bridge.appendingPathComponent("backup/" + backupName))
expect(r["ok"] as? Bool == true && now == new && r["sha256"] as? String == sha(new), "push replaces the file and reads it back")
expect(backupName == "ELDEN RING__regulation.bin." + String(sha(old).prefix(16)) && kept == old, "the replaced file is kept in backup/")
expect(!fm.fileExists(atPath: bridge.appendingPathComponent("in/u1").path), "the pushed copy in in/ is removed after success")
expect(!fm.fileExists(atPath: drive.appendingPathComponent("ELDEN RING/regulation.bin.madeira-new").path), "no temporary file left on the drive")
r = ask("u2", #"{"op":"push","path":"ELDEN RING/regulation.bin"}"#, payload: new)
expect(r["ok"] as? Bool == true && (r["old_sha256"] as? String) == sha(new), "pushing the same contents again works (old = new)")
r = ask("u3", #"{"op":"push","path":"NEW FOLDER/x.bin"}"#, payload: new)
expect(r["ok"] as? Bool == false && !fm.fileExists(atPath: drive.appendingPathComponent("NEW FOLDER").path), "no folder is made")
r = ask("u4", #"{"op":"push","path":"ELDEN RING/missing-payload.bin"}"#)
expect(r["ok"] as? Bool == false && (r["error"] as? String ?? "").contains("in/u4"), "push without in/<id> is refused")

for (id, path) in [("e1", "../outside/secret.txt"), ("e2", "/etc/hosts"), ("e3", "C:/x"), ("e4", "out/secret.txt"), ("e5", "ELDEN RING/./regulation.bin")] {
    r = ask(id, #"{"op":"pull","path":""# + path + #""}"#)
    expect(r["ok"] as? Bool == false && !fm.fileExists(atPath: bridge.appendingPathComponent("out/\(id)").path), "refused: \(path)")
}
r = ask("bad", #"{"op":"rm","path":"ELDEN RING/YAFSML.ini"}"#)
expect(r["ok"] as? Bool == false && fm.fileExists(atPath: drive.appendingPathComponent("ELDEN RING/YAFSML.ini").path), "unknown ops (rm) do nothing")

let noDrive = DriveBridgeCore(documents: docs, root: { nil }, log: { _ in })
try! Data(#"{"op":"ls","path":""}"#.utf8).write(to: bridge.appendingPathComponent("requests/nd.json"))
noDrive.pollOnce()
let nd = (try? JSONSerialization.jsonObject(with: Data(contentsOf: bridge.appendingPathComponent("responses/nd.json")))) as? [String: Any]
expect(nd?["ok"] as? Bool == false && (nd?["error"] as? String ?? "").contains("External drive"), "no drive: answered with the reason")

try! Data("{\"op\":".utf8).write(to: bridge.appendingPathComponent("requests/half.json"))
core.pollOnce()
expect(fm.fileExists(atPath: bridge.appendingPathComponent("requests/half.json").path), "a request still arriving is read again later")
core.pollOnce(now: Date().addingTimeInterval(10))
expect(!fm.fileExists(atPath: bridge.appendingPathComponent("requests/half.json").path) &&
       fm.fileExists(atPath: bridge.appendingPathComponent("responses/half.json").path), "an old malformed request is answered")
try! Data("{}".utf8).write(to: bridge.appendingPathComponent("requests/bad name!.json"))
core.pollOnce()
expect(!fm.fileExists(atPath: bridge.appendingPathComponent("requests/bad name!.json").path), "a malformed id is dropped")
core.pollOnce(now: Date().addingTimeInterval(4000))
expect((try? fm.contentsOfDirectory(atPath: bridge.appendingPathComponent("out").path))?.isEmpty == true, "old pulled copies are swept")
expect(logs.contains { $0.hasPrefix("[drive-bridge] push ELDEN RING/regulation.bin") && $0.contains("backup/") }, "pushes are logged with their backup")
exit(failed == 0 ? 0 : 1)
'''
swiftc = shutil.which('swiftc')
if swiftc and sys.platform == 'darwin':
    work = pathlib.Path(tempfile.mkdtemp(prefix='drive-bridge-'))
    sdir = work / 'swift'
    sdir.mkdir()
    (sdir / 'Stubs.swift').write_text(swift_stubs, encoding='utf-8')
    (sdir / 'main.swift').write_text(swift_main, encoding='utf-8')
    exe = sdir / 'check'
    built = subprocess.run([swiftc, '-O', '-o', str(exe), str(root / 'app/Madeira/DriveBridge.swift'),
                            str(sdir / 'Stubs.swift'), str(sdir / 'main.swift')], capture_output=True, text=True)
    if built.returncode:
        print(built.stderr[-6000:])
        check(False, 'DriveBridge.swift compiles with the stand-ins')
    else:
        run = work / 'run'
        run.mkdir()
        ran = subprocess.run([str(exe), str(run)], capture_output=True, text=True, timeout=120)
        print(ran.stdout, end='')
        if ran.returncode:
            print(ran.stderr[-4000:])
        check(ran.returncode == 0, 'DriveBridge.swift: requests against a folder as the drive')
    shutil.rmtree(work, ignore_errors=True)
elif args.require_swift:
    check(False, 'swiftc on macOS (--require-swift)')
else:
    print('note: the Swift checks run on macOS with swiftc')

print('PASS' if not failed else f'FAIL ({failed})')
sys.exit(1 if failed else 0)
