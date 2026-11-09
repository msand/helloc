# helloc

A "Hello, world!" for macOS that uses no C runtime, no libSystem and no SDK
headers. It brings its own entry points (`start`, `_start`), raw `write`,
`getentropy` and `exit` syscalls in arm64 and x86_64 assembly, and a stack
protector runtime. It can be linked four ways, including with `-static`.

## Requirements

- macOS on Apple Silicon (tested on macOS 14.6.1, `xnu-10063.141.2`)
- Xcode or the Command Line Tools (`clang`, `ld`, `codesign`)
- CMake 3.31+ and Ninja
- Python 3, for the `static-dyld` mode
- Rosetta, for the `static-x86_64` mode

## Build modes

| Mode               | Arch   | Linked with                                                  | Entered at                                 | Runs on stock macOS             |
|--------------------|--------|--------------------------------------------------------------|--------------------------------------------|---------------------------------|
| `nolibs` (default) | arm64  | `-nostdlib -e _start`                                        | `_start`, by dyld via `LC_MAIN`            | yes                             |
| `static`           | arm64  | `-nostdlib -static -e start`                                 | `start`, by the kernel via `LC_UNIXTHREAD` | no, killed at launch (exit 137) |
| `static-x86_64`    | x86_64 | `-nostdlib -static -e start`                                 | `start`, by the kernel via `LC_UNIXTHREAD` | yes, under Rosetta              |
| `static-dyld`      | arm64  | `-nostdlib -static -pie -e _start`, then `static_to_dyld.py` | `_start`, by dyld via `LC_MAIN`            | yes                             |

None of them link a dylib: `otool -L` lists nothing. In the `nolibs` and
`static-dyld` modes dyld still maps the executable, but it loads no libraries.

## Building and testing

Each mode has a CMake preset that builds into `build/<mode>/`:

```sh
cmake --workflow --preset nolibs    # configure, build and test
build/nolibs/helloc
```

Step by step: `cmake --preset <mode>`, `cmake --build --preset <mode>`,
`ctest --preset <mode>`. CLion lists the presets as CMake profiles. Without
presets, choose the mode with `-DHELLOC_MODE=<mode>`.

| Test                     | Checks                                                                                                                         |
|--------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| `prints_hello`           | `helloc` prints exactly `Hello, world!\n` and exits with 0                                                                     |
| `fails_on_closed_stdout` | `helloc` exits with 1 when stdout is closed, so `write` fails                                                                  |
| `rebases_pointers`       | an address stored in data is correct after ASLR slides the image (the static builds aren't slid, so there it passes trivially) |
| `seeds_stack_guard`      | the stack protector's guard was seeded before `main`                                                                           |
| `catches_stack_overflow` | overflowing a local array exits with 134                                                                                       |
| `passes_main_arguments`  | `main` gets the right `argc`, `argv`, `envp` and `apple`, on an aligned stack                                                  |
| `enters_through_start`   | the same, when entered through `start` from a stack laid out like the kernel's                                                 |
| `static_layout`          | the executable is static and starts at `start` (`static` and `static-x86_64` modes only)                                       |
| `static_to_dyld_unit`    | `static_to_dyld.py`'s output and refusals, on synthetic Mach-O files (`static-dyld` mode only)                                 |

The tests from `rebases_pointers` to `enters_through_start` use `helloc_selftest`
(`tests/selftest.c`), which is built in the same mode as `helloc`.

In the `static` mode only `static_layout` runs: the programs can't run on a stock
kernel. `start` is still covered there, because `enters_through_start` runs the
same code in the other arm64 modes: it lays out argc, argv, envp and apple the way
the kernel does, 8 bytes off 16-byte alignment, and jumps to `start`.

The same tests run on GitHub Actions (`.github/workflows/ci.yml`) for every mode,
on the macOS 15 and macOS 26 runners.

## How it works

### Entry points

- **`start`** (`syscalls.s`, `syscalls_x86_64.s`) is used by the static builds.
  The kernel jumps here with the stack pointer at `argc`, followed by `argv[]`,
  NULL, `envp[]`, NULL, `apple[]`, NULL (described in `syscalls.s`). It
  computes the four pointers and aligns the stack.
- **`_start`** (`syscalls.s`) is used when dyld loads the executable. dyld calls
  the `LC_MAIN` entry like `main(argc, argv, envp, apple)`, so the four pointers
  are already in place. On arm64, `start` continues here.

Both then seed the stack protector, call `main` and exit through the `exit`
syscall with its return value.

### Syscalls

`write`, `getentropy` and `exit` put the BSD syscall number (class `0x2000000`
plus the number) in `x16` and use `svc #0x80` on arm64, or in `eax` and use
`syscall` on x86_64. On failure the kernel sets the carry flag and returns the
error number. The wrappers turn that into `-1`, but `errno` isn't stored.

They're declared in `syscalls.h`. C code is compiled with `-ffreestanding
-nostdlibinc`, so only the compiler's own headers (such as `<stddef.h>`) can be
included, not the SDK's.

### Stack protector

clang adds a canary to functions with local arrays of 8 bytes or more by default,
even with `-ffreestanding`. It copies `__stack_chk_guard` into the stack frame and calls
`__stack_chk_fail` on return if the copy changed. libc normally provides both;
here `stack_protector.c` does:

- The entry points call `__guard_setup` before `main`, which fills the guard from
  `getentropy` and clears its second byte, as the kernel does for its own value.
  The kernel also passes a random `stack_guard=` string in `apple[]`, but dyld
  blanks it before calling the entry point, so only the static modes could use it.
- `__stack_chk_fail` exits with 134, the status a shell shows for `abort()`, which
  libc's version calls.

### Why a static arm64 executable doesn't run

The kernel decides by the `MH_DYLDLINK` flag in the Mach-O header
([`mach_loader.c#L864-L881`](https://github.com/apple-oss-distributions/xnu/blob/xnu-10063.141.1/bsd/kern/mach_loader.c#L864-L881)):

- **Flag set (dynamic):** on arm64 the executable must also be PIE (`MH_PIE`),
  and it must name a dynamic linker with `LC_LOAD_DYLINKER`. The kernel loads
  dyld and starts the process there.
- **Flag clear (static):** allowed only for x86_64, for Rosetta's runtime, or on
  a DEVELOPMENT/DEBUG kernel. Otherwise exec fails and the process gets SIGKILL.

Without dyld, the kernel doesn't push the `mach_header` address onto the stack
([`kern_exec.c#L1825`](https://github.com/apple-oss-distributions/xnu/blob/xnu-10063.141.1/bsd/kern/kern_exec.c#L1825)),
so a static executable starts with the stack pointer at `argc`, an `int` stored
in a pointer-sized slot
([`kern_exec.c#L5788-L5789`](https://github.com/apple-oss-distributions/xnu/blob/xnu-10063.141.1/bsd/kern/kern_exec.c#L5788-L5789)).
Rosetta presents x86_64 code with the same layout.

### `static-dyld`: converting a static link

Setting `MH_DYLDLINK`, `MH_PIE` and a dyld path on an ordinary `-static` link
isn't enough. When tested, dyld crashed while rebasing the image, because a static
link keeps no record of the absolute pointers that move when ASLR slides it. dyld
on macOS 14.6 also refuses an `LC_UNIXTHREAD` entry and requires `LC_MAIN`.

So this mode links with `-static -pie`, which keeps that record as local
relocations in `LC_DYSYMTAB`, and with `-e _start`. Then `static_to_dyld.py`:

1. sets `MH_DYLDLINK` (it refuses input that isn't already `MH_PIE`),
2. replaces `LC_UNIXTHREAD` with `LC_MAIN` at the same entry point,
3. adds `LC_LOAD_DYLINKER /usr/lib/dyld`,
4. adds `LC_BUILD_VERSION`, copied from the object files (a static link leaves it out).

The build then signs the result with `codesign --sign -`, and redoes both when
the script changes. To run the converter by hand:

```sh
static_to_dyld.py EXECUTABLE [--build-version-from OBJECT...]
```

## Inspecting the binaries

```sh
otool -hv build/<mode>/helloc                    # header flags, e.g. DYLDLINK PIE
otool -l  build/<mode>/helloc | grep 'cmd LC_'   # load commands
otool -L  build/<mode>/helloc                    # linked libraries (none)
nm -u     build/<mode>/helloc                    # undefined symbols (none)
codesign -dv build/<mode>/helloc                 # arm64 code must be signed to run
```

## Limitations

- There's no libc: only `write`, `getentropy` and `exit` exist, and `errno` isn't set.
- `static` builds a correct static executable that release kernels won't run.
  Running it needs a DEVELOPMENT/DEBUG kernel, which hasn't been tested.
- `static-x86_64` depends on Rosetta, which Apple has announced it will phase out.
- `static-dyld` relies on dyld accepting a hand-converted executable. It has
  been tested with the dyld in macOS 14.6.1.

## Files

| File                              | Purpose                                                             |
|-----------------------------------|---------------------------------------------------------------------|
| `main.c`                          | The program, including its own `strlen`                             |
| `syscalls.h`                      | Declarations of the syscall wrappers, instead of `<unistd.h>`       |
| `syscalls.s`                      | arm64 entry points (`start`, `_start`) and syscall wrappers         |
| `syscalls_x86_64.s`               | x86_64 entry point and syscall wrappers                             |
| `stack_protector.c`               | Stack protector guard, its seeding and the failure handler          |
| `static_to_dyld.py`               | Converts a static-PIE arm64 link for dyld (`static-dyld` mode)      |
| `CMakeLists.txt`                  | Build modes (`HELLOC_MODE`) and tests                               |
| `CMakePresets.json`               | One configure/build/test/workflow preset per mode                   |
| `tests/check_output.cmake`        | Checks the output and exit code of `helloc`                         |
| `tests/check_static_layout.cmake` | Checks that a static build is static and starts at `start`          |
| `tests/selftest.c`                | `helloc_selftest`: rebasing, stack protector and entry point checks |
| `tests/test_static_to_dyld.py`    | Unit tests for `static_to_dyld.py`                                  |
| `.github/workflows/ci.yml`        | Builds and tests every mode on GitHub Actions                       |
| `LICENSE`                         | MIT license                                                         |

## License

MIT, see `LICENSE`.
