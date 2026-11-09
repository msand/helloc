// Stack protector runtime.
//
// clang protects functions with local arrays of 8 bytes or more by default, even
// with -ffreestanding: the prologue copies __stack_chk_guard into the stack frame,
// and the epilogue calls __stack_chk_fail if that copy was overwritten. libc
// normally provides both symbols.
#include "syscalls.h"

// Exit status after a detected overflow. libc's __stack_chk_fail calls abort(), and
// this is the status a shell reports for a process killed by SIGABRT (128 + 6).
#define STACK_CHK_FAIL_STATUS 134

unsigned long __stack_chk_guard;

[[noreturn]] __attribute__((no_stack_protector))
void __stack_chk_fail(void) {
    exit(STACK_CHK_FAIL_STATUS);
}

// Seeds __stack_chk_guard with random bytes. Named after the libc function that
// does the same. Called by the entry points in syscalls*.s before main: a protected
// function running while the guard changes would fail its check on return.
//
// The kernel also passes a random "stack_guard=" string in apple[], but dyld blanks
// it before calling the entry point, so it's only usable in the static modes.
// getentropy works in every mode, and can't fail for 8 bytes into a valid buffer.
__attribute__((no_stack_protector))
void __guard_setup(void) {
    unsigned long guard;
    if (getentropy(&guard, sizeof guard) == 0) {
        // Clear the second byte, as the kernel does for its value, so a string copy
        // that overruns a buffer stops before it has rewritten the whole guard
        __stack_chk_guard = guard & ~(0xffUL << 8);
    }
}
