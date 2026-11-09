# Runs EXE with DYLD_PRINT_LIBRARIES=1, which makes dyld print each image it loads,
# and checks what the process loads.
#
# DYLD=ON: dyld starts EXE, and must load libSystem even if EXE lists no dylib. dyld
# treats an executable that lists none as linking libSystem, and can't start a
# program without it. If this ever fails, a dyld mode really runs with no library.
# DYLD=OFF: the kernel starts EXE without dyld, so nothing may be printed.
# Usage: cmake -DEXE=<path> -DDYLD=ON|OFF -P check_loaded_libraries.cmake
set(ENV{DYLD_PRINT_LIBRARIES} 1)
execute_process(COMMAND "${EXE}" RESULT_VARIABLE rc OUTPUT_QUIET ERROR_VARIABLE log)
if(NOT rc STREQUAL "0")
    message(FATAL_ERROR "expected exit code 0, got '${rc}':\n${log}")
endif()

# dyld prints "dyld[<pid>]: <<uuid>> <path>" per image, the executable first
string(REGEX MATCHALL "dyld\\[[0-9]+\\]: <[^>\n]*> [^\n]+" images "${log}")
list(LENGTH images count)
if(DYLD)
    if(NOT log MATCHES "dyld\\[[0-9]+\\]: <[^>\n]*> /usr/lib/libSystem\\.B\\.dylib\n")
        message(FATAL_ERROR "dyld didn't load libSystem, so the README is out of date:\n${log}")
    endif()
    message(STATUS "dyld loaded ${count} images, including libSystem")
elseif(count GREATER 0)
    message(FATAL_ERROR "dyld loaded images, though the kernel should start EXE without dyld:\n${log}")
endif()
