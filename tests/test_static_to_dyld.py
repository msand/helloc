#!/usr/bin/env python3
"""Unit tests for static_to_dyld.py, using small synthetic Mach-O files.

The real conversion is covered by building and running helloc in the static-dyld
mode; these cover the refusals and the exact load commands written.
"""
import os
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "static_to_dyld.py")
sys.dont_write_bytecode = True  # don't leave __pycache__ in the source tree
sys.path.insert(0, ROOT)
import static_to_dyld as s2d  # noqa: E402

CPU_TYPE_X86_64 = 0x01000007
MH_OBJECT = 0x1
MH_NOUNDEFS = 0x1
TEXT_VMADDR = 0x100000000
PC = TEXT_VMADDR + 0x1F00
SECTION_OFFSET = 0x1000
BUILD_VERSION = (1, 14 << 16, 15 << 16 | 2 << 8)  # macOS 14.0, SDK 15.2


def segment(name=b"__TEXT", fileoff=0, section_offset=SECTION_OFFSET):
    sect = struct.pack("<16s16sQQIIIIIIII", b"__text", name, TEXT_VMADDR + section_offset, 16,
                       section_offset, 2, 0, 0, 0x80000400, 0, 0, 0)
    return struct.pack("<II16sQQQQiiII", s2d.LC_SEGMENT_64, 72 + len(sect), name, TEXT_VMADDR,
                       0x4000, fileoff, 0x4000, 5, 5, 1, 0) + sect


def dysymtab():
    return struct.pack("<II", s2d.LC_DYSYMTAB, 80) + bytes(72)


def unixthread(pc=PC, flavor=s2d.ARM_THREAD_STATE64):
    state = bytearray(s2d.ARM_THREAD_STATE64_COUNT * 4)
    struct.pack_into("<Q", state, 32 * 8, pc)
    return struct.pack("<IIII", s2d.LC_UNIXTHREAD, 16 + len(state), flavor,
                       s2d.ARM_THREAD_STATE64_COUNT) + state


def build_version_cmd(platform, minos, sdk):
    return struct.pack("<IIIIII", s2d.LC_BUILD_VERSION, 24, platform, minos, sdk, 0)


def macho(cmds=None, cputype=s2d.CPU_TYPE_ARM64, filetype=s2d.MH_EXECUTE,
          flags=MH_NOUNDEFS | s2d.MH_PIE):
    """A Mach-O file: header, load commands, padding, then 16 bytes of 'code'."""
    if cmds is None:
        cmds = [segment(), dysymtab(), unixthread()]
    blob = b"".join(cmds)
    header = s2d.HEADER.pack(s2d.MH_MAGIC_64, cputype, 0, filetype, len(cmds), len(blob), flags, 0)
    data = bytearray(header + blob)
    data += bytes(SECTION_OFFSET - len(data)) + b"\xAA" * 16
    return data


class ConvertTest(unittest.TestCase):
    def convert(self, data, build_version=BUILD_VERSION):
        return s2d.convert(data, "test", build_version)

    def assertRefused(self, data, message, build_version=BUILD_VERSION):
        with self.assertRaises(SystemExit) as cm:
            self.convert(data, build_version)
        self.assertIn(message, str(cm.exception.code))

    def test_writes_expected_load_commands(self):
        data = macho()
        original = bytes(data)
        summary = self.convert(data)

        header, cmds = s2d.parse(data, "test")
        self.assertEqual(header[6], MH_NOUNDEFS | s2d.MH_PIE | s2d.MH_DYLDLINK)
        self.assertEqual([cmd for cmd, _ in cmds], [s2d.LC_SEGMENT_64, s2d.LC_DYSYMTAB, s2d.LC_MAIN,
                                                    s2d.LC_LOAD_DYLINKER, s2d.LC_BUILD_VERSION])
        self.assertEqual(header[4], len(cmds))
        self.assertEqual(header[5], sum(len(body) for _, body in cmds))

        main = dict(cmds)[s2d.LC_MAIN]
        self.assertEqual(struct.unpack("<IIQQ", main), (s2d.LC_MAIN, 24, PC - TEXT_VMADDR, 0))
        dylinker = dict(cmds)[s2d.LC_LOAD_DYLINKER]
        self.assertEqual(len(dylinker) % 8, 0)
        name_offset = struct.unpack_from("<I", dylinker, 8)[0]
        self.assertEqual(dylinker[name_offset:].rstrip(b"\0"), b"/usr/lib/dyld")
        self.assertEqual(dict(cmds)[s2d.LC_BUILD_VERSION], build_version_cmd(*BUILD_VERSION))

        # The leftover space of the old, longer load commands is zeroed, and nothing
        # after the header padding changes.
        end = s2d.HEADER.size + header[5]
        self.assertEqual(data[end:SECTION_OFFSET], bytes(SECTION_OFFSET - end))
        self.assertEqual(data[SECTION_OFFSET:], original[SECTION_OFFSET:])
        self.assertEqual(summary, f"LC_UNIXTHREAD -> LC_MAIN (entryoff 0x{PC - TEXT_VMADDR:x}), "
                                  "added LC_LOAD_DYLINKER, added LC_BUILD_VERSION (macOS 14.0, SDK 15.2), "
                                  "set MH_DYLDLINK")

    def test_keeps_existing_build_version(self):
        existing = build_version_cmd(1, 13 << 16, 14 << 16)
        data = macho([segment(), dysymtab(), unixthread(), existing])
        self.convert(data)
        _, cmds = s2d.parse(data, "test")
        self.assertEqual([body for cmd, body in cmds if cmd == s2d.LC_BUILD_VERSION], [existing])

    def test_without_build_version(self):
        data = macho()
        self.convert(data, build_version=None)
        _, cmds = s2d.parse(data, "test")
        self.assertNotIn(s2d.LC_BUILD_VERSION, [cmd for cmd, _ in cmds])

    def test_refuses_non_macho(self):
        self.assertRefused(bytearray(b"\x7fELF" + bytes(60)), "not a 64-bit Mach-O file")

    def test_refuses_truncated_header(self):
        self.assertRefused(macho()[:16], "not a 64-bit Mach-O file")

    def test_refuses_load_commands_past_end(self):
        data = macho()
        struct.pack_into("<I", data, 20, len(data))  # sizeofcmds
        self.assertRefused(data, "load commands run past the end of the file")

    def test_refuses_bad_cmdsize(self):
        data = macho()
        struct.pack_into("<I", data, s2d.HEADER.size + 4, 4)  # first command's cmdsize
        self.assertRefused(data, "malformed load command at offset 32")

    def test_refuses_x86_64(self):
        self.assertRefused(macho(cputype=CPU_TYPE_X86_64), "not an arm64 executable")

    def test_refuses_object_file(self):
        self.assertRefused(macho(filetype=MH_OBJECT), "not an arm64 executable")

    def test_refuses_converted_file(self):
        data = macho()
        self.convert(data)
        self.assertRefused(data, "already dyld-linked")

    def test_refuses_non_pie(self):
        self.assertRefused(macho(flags=MH_NOUNDEFS), "not PIE")

    def test_refuses_signed(self):
        signature = struct.pack("<IIII", s2d.LC_CODE_SIGNATURE, 16, 0, 0)
        self.assertRefused(macho([segment(), dysymtab(), unixthread(), signature]), "already signed")

    def test_refuses_missing_dysymtab(self):
        self.assertRefused(macho([segment(), unixthread()]), "no LC_DYSYMTAB")

    def test_refuses_missing_unixthread(self):
        self.assertRefused(macho([segment(), dysymtab()]), "expected exactly one LC_UNIXTHREAD")

    def test_refuses_other_thread_state(self):
        self.assertRefused(macho([segment(), dysymtab(), unixthread(flavor=4)]),
                           "not an ARM_THREAD_STATE64")

    def test_refuses_entry_outside_text(self):
        self.assertRefused(macho([segment(), dysymtab(), unixthread(pc=TEXT_VMADDR + 0x4000)]),
                           "entry point 0x100004000 is outside __TEXT")

    def test_refuses_missing_text(self):
        self.assertRefused(macho([segment(name=b"__DATA"), dysymtab(), unixthread()]),
                           "expected a __TEXT segment at file offset 0")


class ReadBuildVersionTest(unittest.TestCase):
    def objects(self, *cmd_lists):
        paths = []
        for cmds in cmd_lists:
            f = tempfile.NamedTemporaryFile(suffix=".o", delete=False)
            self.addCleanup(os.unlink, f.name)
            f.write(macho(cmds, filetype=MH_OBJECT))
            f.close()
            paths.append(f.name)
        return paths

    def test_takes_highest_versions(self):
        paths = self.objects([build_version_cmd(1, 14 << 16, 0)],  # assembler: no SDK version
                             [build_version_cmd(1, 13 << 16, 15 << 16 | 2 << 8)])
        self.assertEqual(s2d.read_build_version(paths), (1, 14 << 16, 15 << 16 | 2 << 8))

    def test_refuses_mixed_platforms(self):
        paths = self.objects([build_version_cmd(1, 14 << 16, 0)], [build_version_cmd(2, 17 << 16, 0)])
        with self.assertRaises(SystemExit) as cm:
            s2d.read_build_version(paths)
        self.assertIn("built for different platforms", str(cm.exception.code))

    def test_refuses_missing(self):
        paths = self.objects([dysymtab()])
        with self.assertRaises(SystemExit) as cm:
            s2d.read_build_version(paths)
        self.assertIn("no LC_BUILD_VERSION", str(cm.exception.code))


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "exe")

    def run_script(self, data, mode=0o755):
        with open(self.path, "wb") as f:
            f.write(data)
        os.chmod(self.path, mode)
        return subprocess.run([sys.executable, "-B", SCRIPT, self.path], capture_output=True, text=True)

    def test_converts_in_place_and_keeps_mode(self):
        result = self.run_script(macho(), mode=0o751)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("set MH_DYLDLINK", result.stdout)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o751)
        with open(self.path, "rb") as f:
            self.assertTrue(struct.unpack_from("<I", f.read(), 24)[0] & s2d.MH_DYLDLINK)
        self.assertEqual(os.listdir(self.dir.name), ["exe"])

    def test_reports_short_load_command(self):
        # An LC_UNIXTHREAD without room for the thread state it declares
        data = macho([segment(), dysymtab(), struct.pack("<IIII", s2d.LC_UNIXTHREAD, 16, 6, 68)])
        original = bytes(data)
        result = self.run_script(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("static_to_dyld: malformed Mach-O file", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(os.listdir(self.dir.name), ["exe"])


if __name__ == "__main__":
    unittest.main()
