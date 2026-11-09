# Runs HELLOC and checks that it exits with 0 and prints exactly "Hello, world!\n".
# Usage: cmake -DHELLOC=<path to helloc> -P check_output.cmake
execute_process(COMMAND "${HELLOC}" RESULT_VARIABLE rc OUTPUT_VARIABLE out)
if(NOT rc STREQUAL "0")
    message(FATAL_ERROR "expected exit code 0, got '${rc}'")
endif()
if(NOT out STREQUAL "Hello, world!\n")
    message(FATAL_ERROR "unexpected output: '${out}'")
endif()
