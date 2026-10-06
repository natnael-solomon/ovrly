cmake_minimum_required(VERSION 3.18)
project(res02_device_harness C CXX)

# Paths to the pinned, separately downloaded upstream inputs (see README.md).
set(WHISPER_SRC "" CACHE PATH "Extracted whisper.cpp source tree")
set(VOSK_DIR "" CACHE PATH "Directory with vosk_api.h and arm64-v8a libvosk.so")

if(NOT WHISPER_SRC OR NOT VOSK_DIR)
  message(FATAL_ERROR "Set -DWHISPER_SRC=... and -DVOSK_DIR=...")
endif()

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
add_subdirectory(${WHISPER_SRC} whisper EXCLUDE_FROM_ALL)

add_executable(whisper_bench whisper_bench.c)
target_link_libraries(whisper_bench PRIVATE whisper)

add_library(vosk SHARED IMPORTED)
set_target_properties(vosk PROPERTIES IMPORTED_LOCATION ${VOSK_DIR}/libvosk.so)
add_executable(vosk_bench vosk_bench.c)
target_include_directories(vosk_bench PRIVATE ${VOSK_DIR})
target_link_libraries(vosk_bench PRIVATE vosk)
