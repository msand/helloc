cmake_minimum_required(VERSION 3.31)
# The oldest macOS the binaries are for, recorded in them as the minimum OS version.
# Set here rather than left to the compiler's default, the build machine's version,
# so every machine records the same one, below the SDK version static-dyld records
# (see HELLOC_DYLD_SDK below).
set(CMAKE_OSX_DEPLOYMENT_TARGET 14.0 CACHE STRING "Minimum macOS version")
project(helloc C ASM)

set(CMAKE_C_STANDARD 23)
# The code uses C23 keywords (bool, nullptr, alignas): stop if the compiler lacks C23,
# rather than falling back to an older standard and failing on them
set(CMAKE_C_STANDARD_REQUIRED ON)

# No mode uses a crt or libc, only raw syscalls. README.md lists which modes work
# with which macOS and Xcode versions. dyld loads libSystem into every process it
# starts, even when the executable lists no dylib, so only the static modes run
# with no library loaded.
#   nolibs         arm64, loaded by dyld; lists no dylib
#   libsystem      arm64, like nolibs but lists libSystem as a dependency, which newer
#                  linkers and macOS 26's dyld require; nothing in it is called
#   static         arm64, -static; stock macOS kernels refuse to run static arm64
#                  executables, so this only runs on a DEVELOPMENT/DEBUG kernel
#   static-x86_64  x86_64, -static; runs on Apple Silicon via Rosetta (default, as it
#                  runs on every macOS version tested, without a workaround)
#   static-dyld    arm64, -static -pie, then converted by static_to_dyld.py into an
#                  executable that dyld loads (still listing no dylib)
set(HELLOC_MODES nolibs libsystem static static-x86_64 static-dyld)
list(JOIN HELLOC_MODES ", " modes)
set(HELLOC_MODE static-x86_64 CACHE STRING "Link mode: ${modes}")
set_property(CACHE HELLOC_MODE PROPERTY STRINGS ${HELLOC_MODES})
if(NOT HELLOC_MODE IN_LIST HELLOC_MODES)
    message(FATAL_ERROR "Unknown HELLOC_MODE '${HELLOC_MODE}': use one of ${modes}")
endif()
if(HELLOC_MODE STREQUAL "static")
    message(WARNING "HELLOC_MODE=static: stock macOS kernels refuse static arm64 executables, "
                    "so helloc is killed at launch (exit 137) unless a DEVELOPMENT/DEBUG kernel is booted. "
                    "The tests that run it are disabled; static_layout still checks the binary.")
elseif(HELLOC_MODE STREQUAL "static-x86_64" AND CMAKE_HOST_SYSTEM_PROCESSOR STREQUAL "arm64")
    # Apple Silicon runs x86_64 code through Rosetta, which macOS doesn't install by
    # default and a build can't prompt for
    execute_process(COMMAND /usr/bin/arch -x86_64 /usr/bin/true
        RESULT_VARIABLE rosetta OUTPUT_QUIET ERROR_QUIET)
    if(NOT rosetta EQUAL 0)
        message(WARNING "HELLOC_MODE=static-x86_64: Rosetta isn't installed, so helloc builds but can't run "
                        "and its tests will fail. Install it with: softwareupdate --install-rosetta --agree-to-license")
    endif()
elseif(HELLOC_MODE STREQUAL "static-dyld")
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
endif()

# The SDK version static-dyld records instead of the real one. macOS 26's dyld
# refuses an executable that lists no dylib only if it was built with SDK 15.4 or
# newer (dyld's Policy::enforceHasLinkedDylibs), so static-dyld records an older SDK.
# 15.0 rather than 15.3, in case an older dyld counts 15.3 as the spring 2025 SDK.
# (nolibs can't do the same: Xcode 16.4's and 26.6's linkers refuse it even with
# -platform_version recording SDK 15.0.)
set(HELLOC_DYLD_SDK 15.0)

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
    target_compile_options(${target} PRIVATE "$<$<COMPILE_LANGUAGE:C>:-ffreestanding;-nostdlibinc>")
    target_link_options(${target} PRIVATE -nostdlib)

    if(HELLOC_MODE STREQUAL "nolibs")
        target_link_options(${target} PRIVATE LINKER:-e,_start)
    elseif(HELLOC_MODE STREQUAL "libsystem")
        # Only listed as a dependency: everything is defined here, so nothing binds to it
        target_link_options(${target} PRIVATE LINKER:-e,_start)
        target_link_libraries(${target} PRIVATE System)
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
                    --sdk-version ${HELLOC_DYLD_SDK} --build-version-from $<TARGET_OBJECTS:${target}>
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
    COMMAND ${CMAKE_COMMAND} -DEXE=$<TARGET_FILE:helloc_selftest> -DARG=smash -DEXPECTED=134
            -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_exit_code.cmake)
add_test(NAME passes_main_arguments COMMAND helloc_selftest args)
set_tests_properties(passes_main_arguments PROPERTIES ENVIRONMENT HELLOC_SELFTEST=1)
add_test(NAME enters_through_start COMMAND helloc_selftest start)
# What the process loads: libSystem in the modes that dyld starts, nothing in the others
if(HELLOC_MODE MATCHES "^static(-x86_64)?$")
    set(dyld OFF)
else()
    set(dyld ON)
endif()
add_test(NAME loaded_libraries
    COMMAND ${CMAKE_COMMAND} -DEXE=$<TARGET_FILE:helloc> -DDYLD=${dyld}
            -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_loaded_libraries.cmake)
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
else()
    # Loaded by dyld: nothing may be imported, and only libsystem lists a dylib
    if(HELLOC_MODE STREQUAL "libsystem")
        set(dylibs /usr/lib/libSystem.B.dylib)
    else()
        set(dylibs "")
    endif()
    add_test(NAME imports_nothing
        COMMAND ${CMAKE_COMMAND} -DEXE=$<TARGET_FILE:helloc> "-DDYLIBS=${dylibs}"
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_imports.cmake)
endif()
if(HELLOC_MODE STREQUAL "static-dyld")
    add_test(NAME static_to_dyld_unit
        COMMAND ${Python3_EXECUTABLE} -B ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_static_to_dyld.py)
endif()

# Each test takes about a second; a hung one fails instead of blocking ctest
get_property(tests DIRECTORY PROPERTY TESTS)
set_tests_properties(${tests} PROPERTIES TIMEOUT 60)
