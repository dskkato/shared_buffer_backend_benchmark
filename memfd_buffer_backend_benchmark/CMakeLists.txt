cmake_minimum_required(VERSION 3.20)
project(memfd_buffer_backend_benchmark)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR
    "memfd_buffer_backend_benchmark supports Linux only (CMAKE_SYSTEM_NAME='${CMAKE_SYSTEM_NAME}').")
endif()

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(memfd_buffer REQUIRED)
find_package(memfd_buffer_backend REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)

add_executable(memfd_buffer_backend_e2e_node src/memfd_buffer_backend_e2e_node.cpp)
target_link_libraries(
  memfd_buffer_backend_e2e_node
  memfd_buffer::memfd_buffer
  rclcpp::rclcpp
  ${sensor_msgs_TARGETS}
)

install(
  TARGETS memfd_buffer_backend_e2e_node
  DESTINATION lib/${PROJECT_NAME}
)

install(
  PROGRAMS
    scripts/run_e2e_benchmark.py
    scripts/run_16way_benchmark.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
