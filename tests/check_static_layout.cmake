# Checks that EXE is a static executable that the kernel would start at `start`:
# no MH_DYLDLINK flag, no dynamic linker, dylibs or dyld fixups, one LC_UNIXTHREAD
# whose pc (rip on x86_64) is the address of `start`, no undefined symbols, and on
# arm64, where code must be signed to run, a valid signature.
# Usage: cmake -DEXE=<path> -DARCH=arm64|x86_64 -P check_static_layout.cmake

function(run out)
    execute_process(COMMAND ${ARGN} RESULT_VARIABLE rc OUTPUT_VARIABLE stdout ERROR_VARIABLE stderr)
    if(NOT rc EQUAL 0)
        list(JOIN ARGN " " command)
        message(FATAL_ERROR "'${command}' failed (${rc}): ${stderr}")
    endif()
    set(${out} "${stdout}" PARENT_SCOPE)
endfunction()

run(header otool -hv "${EXE}")
if(NOT header MATCHES "EXECUTE")
    message(FATAL_ERROR "not an executable:\n${header}")
endif()
if(header MATCHES "DYLDLINK")
    message(FATAL_ERROR "has the MH_DYLDLINK flag, so the kernel would load dyld:\n${header}")
endif()

run(commands otool -l "${EXE}")
foreach(cmd LC_LOAD_DYLINKER LC_MAIN LC_LOAD_DYLIB LC_DYLD_INFO_ONLY LC_DYLD_CHAINED_FIXUPS)
    if(commands MATCHES "cmd ${cmd}\n")
        message(FATAL_ERROR "has ${cmd}, which only dyld uses")
    endif()
endforeach()
string(REGEX MATCHALL "cmd LC_UNIXTHREAD\n" threads "${commands}")
list(LENGTH threads count)
if(NOT count EQUAL 1)
    message(FATAL_ERROR "expected one LC_UNIXTHREAD, found ${count}")
endif()
if(NOT commands MATCHES "[ \t](pc|rip) +0x([0-9a-f]+)")
    message(FATAL_ERROR "no pc or rip in LC_UNIXTHREAD")
endif()
set(entry "${CMAKE_MATCH_2}")

run(symbols nm "${EXE}")
if(NOT symbols MATCHES "([0-9a-f]+) T start\n")
    message(FATAL_ERROR "no global symbol 'start'")
endif()
if(NOT entry STREQUAL CMAKE_MATCH_1)
    message(FATAL_ERROR "entry point is 0x${entry}, but start is at 0x${CMAKE_MATCH_1}")
endif()

run(undefined nm -u "${EXE}")
if(NOT undefined STREQUAL "")
    message(FATAL_ERROR "undefined symbols:\n${undefined}")
endif()

if(ARCH STREQUAL "arm64")
    run(signature codesign --verify --strict "${EXE}")
endif()
