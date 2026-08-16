// Copyright 2026 Daisuke Kato
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "memfd_buffer/memfd_buffer_api.hpp"
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/image.hpp"

namespace
{
using Clock = std::chrono::steady_clock;
using Image = sensor_msgs::msg::Image;
using ImageConstSharedPtr = std::shared_ptr<const Image>;

std::string value_of(int argc, char ** argv, const std::string & key, const std::string & fallback)
{
  for (int i = 1; i + 1 < argc; ++i) {
    if (argv[i] == key) {
      return argv[i + 1];
    }
  }
  return fallback;
}

template <typename StampT>
void set_steady_stamp(StampT & stamp, Clock::time_point time)
{
  const auto nanoseconds =
    std::chrono::duration_cast<std::chrono::nanoseconds>(time.time_since_epoch()).count();
  stamp.sec = static_cast<std::int32_t>(nanoseconds / 1000000000LL);
  stamp.nanosec = static_cast<std::uint32_t>(nanoseconds % 1000000000LL);
}

template <typename StampT>
std::int64_t steady_stamp_to_nanoseconds(const StampT & stamp)
{
  return static_cast<std::int64_t>(stamp.sec) * 1000000000LL +
         static_cast<std::int64_t>(stamp.nanosec);
}

double percentile_us(std::vector<std::int64_t> values, double p)
{
  if (values.empty()) {
    return 0.0;
  }
  std::sort(values.begin(), values.end());
  const auto index = static_cast<std::size_t>((values.size() - 1) * p);
  return static_cast<double>(values[index]) / 1000.0;
}

rclcpp::NodeOptions node_options_for(bool use_intra_process)
{
  return rclcpp::NodeOptions().use_intra_process_comms(use_intra_process);
}

class E2eNode : public rclcpp::Node
{
public:
  E2eNode(
    const std::string & role, const std::string & mode, std::size_t size, std::size_t count,
    int rate, std::size_t warmup, bool use_intra_process, const std::string & raw_output_path)
  : Node("memfd_old_e2e_" + role + "_" + mode, node_options_for(use_intra_process)),
    role_(role),
    mode_(mode),
    communication_(use_intra_process ? "intra_process_va" : "inter_process"),
    size_(size),
    count_(count),
    warmup_(warmup),
    use_intra_process_(use_intra_process),
    raw_output_path_(raw_output_path)
  {
    if (!raw_output_path_.empty()) {
      raw_output_.open(raw_output_path_);
      if (!raw_output_) {
        throw std::runtime_error("could not open raw output: " + raw_output_path_);
      }
    }

    const auto qos = rclcpp::QoS(rclcpp::KeepLast(10)).reliable();

    if (role_ == "pub" || role_ == "intra") {
      rclcpp::PublisherOptions publisher_options;
      publisher_options.use_intra_process_comm = use_intra_process_
                                                   ? rclcpp::IntraProcessSetting::Enable
                                                   : rclcpp::IntraProcessSetting::Disable;
      publisher_options.intra_process_buffer_type = rclcpp::IntraProcessBufferType::SharedPtr;
      pub_ = create_publisher<Image>("memfd_old_benchmark_image", qos, publisher_options);
      timer_ =
        create_wall_timer(std::chrono::milliseconds(1000 / rate), [this] { publish_once(); });
    }

    if (role_ == "sub" || role_ == "intra") {
      rclcpp::SubscriptionOptions subscription_options;
      subscription_options.use_intra_process_comm = use_intra_process_
                                                      ? rclcpp::IntraProcessSetting::Enable
                                                      : rclcpp::IntraProcessSetting::Disable;
      subscription_options.intra_process_buffer_type = rclcpp::IntraProcessBufferType::SharedPtr;
      if (mode_ == "memfd") {
        subscription_options.acceptable_buffer_backends = "any";
      }
      sub_ = create_subscription<Image>(
        "memfd_old_benchmark_image", qos,
        [this](ImageConstSharedPtr msg) { receive(std::move(msg)); }, subscription_options);
    }
  }

private:
  void publish_once()
  {
    if (sent_ >= count_) {
      timer_->cancel();
      return;
    }

    const auto subscription_count =
      pub_->get_subscription_count() + pub_->get_intra_process_subscription_count();
    if (subscription_count == 0) {
      return;
    }

    auto msg = std::make_unique<Image>();
    msg->height = 1;
    msg->width = static_cast<std::uint32_t>(size_);
    msg->encoding = "8UC1";
    msg->step = static_cast<std::uint32_t>(size_);

    std::uintptr_t published_data_address = 0;
    if (mode_ == "memfd") {
      msg->data = memfd_buffer_backend::allocate_buffer(size_);
      auto view = memfd_buffer_backend::from_output_buffer(msg->data);
      std::memset(view.get_ptr(), static_cast<int>(sent_ & 0xff), size_);
      published_data_address = reinterpret_cast<std::uintptr_t>(view.get_ptr());
    } else {
      msg->data.resize(size_);
      std::memset(msg->data.data(), static_cast<int>(sent_ & 0xff), size_);
      published_data_address = reinterpret_cast<std::uintptr_t>(msg->data.data());
    }

    if (use_intra_process_) {
      published_data_address_ = published_data_address;
    }

    // Start immediately before publish.  steady_clock is comparable between
    // same-host Linux processes.  The timestamp also identifies this message
    // when the publisher and subscriber raw files are joined by the runner.
    const auto publish_start = Clock::now();
    set_steady_stamp(msg->header.stamp, publish_start);
    const auto publish_timestamp_ns =
      std::chrono::duration_cast<std::chrono::nanoseconds>(publish_start.time_since_epoch()).count();
    pub_->publish(std::move(msg));
    const auto publish_end = Clock::now();
    write_raw_publish(
      publish_timestamp_ns,
      std::chrono::duration_cast<std::chrono::nanoseconds>(publish_end - publish_start).count());
    ++sent_;
  }

  void receive(ImageConstSharedPtr msg)
  {
    const auto backend = msg->data.get_backend_type();
    if ((mode_ == "memfd" && backend != "memfd") || (mode_ == "cpu" && backend != "cpu")) {
      std::cerr << "RESULT,error,backend_mismatch," << communication_ << ',' << mode_ << ','
                << backend << std::endl;
      rclcpp::shutdown();
      return;
    }

    std::uintptr_t received_data_address = 0;
    volatile std::uint8_t sample = 0;
    Clock::time_point end;
    if (mode_ == "memfd") {
      auto view = memfd_buffer_backend::from_input_buffer(msg->data);
      received_data_address = reinterpret_cast<std::uintptr_t>(view.get_ptr());
      sample = view.get_ptr()[0];
      end = Clock::now();
    } else {
      received_data_address = reinterpret_cast<std::uintptr_t>(msg->data.data());
      sample = msg->data.data()[0];
      end = Clock::now();
    }
    (void)sample;

    if (use_intra_process_ && received_data_address == published_data_address_) {
      ++va_matches_;
    }

    const auto latency_ns =
      std::chrono::duration_cast<std::chrono::nanoseconds>(end.time_since_epoch()).count() -
      steady_stamp_to_nanoseconds(msg->header.stamp);
    if (latency_ns < 0) {
      std::cerr << "RESULT,error,negative_latency," << communication_ << ',' << mode_ << ','
                << size_ << ',' << latency_ns << std::endl;
      rclcpp::shutdown();
      return;
    }

    ++received_;
    const bool measured = received_ > warmup_;
    const auto sample_index = measured ? received_ - warmup_ - 1 : 0;
    const auto publish_timestamp_ns = steady_stamp_to_nanoseconds(msg->header.stamp);
    const auto receive_timestamp_ns =
      std::chrono::duration_cast<std::chrono::nanoseconds>(end.time_since_epoch()).count();
    write_raw_receive(
      publish_timestamp_ns, receive_timestamp_ns, latency_ns, measured, sample_index);
    if (measured) {
      latencies_.push_back(latency_ns);
    }

    if (received_ == count_) {
      std::cout << "RESULT,ok," << communication_ << ',' << mode_ << ',' << size_ << ','
                << received_ << ',' << latencies_.size() << ',' << va_matches_ << ','
                << percentile_us(latencies_, 0.50) << ',' << percentile_us(latencies_, 0.95) << ','
                << percentile_us(latencies_, 0.99) << std::endl;
      rclcpp::shutdown();
    }
  }

  void write_raw_publish(std::int64_t publish_timestamp_ns, std::int64_t duration_ns)
  {
    if (!raw_output_) {
      return;
    }
    raw_output_ << "RAW,publish," << publish_timestamp_ns << ',' << duration_ns << '\n';
    raw_output_.flush();
  }

  void write_raw_receive(
    std::int64_t publish_timestamp_ns, std::int64_t receive_timestamp_ns,
    std::int64_t latency_ns, bool measured, std::size_t sample_index)
  {
    if (!raw_output_) {
      return;
    }
    raw_output_ << "RAW,receive," << publish_timestamp_ns << ',' << receive_timestamp_ns << ','
                << latency_ns << ',' << (measured ? 1 : 0) << ',' << sample_index << '\n';
    raw_output_.flush();
  }

  std::string role_;
  std::string mode_;
  std::string communication_;
  std::size_t size_;
  std::size_t count_;
  std::size_t warmup_;
  bool use_intra_process_;
  std::size_t sent_{0};
  std::size_t received_{0};
  std::size_t va_matches_{0};
  std::uintptr_t published_data_address_{0};
  std::string raw_output_path_;
  std::ofstream raw_output_;
  rclcpp::Publisher<Image>::SharedPtr pub_;
  rclcpp::Subscription<Image>::SharedPtr sub_;
  rclcpp::TimerBase::SharedPtr timer_;
  std::vector<std::int64_t> latencies_;
};
}  // namespace

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  const auto role = value_of(argc, argv, "--role", "");
  const auto mode = value_of(argc, argv, "--mode", "cpu");
  const bool use_intra_process = role == "intra";
  if ((role != "pub" && role != "sub" && role != "intra") || (mode != "cpu" && mode != "memfd")) {
    rclcpp::shutdown();
    return 2;
  }

  auto node = std::make_shared<E2eNode>(
    role, mode, std::stoull(value_of(argc, argv, "--size", "1048576")),
    std::stoull(value_of(argc, argv, "--count", "100")),
    std::stoi(value_of(argc, argv, "--rate-hz", "50")),
    std::stoull(value_of(argc, argv, "--warmup", "10")), use_intra_process,
    value_of(argc, argv, "--raw-output", ""));
  rclcpp::spin(node);
  rclcpp::shutdown();
}
