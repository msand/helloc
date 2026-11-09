#include "syscalls.h"

size_t strlen(const char *str) {
    size_t len = 0;
    while (str[len] != '\0') {
        len++;
    }
    return len;
}

int main() {
    const char *buf = "Hello, world!\n";
    const size_t nbyte = strlen(buf);
    // write() returns -1 on failure and may write fewer bytes than requested
    return write(STDOUT_FILENO, buf, nbyte) == (ssize_t)nbyte ? 0 : 1;
}
