// Declarations for the syscall wrappers in syscalls.s and syscalls_x86_64.s.
//
// There's no libc, so this replaces <unistd.h> and <stdlib.h>. C code is compiled
// with -ffreestanding -nostdlibinc: only the compiler's own headers, such as
// <stddef.h>, are available, not the SDK's.
#pragma once

#include <stddef.h>

typedef long ssize_t;

#define STDOUT_FILENO 1
#define STDERR_FILENO 2

// Returns the number of bytes written, or -1 on failure (errno isn't set).
ssize_t write(int fd, const void *buf, size_t nbyte);

// Fills buf with size (at most 256) random bytes. Returns 0, or -1 on failure.
int getentropy(void *buf, size_t size);

[[noreturn]] void exit(int status);
