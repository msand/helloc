# Checks that EXE imports no symbols, so it calls nothing in any library, and that
# the dylibs it lists are exactly DYLIBS (a list, possibly empty).
# Usage: cmake -DEXE=<path> "-DDYLIBS=<dylib>;..." -P check_imports.cmake

function(run out)
    execute_process(COMMAND ${ARGN} RESULT_VARIABLE rc OUTPUT_VARIABLE stdout ERROR_VARIABLE stderr)
    if(NOT rc EQUAL 0)
        list(JOIN ARGN " " command)
        message(FATAL_ERROR "'${command}' failed (${rc}): ${stderr}")
    endif()
    set(${out} "${stdout}" PARENT_SCOPE)
endfunction()

run(undefined nm -u "${EXE}")
if(NOT undefined STREQUAL "")
    message(FATAL_ERROR "imports symbols:\n${undefined}")
endif()

# otool -L prints the file name, then one "\t<path> (compatibility version ...)" line per dylib
run(listing otool -L "${EXE}")
string(REGEX MATCHALL "\t[^ \t\n]+ \\(" entries "${listing}")
set(found "")
foreach(entry IN LISTS entries)
    string(REGEX REPLACE "^\t([^ ]+) \\($" "\\1" path "${entry}")
    list(APPEND found "${path}")
endforeach()
if(NOT "${found}" STREQUAL "${DYLIBS}")
    message(FATAL_ERROR "lists dylibs '${found}', expected '${DYLIBS}'")
endif()
