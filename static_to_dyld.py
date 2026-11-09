#!/usr/bin/env python3
"""Convert a static-PIE arm64 Mach-O executable into one that dyld loads.

Release macOS kernels refuse static arm64 executables (xnu mach_loader.c), so
this rewrites the output of `ld -static -pie -e <entry>` into a form the kernel
and dyld accept, without listing any dylib (dyld still loads libSystem, as it
does for every executable that lists none):

  * set MH_DYLDLINK in the header (MH_PIE must already be set, by -pie)
  * replace LC_UNIXTHREAD with LC_MAIN pointing at the same entry point
  * add LC_LOAD_DYLINKER /usr/lib/dyld
  * add LC_BUILD_VERSION (platform, minimum OS, SDK), which a -static link
    leaves out, copied from the object files given with --build-version-from

--sdk-version records a different SDK version in that LC_BUILD_VERSION. macOS
26's dyld refuses an executable that lists no dylib only if its SDK version is
15.4 or newer, so recording an older one gets it past that check.

-pie makes ld keep the rebase information (local relocations in LC_DYSYMTAB)
that dyld needs once ASLR slides the image. dyld calls the LC_MAIN entry like
main(argc, argv, envp, apple), so the entry point must not expect the kernel's
static-exec stack layout. The file is rewritten in place and must be signed
afterwards (codesign -s -).

Usage: static_to_dyld.py EXECUTABLE [--sdk-version X.Y[.Z]] [--build-version-from OBJECT...]
"""
import argparse
import os
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
MH_EXECUTE = 0x2
MH_DYLDLINK = 0x4
MH_PIE = 0x200000
CPU_TYPE_ARM64 = 0x0100000C
LC_UNIXTHREAD = 0x5
LC_DYSYMTAB = 0xB
LC_LOAD_DYLINKER = 0xE
LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
LC_BUILD_VERSION = 0x32
LC_MAIN = 0x80000028
ARM_THREAD_STATE64 = 6
ARM_THREAD_STATE64_COUNT = 68  # 32-bit words: x0-x28, fp, lr, sp, pc, cpsr, pad
DYLD_PATH = b"/usr/lib/dyld"
PLATFORM_NAMES = {1: "macOS"}

HEADER = struct.Struct("<IiiIIIII")  # magic cputype cpusubtype filetype ncmds sizeofcmds flags reserved


def fail(msg):
    sys.exit(f"static_to_dyld: {msg}")


def parse(data, path):
    """Return the Mach-O header fields and the load commands as (cmd, bytes) pairs."""
    if len(data) < HEADER.size or struct.unpack_from("<I", data)[0] != MH_MAGIC_64:
        fail(f"{path}: not a 64-bit Mach-O file")
    header = HEADER.unpack_from(data)
    end = HEADER.size + header[5]
    if end > len(data):
        fail(f"{path}: load commands run past the end of the file")
    cmds = []
    off = HEADER.size
    for _ in range(header[4]):
        cmd, cmdsize = struct.unpack_from("<II", data, off) if off + 8 <= end else (0, 0)
        if cmdsize < 8 or off + cmdsize > end:
            fail(f"{path}: malformed load command at offset {off}")
        cmds.append((cmd, bytes(data[off:off + cmdsize])))
        off += cmdsize
    return header, cmds


def version_str(v):
    return f"{v >> 16}.{(v >> 8) & 0xFF}" + (f".{v & 0xFF}" if v & 0xFF else "")


def parse_version(text):
    """'X.Y' or 'X.Y.Z' packed as in LC_BUILD_VERSION: X in 16 bits, Y and Z in 8 each."""
    parts = text.split(".")
    if not 2 <= len(parts) <= 3 or not all(p.isascii() and p.isdigit() for p in parts):
        raise argparse.ArgumentTypeError(f"invalid version '{text}': use X.Y or X.Y.Z")
    major, minor, patch = (int(p) for p in parts + ["0"] * (3 - len(parts)))
    if major > 0xFFFF or minor > 0xFF or patch > 0xFF:
        raise argparse.ArgumentTypeError(f"version '{text}' is out of range")
    return major << 16 | minor << 8 | patch


def read_build_version(paths):
    """Platform, minimum OS and SDK version recorded by the compiler in the object files.

    Platforms must agree; the highest minimum OS and SDK win (an assembler object
    records no SDK version).
    """
    platforms, minos, sdk = set(), 0, 0
    for path in paths:
        with open(path, "rb") as f:
            _, cmds = parse(f.read(), path)
        for cmd, body in cmds:
            if cmd == LC_BUILD_VERSION:
                platform, obj_minos, obj_sdk = struct.unpack_from("<III", body, 8)
                platforms.add(platform)
                minos, sdk = max(minos, obj_minos), max(sdk, obj_sdk)
    if not platforms:
        fail("no LC_BUILD_VERSION in the --build-version-from files")
    if len(platforms) > 1:
        fail("the --build-version-from files were built for different platforms")
    return platforms.pop(), minos, sdk


def convert(data, path, build_version):
    """Rewrite data in place; return a short description of what changed."""
    (magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved), cmds = parse(data, path)
    if cputype != CPU_TYPE_ARM64 or filetype != MH_EXECUTE:
        fail(f"{path}: not an arm64 executable")
    if flags & MH_DYLDLINK:
        fail(f"{path}: already dyld-linked")
    if not flags & MH_PIE:
        fail(f"{path}: not PIE; link with -static -pie so ld keeps the rebase information")

    kinds = [cmd for cmd, _ in cmds]
    if LC_CODE_SIGNATURE in kinds:
        fail(f"{path}: already signed; link without signing and sign after converting")
    if LC_DYSYMTAB not in kinds:
        fail(f"{path}: no LC_DYSYMTAB, so no rebase information")
    if kinds.count(LC_UNIXTHREAD) != 1:
        fail(f"{path}: expected exactly one LC_UNIXTHREAD")

    # __TEXT bounds (LC_MAIN's entryoff is relative to it) and where section data starts.
    text = None
    first_section = len(data)
    for cmd, body in cmds:
        if cmd != LC_SEGMENT_64:
            continue
        segname = body[8:24].rstrip(b"\0")
        vmaddr, vmsize, fileoff = struct.unpack_from("<QQQ", body, 24)
        if segname == b"__TEXT":
            text = (vmaddr, vmsize, fileoff)
        nsects = struct.unpack_from("<I", body, 64)[0]
        for i in range(nsects):
            sect_offset = struct.unpack_from("<I", body, 72 + i * 80 + 48)[0]
            if sect_offset:
                first_section = min(first_section, sect_offset)
    if text is None or text[2] != 0:
        fail(f"{path}: expected a __TEXT segment at file offset 0")

    new_cmds = []
    for cmd, body in cmds:
        if cmd == LC_UNIXTHREAD:
            flavor, count = struct.unpack_from("<II", body, 8)
            if flavor != ARM_THREAD_STATE64 or count != ARM_THREAD_STATE64_COUNT:
                fail(f"{path}: LC_UNIXTHREAD is not an ARM_THREAD_STATE64")
            pc = struct.unpack_from("<Q", body, 16 + 32 * 8)[0]
            if not text[0] <= pc < text[0] + text[1]:
                fail(f"{path}: entry point 0x{pc:x} is outside __TEXT")
            entryoff = pc - text[0]
            # entry_point_command: cmd, cmdsize, entryoff, stacksize (0 = default)
            body = struct.pack("<IIQQ", LC_MAIN, 24, entryoff, 0)
        new_cmds.append(body)
    changes = [f"LC_UNIXTHREAD -> LC_MAIN (entryoff 0x{entryoff:x})"]

    name = DYLD_PATH + b"\0"
    cmdsize = (12 + len(name) + 7) & ~7
    new_cmds.append(struct.pack("<III", LC_LOAD_DYLINKER, cmdsize, 12) + name.ljust(cmdsize - 12, b"\0"))
    changes.append("added LC_LOAD_DYLINKER")

    if build_version and LC_BUILD_VERSION not in kinds:
        platform, minos, sdk = build_version
        # build_version_command: cmd, cmdsize, platform, minos, sdk, ntools
        new_cmds.append(struct.pack("<IIIIII", LC_BUILD_VERSION, 24, platform, minos, sdk, 0))
        changes.append(f"added LC_BUILD_VERSION ({PLATFORM_NAMES.get(platform, f'platform {platform}')} "
                       f"{version_str(minos)}, SDK {version_str(sdk)})")

    blob = b"".join(new_cmds)
    if HEADER.size + len(blob) > first_section:
        fail(f"{path}: not enough header padding for the new load commands")
    data[HEADER.size:HEADER.size + max(sizeofcmds, len(blob))] = bytes(max(sizeofcmds, len(blob)))
    data[HEADER.size:HEADER.size + len(blob)] = blob
    HEADER.pack_into(data, 0, magic, cputype, cpusubtype, filetype,
                     len(new_cmds), len(blob), flags | MH_DYLDLINK, reserved)
    changes.append("set MH_DYLDLINK")
    return ", ".join(changes)


def main():
    parser = argparse.ArgumentParser(
        description="Convert a static-PIE arm64 executable (ld -static -pie) into one that dyld loads.")
    parser.add_argument("executable", help="linked with -static -pie, unsigned; rewritten in place")
    parser.add_argument("--build-version-from", nargs="+", default=[], metavar="OBJECT",
                        help="object files to copy LC_BUILD_VERSION (platform, minimum OS, SDK) from")
    parser.add_argument("--sdk-version", type=parse_version, metavar="X.Y[.Z]",
                        help="SDK version to record instead of the object files' one")
    args = parser.parse_args()
    if args.sdk_version is not None and not args.build_version_from:
        parser.error("--sdk-version needs --build-version-from")

    path = args.executable
    try:
        build_version = read_build_version(args.build_version_from) if args.build_version_from else None
        if build_version and args.sdk_version is not None:
            platform, minos, _ = build_version
            build_version = (platform, minos, args.sdk_version)
        with open(path, "rb") as f:
            data = bytearray(f.read())
        changes = convert(data, path, build_version)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.chmod(tmp, os.stat(path).st_mode)
        os.replace(tmp, path)
    except struct.error as e:  # a load command too short for the fields it should hold
        fail(f"malformed Mach-O file: {e}")
    except OSError as e:  # such as a missing input file
        fail(f"{e.filename}: {e.strerror}" if e.filename else e)
    print(f"static_to_dyld: {path}: {changes}")


if __name__ == "__main__":
    main()
