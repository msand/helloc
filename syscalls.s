// arm64 runtime for helloc: entry points and syscall wrappers.

.global start
.global _start
.global _write
.global _getentropy
.global _exit

.text

/*
 * C runtime startup for HELLOC_MODE=static (entered via LC_UNIXTHREAD)
 *
 * Stock macOS arm64 kernels refuse static executables: xnu's mach_loader.c
 * only allows them on DEVELOPMENT/DEBUG kernels, otherwise exec is SIGKILLed.
 *
 * The kernel passes nothing in registers. exec_copyout_strings() in xnu's
 * kern_exec.c has put the arguments on the stack instead, in 8-byte slots
 * starting at sp:
 *
 *   sp                  argc, an int stored as a 64-bit value
 *   sp + 8              argv: argc string pointers, then NULL
 *   after argv's NULL   envp: pointers to "NAME=value" strings, then NULL
 *   after envp's NULL   apple: pointers to strings the kernel adds for the
 *                       runtime, such as "executable_path=...", then NULL
 *   higher addresses    the strings themselves
 *
 * This finds argc, argv, envp and apple, puts them in x0-x3 as dyld would, and
 * continues in _start.
 */
.align 2
start:
    mov   x9, sp                // x9 = &argc
    and   sp, x9, #~15          // calls need a 16-byte aligned sp
    ldr   x0, [x9]              // x0 = argc
    add   x1, x9, #8            // x1 = argv
    add   x2, x1, x0, lsl #3    // x2 = &argv[argc], the NULL after argv
    add   x2, x2, #8            // x2 = envp
    mov   x3, x2
1:  ldr   x10, [x3], #8         // step x3 past each envp entry and the NULL
    cbnz  x10, 1b               // x3 = apple
    b     _start

/*
 * C runtime startup for HELLOC_MODE=nolibs, libsystem and static-dyld (entered
 * via LC_MAIN)
 *
 * dyld maps the executable, loads and initializes libSystem (in every mode, even
 * when the executable lists no dylib; nothing in it is called) and calls this
 * like main():
 * x0=argc, x1=argv, x2=envp, x3=apple, on a 16-byte aligned stack. `start`
 * continues here with the same registers.
 *
 * Seeds the stack protector, calls main and exits via our own syscall instead of
 * returning into dyld. x19-x22 are callee-saved, so the arguments survive the
 * __guard_setup call; their old values needn't be kept, as this never returns.
 */
.align 2
_start:
    mov   x19, x0
    mov   x20, x1
    mov   x21, x2
    mov   x22, x3
    bl    ___guard_setup        // in stack_protector.c
    mov   x0, x19
    mov   x1, x20
    mov   x2, x21
    mov   x3, x22
    bl    _main                 // main(x0=argc, x1=argv, x2=envp, x3=apple)
    b     _exit                 // exit with return value of main

// write syscall wrapper for macOS ARM64
// syscall number for write is 0x2000004
// On failure the kernel sets the carry flag and puts errno in x0; return -1
// instead, as write(2) does (errno itself is not stored anywhere).
.align 2
_write:
    mov   x16, #4               // syscall number (write)
    orr   x16, x16, #0x2000000  // add macOS syscall class
    svc   #0x80                 // make syscall (interrupt/supervisor call)
    b.cc  1f                    // carry clear: x0 = bytes written
    mov   x0, #-1               // carry set: x0 held errno, return -1
1:
    ret

// getentropy syscall wrapper for macOS ARM64
// syscall number for getentropy is 0x2000000 + 500; returns 0, or -1 on failure
.align 2
_getentropy:
    mov   x16, #500             // syscall number (getentropy)
    orr   x16, x16, #0x2000000  // add macOS syscall class
    svc   #0x80                 // make syscall
    b.cc  1f                    // carry clear: x0 = 0
    mov   x0, #-1               // carry set: x0 held errno, return -1
1:
    ret

// exit syscall wrapper for macOS ARM64
// syscall number for exit is 0x2000001
.align 2
_exit:
    mov   x16, #1               // syscall number (exit)
    orr   x16, x16, #0x2000000  // add macOS syscall class
                                // x0 already contains the exit code
    svc   #0x80                 // make syscall; it doesn't return
    brk   #1                    // trap if it ever does, instead of running off the end
