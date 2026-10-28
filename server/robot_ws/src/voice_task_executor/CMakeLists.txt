cmake_minimum_required(VERSION 3.8)
project(voice_task_executor)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(ament_index_cpp REQUIRED)
find_package(behaviortree_cpp REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "action/ExecuteTask.action"
)

add_executable(task_executor_node src/task_executor_node.cpp)
ament_target_dependencies(task_executor_node
  ament_index_cpp
  behaviortree_cpp
  rclcpp
  rclcpp_action
)
rosidl_get_typesupport_target(cpp_typesupport_target ${PROJECT_NAME} "rosidl_typesupport_cpp")
target_link_libraries(task_executor_node "${cpp_typesupport_target}")
target_compile_features(task_executor_node PUBLIC cxx_std_17)

install(TARGETS task_executor_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY behavior_trees DESTINATION share/${PROJECT_NAME})

ament_export_dependencies(rosidl_default_runtime)
ament_package()
