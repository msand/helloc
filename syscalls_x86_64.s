.global start
.global _write
.global _getentropy
.global _exit

.text

/*
 * C runtime startup for HELLOC_MODE=static-x86_64 (entered via LC_UNIXTHREAD)
 *
 * Static x86_64 executables are allowed by the kernel and run on Apple
 * Silicon under Rosetta. The initial stack has the same layout as the arm64
 * static build (see syscalls.s): argc at (%rsp), then argv[], NULL, envp[],
 * NULL, apple[], NULL. Seeds the stack protector, then calls main.
 */
.p2align 4
start:
    movq  (%rsp), %rdi              // argc
    leaq  8(%rsp), %rsi             // argv
    leaq  8(%rsi,%rdi,8), %rdx      // &env[0] = argv+(argc+1)*8
    movq  %rdx, %rcx
L1:
    movq  (%rcx), %rax              // load env pointer and advance
    addq  $8, %rcx
    testq %rax, %rax                // look for NULL ending env[] array
    jnz   L1                        // loop if not NULL; rcx = apple
    andq  $-16, %rsp                // ABI requires 16-byte alignment at call
    movq  %rdi, %rbx                // keep the arguments in callee-saved registers
    movq  %rsi, %r12
    movq  %rdx, %r13
    movq  %rcx, %r14
    callq ___guard_setup            // in stack_protector.c
    movq  %rbx, %rdi
    movq  %r12, %rsi
    movq  %r13, %rdx
    movq  %r14, %rcx
    callq _main                     // main(rdi=argc, rsi=argv, rdx=envp, rcx=apple)
    movl  %eax, %edi
    jmp   _exit                     // exit with return value of main

// write syscall wrapper for macOS x86_64
// syscall number for write is 0x2000004; fd, buf, nbyte are already in rdi, rsi, rdx
// On failure the kernel sets the carry flag and puts errno in rax; return -1
// instead, as write(2) does (errno itself is not stored anywhere).
.p2align 4
_write:
    movl  $0x2000004, %eax
    syscall
    jnc   1f                        // carry clear: rax = bytes written
    movq  $-1, %rax                 // carry set: rax held errno, return -1
1:
    retq

// getentropy syscall wrapper for macOS x86_64
// syscall number for getentropy is 0x2000000 + 500; buf, size are already in rdi, rsi
// Returns 0, or -1 on failure
.p2align 4
_getentropy:
    movl  $0x20001f4, %eax
    syscall
    jnc   1f                        // carry clear: eax = 0
    movl  $-1, %eax                 // carry set: eax held errno, return -1
1:
    retq

// exit syscall wrapper for macOS x86_64
// syscall number for exit is 0x2000001; edi already contains the exit code
.p2align 4
_exit:
    movl  $0x2000001, %eax
    syscall                         // doesn't return
    ud2                             // trap if it ever does, instead of running off the end
