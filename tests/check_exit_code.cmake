# Runs EXE with the argument ARG and checks that it exits normally with EXPECTED.
#
# A shell's $? can't tell these apart: it reports a process killed by signal N as
# 128 + N, so a SIGABRT (6) looks like exit code 134. execute_process reports a
# signal as text instead, such as "Subprocess aborted".
# Usage: cmake -DEXE=<path> -DARG=<argument> -DEXPECTED=<code> -P check_exit_code.cmake
execute_process(COMMAND "${EXE}" ${ARG} RESULT_VARIABLE rc)
if(NOT rc STREQUAL EXPECTED)
    message(FATAL_ERROR "expected exit code ${EXPECTED}, got '${rc}'")
endif()
