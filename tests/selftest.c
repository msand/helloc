// Checks the runtime behavior that helloc itself doesn't exercise. Built in the
// same mode as helloc and run as "helloc_selftest <check>"; exits with 0 when the
// check passes and 2 on a usage error.
#include "syscalls.h"

extern unsigned long __stack_chk_guard;

static bool streq(const char *a, const char *b) {
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

static bool has_prefix(const char *s, const char *prefix) {
    while (*prefix && *s == *prefix) {
        s++;
        prefix++;
    }
    return *prefix == '\0';
}

// Whether a NULL-terminated string array has an entry starting with prefix
static bool any_has_prefix(char *list[], const char *prefix) {
    for (; *list; list++) {
        if (has_prefix(*list, prefix)) {
            return true;
        }
    }
    return false;
}

// Whether the stack was 16-byte aligned when this was called, as the ABI requires.
// On x86_64 the frame address is 16-byte aligned only if the stack was correctly
// aligned at the call; on arm64 a misaligned stack would already have faulted.
[[gnu::noinline]] static bool stack_aligned(void) {
    return ((unsigned long)__builtin_frame_address(0) & 15) == 0;
}

// --- rebase -----------------------------------------------------------------

// An absolute address stored in data. It must be adjusted when ASLR slides the
// image: in static-dyld from the local relocations that -pie keeps, in nolibs from
// a chained fixup. `pointer` isn't const and has external linkage, so the compiler
// must load it instead of assuming it still holds its initial value, while `target`
// is addressed relative to the program counter and is always correct.
static const char target[] = "rebased";
const char *pointer = target;

static int check_rebase(void) {
    return pointer == target ? 0 : 1;
}

// --- guard, smash -----------------------------------------------------------

// __guard_setup must have replaced the zero guard with random bytes, with the
// second byte cleared. This file is built with -fstack-protector-all, so main is
// protected as well and would fail its own check on return if the guard changed
// while it was running.
static int check_guard(void) {
    return __stack_chk_guard != 0 && (__stack_chk_guard & 0xff00) == 0 ? 0 : 1;
}

[[gnu::noinline]] static void fill(volatile char *p, size_t n) {
    while (n--) {
        *p++ = 'A';
    }
}

// Overflows a local array. The stack protector must catch it when this returns and
// exit with 134 (see stack_protector.c); returning 1 means it didn't.
[[gnu::noinline]] static int check_smash(void) {
    char buf[16];
    fill(buf, 64);
    return 1;
}

// --- args -------------------------------------------------------------------

// main's four arguments as the entry point passed them. CTest runs this check with
// HELLOC_SELFTEST=1 in the environment, and the kernel always puts the executable's
// path in apple[] as "executable_path=...".
static int check_args(int argc, char *argv[], char *envp[], char *apple[]) {
    bool ok = argc == 2 && argv[0] && streq(argv[1], "args") && !argv[2]
              && any_has_prefix(envp, "HELLOC_SELFTEST=1")
              && any_has_prefix(apple, "executable_path=")
              && stack_aligned();
    return ok ? 0 : 1;
}

// --- start ------------------------------------------------------------------

// `start` is the entry point of the static builds, which a release kernel won't
// run on arm64. To test it anyway, this check lays out a stack the way the kernel
// does (see the description in syscalls.s) and jumps to `start`, which calls main
// again with argv[1] = "start-args".
//
// The stack is a static array, with room below the arguments for the frames that
// follow. sp is set 8 bytes off 16-byte alignment, so `start` has to align it.
#define FAKE_STACK_SLOTS 8192
alignas(16) static char *fake_stack[FAKE_STACK_SLOTS];
#define FAKE_SP (&fake_stack[FAKE_STACK_SLOTS - 9])

static char fake_argv0[] = "helloc_selftest";
static char fake_argv1[] = "start-args";
static char fake_env[] = "FAKE=1";
static char fake_apple[] = "executable_path=helloc_selftest";

[[noreturn]] static void enter_start_like_kernel(void) {
    char **sp = FAKE_SP;
    sp[0] = (char *)2;  // argc: an int in a pointer-sized slot
    sp[1] = fake_argv0;
    sp[2] = fake_argv1;
    sp[3] = nullptr;
    sp[4] = fake_env;
    sp[5] = nullptr;
    sp[6] = fake_apple;
    sp[7] = nullptr;
#if defined(__aarch64__)
    __asm__ volatile("mov sp, %0\n\tb start" : : "r"(sp) : "memory");
#elif defined(__x86_64__)
    __asm__ volatile("movq %0, %%rsp\n\tjmp start" : : "r"(sp) : "memory");
#endif
    __builtin_unreachable();
}

// main as `start` called it from the fake stack: the pointers must point into it
static int check_start_args(int argc, char *argv[], char *envp[], char *apple[]) {
    char **sp = FAKE_SP;
    bool ok = argc == 2 && argv == sp + 1 && envp == sp + 4 && apple == sp + 6 && stack_aligned();
    return ok ? 0 : 1;
}

int main(int argc, char *argv[], char *envp[], char *apple[]) {
    const char *check = argc == 2 ? argv[1] : "";
    if (streq(check, "rebase")) {
        return check_rebase();
    }
    if (streq(check, "guard")) {
        return check_guard();
    }
    if (streq(check, "smash")) {
        return check_smash();
    }
    if (streq(check, "args")) {
        return check_args(argc, argv, envp, apple);
    }
    if (streq(check, "start")) {
        enter_start_like_kernel();
    }
    if (streq(check, "start-args")) {
        return check_start_args(argc, argv, envp, apple);
    }
    static const char usage[] = "usage: helloc_selftest rebase|guard|smash|args|start\n";
    write(STDERR_FILENO, usage, sizeof usage - 1);
    return 2;
}
