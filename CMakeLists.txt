cmake_minimum_required(VERSION 3.31)
project(helloc C ASM)

set(CMAKE_C_STANDARD 23)

# Every mode is standalone: no crt, no libSystem, only raw syscalls. See README.md.
#   nolibs         arm64, mapped by dyld but loads no dylibs; runs on stock macOS (default)
#   static         arm64, -static; stock macOS kernels refuse to run static arm64
#                  executables, so this only runs on a DEVELOPMENT/DEBUG kernel
#   static-x86_64  x86_64, -static; runs on Apple Silicon via Rosetta
#   static-dyld    arm64, -static -pie, then converted by static_to_dyld.py into an
#                  executable that dyld maps (still no dylibs); runs on stock macOS
set(HELLOC_MODES nolibs static static-x86_64 static-dyld)
set(HELLOC_MODE nolibs CACHE STRING "Link mode: nolibs, static, static-x86_64 or static-dyld")
set_property(CACHE HELLOC_MODE PROPERTY STRINGS ${HELLOC_MODES})
if(NOT HELLOC_MODE IN_LIST HELLOC_MODES)
    message(FATAL_ERROR "Unknown HELLOC_MODE '${HELLOC_MODE}': use nolibs, static, static-x86_64 or static-dyld")
endif()
if(HELLOC_MODE STREQUAL "static")
    message(WARNING "HELLOC_MODE=static: stock macOS kernels refuse static arm64 executables, "
                    "so helloc is killed at launch (exit 137) unless a DEVELOPMENT/DEBUG kernel is booted. "
                    "The tests that run it are disabled; static_layout still checks the binary.")
elseif(HELLOC_MODE STREQUAL "static-dyld")
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
endif()

# Adds an executable built from the given C sources and the runtime (entry points,
# syscall wrappers, stack protector), linked the way HELLOC_MODE says.
function(helloc_add_executable target)
    if(HELLOC_MODE STREQUAL "static-x86_64")
        add_executable(${target} syscalls_x86_64.s stack_protector.c ${ARGN})
        set_target_properties(${target} PROPERTIES OSX_ARCHITECTURES x86_64)
    else()
        add_executable(${target} syscalls.s stack_protector.c ${ARGN})
        set_target_properties(${target} PROPERTIES OSX_ARCHITECTURES arm64)
    endif()
    target_include_directories(${target} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    # Only the compiler's own headers: no SDK headers, as there's no libc to back them
    target_compile_options(${target} PRIVATE $<$<COMPILE_LANGUAGE:C>:-ffreestanding -nostdlibinc>)
    target_link_options(${target} PRIVATE -nostdlib)

    if(HELLOC_MODE STREQUAL "nolibs")
        target_link_options(${target} PRIVATE LINKER:-e,_start)
    elseif(HELLOC_MODE STREQUAL "static")
        # arm64 code must be signed to run, and ld only signs dyld-linked binaries by default
        target_link_options(${target} PRIVATE -static LINKER:-e,start LINKER:-adhoc_codesign)
    elseif(HELLOC_MODE STREQUAL "static-x86_64")
        target_link_options(${target} PRIVATE -static LINKER:-e,start)
    elseif(HELLOC_MODE STREQUAL "static-dyld")
        # -pie keeps the rebase info dyld needs after ASLR slides the image; the entry is
        # _start because dyld enters through LC_MAIN. Sign only after converting.
        target_link_options(${target} PRIVATE -static LINKER:-pie LINKER:-e,_start)
        add_custom_command(TARGET ${target} POST_BUILD
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/static_to_dyld.py $<TARGET_FILE:${target}>
                    --build-version-from $<TARGET_OBJECTS:${target}>
            COMMAND codesign --sign - $<TARGET_FILE:${target}>
            COMMAND_EXPAND_LISTS
            VERBATIM)
        # Relink, and so convert again, when the converter changes
        set_property(TARGET ${target} APPEND PROPERTY LINK_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/static_to_dyld.py)
    endif()
endfunction()

helloc_add_executable(helloc main.c)
helloc_add_executable(helloc_selftest tests/selftest.c)
# Protect every function, so the checks don't depend on the compiler's default level
target_compile_options(helloc_selftest PRIVATE -fstack-protector-all)

enable_testing()
add_test(NAME prints_hello
    COMMAND ${CMAKE_COMMAND} -DHELLOC=$<TARGET_FILE:helloc> -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_output.cmake)
# With stdout closed, write() fails and helloc must exit with 1
add_test(NAME fails_on_closed_stdout
    COMMAND sh -c "\"$0\" >&-; test $? -eq 1" $<TARGET_FILE:helloc>)
add_test(NAME rebases_pointers COMMAND helloc_selftest rebase)
add_test(NAME seeds_stack_guard COMMAND helloc_selftest guard)
add_test(NAME catches_stack_overflow
    COMMAND sh -c "\"$0\" smash; test $? -eq 134" $<TARGET_FILE:helloc_selftest>)
add_test(NAME passes_main_arguments COMMAND helloc_selftest args)
set_tests_properties(passes_main_arguments PROPERTIES ENVIRONMENT HELLOC_SELFTEST=1)
add_test(NAME enters_through_start COMMAND helloc_selftest start)
if(HELLOC_MODE STREQUAL "static")
    # The programs can't run on a release kernel
    get_property(tests DIRECTORY PROPERTY TESTS)
    set_tests_properties(${tests} PROPERTIES DISABLED TRUE)
endif()

if(HELLOC_MODE MATCHES "^static(-x86_64)?$")
    get_target_property(arch helloc OSX_ARCHITECTURES)
    add_test(NAME static_layout
        COMMAND ${CMAKE_COMMAND} -DEXE=$<TARGET_FILE:helloc> -DARCH=${arch}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_static_layout.cmake)
elseif(HELLOC_MODE STREQUAL "static-dyld")
    add_test(NAME static_to_dyld_unit
        COMMAND ${Python3_EXECUTABLE} -B ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_static_to_dyld.py)
endif()
