# helloc

A "Hello, world!" for macOS that calls no C runtime or libc and uses no SDK
headers. It brings its own entry points (`start`, `_start`), raw `write`,
`getentropy` and `exit` syscalls in arm64 and x86_64 assembly, and a stack
protector runtime. It can be linked five ways, including with `-static`. In the
two static ones the process loads no library at all; dyld loads libSystem into
the others, although nothing in it is called. Which of them work depends on the
macOS and Xcode versions (see [Where it works](#where-it-works)).

## Requirements

- macOS on Apple Silicon (see [Where it works](#where-it-works) for versions)
- Xcode or the Command Line Tools (`clang`, `ld`, `codesign`)
- CMake 3.31+ and Ninja
- Python 3, for the `static-dyld` mode
- Rosetta, for the default `static-x86_64` mode. Configuring warns if it's missing;
  install it with `softwareupdate --install-rosetta --agree-to-license`

## Build modes

| Mode                      | Arch   | Linked with                                                  | Entered at                                 |
|---------------------------|--------|--------------------------------------------------------------|--------------------------------------------|
| `nolibs`                  | arm64  | `-nostdlib -e _start`                                        | `_start`, by dyld via `LC_MAIN`            |
| `libsystem`               | arm64  | `-nostdlib -lSystem -e _start`                               | `_start`, by dyld via `LC_MAIN`            |
| `static`                  | arm64  | `-nostdlib -static -e start`                                 | `start`, by the kernel via `LC_UNIXTHREAD` |
| `static-x86_64` (default) | x86_64 | `-nostdlib -static -e start`                                 | `start`, by the kernel via `LC_UNIXTHREAD` |
| `static-dyld`             | arm64  | `-nostdlib -static -pie -e _start`, then `static_to_dyld.py` | `_start`, by dyld via `LC_MAIN`            |

The default is `static-x86_64`: it's a real `-static` executable, with no
conversion or extra dependency, and it runs on every macOS version tested, under
Rosetta.

No mode imports a symbol (`nm -u` lists nothing), so nothing in any library is
called. Only `libsystem` lists a dylib, libSystem, which newer linkers and dyld
versions require. But in all three modes that dyld starts, it loads and
initializes libSystem and the system libraries it's built from (about 40 on
macOS 14.6.1):

- dyld treats an executable that lists no dylib as linking libSystem
  ([`UnsafeHeader.cpp#L1406`](https://github.com/apple-oss-distributions/dyld/blob/dyld-1378/mach_o/UnsafeHeader.cpp#L1406)).
- dyld can't start a program without it: it stops with `libdyld.dylib not found`
  ([`dyldMain.cpp#L857`](https://github.com/apple-oss-distributions/dyld/blob/dyld-1378/dyld/dyldMain.cpp#L857)).
  That's what happens with a weak link to a dylib that doesn't exist, which
  stops dyld from adding libSystem.

So `nolibs` and `static-dyld` differ from `libsystem` in what the executable
lists, not in what the process loads. Only the static modes, which the kernel
starts without dyld, run with no library loaded. The `loaded_libraries` test
checks both with `DYLD_PRINT_LIBRARIES`.

## Where it works

| Mode            | macOS 14.6.1, Xcode 16.2              | macOS 15.8.2, Xcode 16.4 | macOS 26.7.2, Xcode 26.6 |
|-----------------|---------------------------------------|--------------------------|--------------------------|
| `nolibs`        | works                                 | the linker refuses it    | the linker refuses it    |
| `libsystem`     | works                                 | works                    | works                    |
| `static`        | builds; the kernel kills it at launch | builds                   | builds                   |
| `static-x86_64` | works, under Rosetta                  | works, under Rosetta     | works, under Rosetta     |
| `static-dyld`   | works                                 | works                    | works                    |

The first column was tested by hand. The others are the GitHub Actions runners,
which check this table for pull requests and pushes to `main` (see [Building and testing](#building-and-testing)).
There, the `static` binary is only checked by `static_layout`, not run.

The limits:

- **`nolibs` needs the linker from Xcode 16.2 or older.** Xcode 16.4 and 26.6
  refuse to link an executable for dyld that doesn't list libSystem:
  `ld: dynamic executables or dylibs must link with libSystem.dylib`. The versions
  in between haven't been tested. Recording an older SDK version, which gets
  `static-dyld` past macOS 26's dyld (below), doesn't help here: those linkers
  refuse `nolibs` even with `-platform_version` recording SDK 15.0. A `nolibs`
  binary built with Xcode 16.2 records SDK 15.2, so macOS 26's dyld should accept
  it, but that hasn't been tested.
- **`static-dyld` runs on macOS 26 only because it records SDK 15.0.** macOS 26's
  dyld refuses an executable that lists no dylib (`dyld[...]: missing LC_LOAD_DYLIB
  (must link with at least libSystem.dylib)`), but only if it records SDK 15.4 or
  newer
  ([`Policy.cpp#L440-L443`](https://github.com/apple-oss-distributions/dyld/blob/dyld-1378/mach_o/Policy.cpp#L440-L443)),
  to keep older binaries working. So `static_to_dyld.py` records SDK 15.0 instead
  of the real one. A later macOS could drop that exemption. The linker check above
  doesn't apply, because the link itself is `-static`.
- **`static` doesn't run on any release kernel.** It needs a DEVELOPMENT/DEBUG
  kernel, which hasn't been tested.
- **`static-x86_64` needs Rosetta**, which Apple has announced it will phase out.
- **`libsystem` is the only arm64 mode that runs everywhere without a workaround.**
  It loads nothing the other dyld modes don't: dyld loads libSystem into all of them.

## Building and testing

Each mode has a CMake preset that builds into `build/<mode>/`:

```sh
cmake --workflow --preset static-x86_64    # configure, build and test
build/static-x86_64/helloc
```

Step by step: `cmake --preset <mode>`, `cmake --build --preset <mode>`,
`ctest --preset <mode>`. CLion lists the presets as CMake profiles. Without
presets, choose the mode with `-DHELLOC_MODE=<mode>` (`static-x86_64` if not set).
The binaries are built for macOS 14.0 and later; `-DCMAKE_OSX_DEPLOYMENT_TARGET`
changes that.

| Test                     | Checks                                                                                                                         |
|--------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| `prints_hello`           | `helloc` prints exactly `Hello, world!\n` and exits with 0                                                                     |
| `fails_on_closed_stdout` | `helloc` exits with 1 when stdout is closed, so `write` fails                                                                  |
| `rebases_pointers`       | an address stored in data is correct after ASLR slides the image (the static builds aren't slid, so there it passes trivially) |
| `seeds_stack_guard`      | the stack protector's guard was seeded before `main`                                                                           |
| `catches_stack_overflow` | overflowing a local array makes the program exit with 134 (a SIGABRT, which a shell also reports as 134, fails it)                    |
| `passes_main_arguments`  | `main` gets the right `argc`, `argv`, `envp` and `apple`, on an aligned stack                                                  |
| `enters_through_start`   | the same, when entered through `start` from a stack laid out like the kernel's                                                 |
| `static_layout`          | the executable is static and starts at `start` (`static` and `static-x86_64` modes only)                                       |
| `imports_nothing`        | the executable imports no symbols and lists no dylib except libSystem in `libsystem` (modes that dyld loads)                    |
| `loaded_libraries`       | with `DYLD_PRINT_LIBRARIES`, dyld loads libSystem in the modes it starts, and nothing is loaded in `static-x86_64`             |
| `static_to_dyld_unit`    | `static_to_dyld.py`'s output and refusals, on synthetic Mach-O files (`static-dyld` mode only)                                 |

The tests from `rebases_pointers` to `enters_through_start` use `helloc_selftest`
(`tests/selftest.c`), which is built in the same mode as `helloc`. Each test fails
if it runs for more than 60 seconds.

In the `static` mode only `static_layout` runs: the programs can't run on a stock
kernel. `start` is still covered there, because `enters_through_start` runs the
same code in the other arm64 modes: it lays out argc, argv, envp and apple the way
the kernel does, 8 bytes off 16-byte alignment, and jumps to `start`.

GitHub Actions (`.github/workflows/ci.yml`) runs the same tests for every mode on
the macOS 15 and macOS 26 runners, for each pull request and each push to `main`.
Other branches can be run by hand with `gh workflow run CI --ref <branch>`. A new
push cancels the run still going for the same branch or pull request, so only the
latest commit is sure to be checked. The combinations that are known to fail stay
in CI, and pass only if they fail with the known error. If one starts working or
fails differently, its job fails. Each finished run's summary has a table of what
works where.

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
4. adds `LC_BUILD_VERSION` (a static link leaves it out), with the platform and
   minimum OS version of the object files, and SDK 15.0 instead of theirs (see
   [Where it works](#where-it-works)).

The build then signs the result with `codesign --sign -`, and redoes both when
the script changes. To run the converter by hand:

```sh
./static_to_dyld.py EXECUTABLE [--sdk-version X.Y[.Z]] [--build-version-from OBJECT...]
```

## Inspecting the binaries

```sh
otool -hv build/<mode>/helloc                    # header flags, e.g. DYLDLINK PIE
otool -l  build/<mode>/helloc | grep 'cmd LC_'   # load commands
otool -L  build/<mode>/helloc                    # dylibs (none, or libSystem in libsystem)
nm -u     build/<mode>/helloc                    # undefined symbols (none)
DYLD_PRINT_LIBRARIES=1 build/<mode>/helloc       # what dyld loads (libSystem, in every dyld mode)
codesign -dv build/<mode>/helloc                 # arm64 code must be signed to run
```

## Limitations

- There's no libc: only `write`, `getentropy` and `exit` exist, and `errno` isn't set.
- Which modes run depends on the macOS and Xcode versions; see
  [Where it works](#where-it-works).

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
| `tests/check_exit_code.cmake`     | Checks an exit code, telling a signal apart from a normal exit      |
| `tests/check_imports.cmake`       | Checks that an executable imports nothing and lists the right dylibs |
| `tests/check_static_layout.cmake` | Checks that a static build is static and starts at `start`          |
| `tests/check_loaded_libraries.cmake` | Checks which libraries the process loads, with `DYLD_PRINT_LIBRARIES` |
| `tests/selftest.c`                | `helloc_selftest`: rebasing, stack protector and entry point checks |
| `tests/test_static_to_dyld.py`    | Unit tests for `static_to_dyld.py`                                  |
| `.github/workflows/ci.yml`        | Builds and tests every mode on GitHub Actions                       |
| `LICENSE`                         | MIT license                                                         |

## License

MIT, see `LICENSE`.
