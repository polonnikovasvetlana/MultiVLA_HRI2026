#include <atomic>
#include <chrono>
#include <filesystem>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "ament_index_cpp/get_package_share_directory.hpp"
#include "behaviortree_cpp/bt_factory.h"
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "voice_task_executor/action/execute_task.hpp"

using namespace std::chrono_literals;

class TaskExecutorNode : public rclcpp::Node {
 public:
  using ExecuteTask = voice_task_executor::action::ExecuteTask;
  using GoalHandle = rclcpp_action::ServerGoalHandle<ExecuteTask>;

  TaskExecutorNode() : Node("voice_task_executor") {
    tree_directory_ = declare_parameter<std::string>(
        "tree_directory",
        ament_index_cpp::get_package_share_directory("voice_task_executor") +
            "/behavior_trees");
    const auto plugin_libraries =
        declare_parameter<std::vector<std::string>>("bt_plugin_libraries", {});
    for (const auto& library : plugin_libraries) {
      factory_.registerFromPlugin(library);
    }

    action_server_ = rclcpp_action::create_server<ExecuteTask>(
        this, "execute_task",
        std::bind(&TaskExecutorNode::handle_goal, this, std::placeholders::_1,
                  std::placeholders::_2),
        std::bind(&TaskExecutorNode::handle_cancel, this,
                  std::placeholders::_1),
        std::bind(&TaskExecutorNode::handle_accepted, this,
                  std::placeholders::_1));
  }

 private:
  rclcpp_action::GoalResponse handle_goal(
      const rclcpp_action::GoalUUID&,
      std::shared_ptr<const ExecuteTask::Goal> goal) {
    static const std::unordered_set<std::string> allowed{
        "pick", "place", "pick_and_place", "move_to", "home", "stop"};
    if (allowed.count(goal->behavior) == 0U || goal->request_id.empty()) {
      return rclcpp_action::GoalResponse::REJECT;
    }
    if (goal->behavior != "stop") {
      bool expected = false;
      if (!busy_.compare_exchange_strong(expected, true)) {
        return rclcpp_action::GoalResponse::REJECT;
      }
    }
    return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
  }

  rclcpp_action::CancelResponse handle_cancel(
      const std::shared_ptr<GoalHandle>) {
    stop_requested_.store(true);
    return rclcpp_action::CancelResponse::ACCEPT;
  }

  void handle_accepted(const std::shared_ptr<GoalHandle> goal_handle) {
    std::thread{[this, goal_handle]() { execute(goal_handle); }}.detach();
  }

  void execute(const std::shared_ptr<GoalHandle> goal_handle) {
    const auto goal = goal_handle->get_goal();
    auto result = std::make_shared<ExecuteTask::Result>();
    if (goal->behavior == "stop") {
      stop_requested_.store(true);
      result->success = true;
      result->error_code = "";
      result->message = "Stop requested; active tree will be halted";
      goal_handle->succeed(result);
      return;
    }

    std::lock_guard<std::mutex> execution_guard(execution_mutex_);
    stop_requested_.store(false);
    const auto clear_busy = [this](void*) { busy_.store(false); };
    std::unique_ptr<void, decltype(clear_busy)> busy_guard(this, clear_busy);

    const std::unordered_map<std::string, std::string> tree_files{
        {"pick", "pick.xml"},
        {"place", "place.xml"},
        {"pick_and_place", "pick_and_place.xml"},
        {"move_to", "move_to.xml"},
        {"home", "home.xml"}};
    const auto xml_path =
        std::filesystem::path(tree_directory_) / tree_files.at(goal->behavior);
    if (!std::filesystem::is_regular_file(xml_path)) {
      result->success = false;
      result->error_code = "TREE_NOT_FOUND";
      result->message = xml_path.string();
      goal_handle->abort(result);
      return;
    }

    try {
      auto blackboard = BT::Blackboard::create();
      blackboard->set("request_id", goal->request_id);
      blackboard->set("object", goal->object);
      blackboard->set("destination", goal->destination);
      if (goal->has_coordinates) {
        blackboard->set("frame_id", goal->frame_id);
        blackboard->set("x", goal->x);
        blackboard->set("y", goal->y);
        blackboard->set("z", goal->z);
      }
      auto tree = factory_.createTreeFromFile(xml_path.string(), blackboard);
      auto feedback = std::make_shared<ExecuteTask::Feedback>();
      feedback->stage = "RUNNING_BEHAVIOR_TREE";
      feedback->progress = 0.0F;
      goal_handle->publish_feedback(feedback);

      BT::NodeStatus status = BT::NodeStatus::RUNNING;
      while (rclcpp::ok() && status == BT::NodeStatus::RUNNING) {
        if (stop_requested_.load() || goal_handle->is_canceling()) {
          tree.haltTree();
          result->success = false;
          result->error_code = "CANCELLED";
          result->message = "Behavior tree halted";
          goal_handle->canceled(result);
          return;
        }
        status = tree.tickOnce();
        std::this_thread::sleep_for(10ms);
      }
      result->success = status == BT::NodeStatus::SUCCESS;
      result->error_code = result->success ? "" : "TREE_FAILURE";
      result->message = result->success ? "Task completed" : "Behavior tree failed";
      if (result->success) {
        goal_handle->succeed(result);
      } else {
        goal_handle->abort(result);
      }
    } catch (const std::exception& error) {
      result->success = false;
      result->error_code = "BT_CONFIGURATION_ERROR";
      result->message = error.what();
      goal_handle->abort(result);
    }
  }

  std::string tree_directory_;
  BT::BehaviorTreeFactory factory_;
  rclcpp_action::Server<ExecuteTask>::SharedPtr action_server_;
  std::atomic_bool busy_{false};
  std::atomic_bool stop_requested_{false};
  std::mutex execution_mutex_;
};

int main(int argc, char** argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<TaskExecutorNode>());
  rclcpp::shutdown();
  return 0;
}
