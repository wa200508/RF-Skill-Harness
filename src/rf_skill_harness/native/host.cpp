// SPDX-License-Identifier: Apache-2.0
// Native RF MEL host. Uses the pinned starter-kit runtime and UCI/OWP client.
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "runtime.h"
#include "c2.h"
#include <rfmel/admin/AdminMEL.h>
#include <rfmel/admin/UCI_Control.h>
#include <rfmel/admin/StatusControl.h>
#include <rfmel/c2/JobDetail.h>
#include <rfmel/c2/CancelStatus.h>
#include <rfmel/data/ProductRxEndpoint.h>
#include <rfmel/jobs/JobInterval.h>
#include <boost/uuid/name_generator_sha1.hpp>
#include <boost/uuid/string_generator.hpp>
#include <boost/uuid/uuid_io.hpp>
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <csignal>
#include <deque>
#include <fstream>
#include <iostream>
#include <mutex>
#include <thread>

using json = nlohmann::json;
namespace rt = rf_fm_demod::runtime;
namespace c2 = rf_fm_demod::c2;
namespace mel = ams::iface::rfmel;
volatile std::sig_atomic_t stopping = 0;
void stop_signal(int) { stopping = 1; }

class PythonEngine {
  PyObject *engine_ = nullptr;
  PyThreadState *state_ = nullptr;
  static void checked(bool valid) {
    if (!valid) { PyErr_Print(); throw std::runtime_error("Python skill failed; see traceback"); }
  }
public:
  explicit PythonEngine(const json &config) {
    Py_Initialize();
    auto path = PyUnicode_FromString((config.at("skill_root").get<std::string>() + "/runtime").c_str());
    checked(path != nullptr);
    PyList_Insert(PySys_GetObject("path"), 0, path); Py_DECREF(path);
    auto module = PyImport_ImportModule("engine"); checked(module != nullptr);
    auto cls = PyObject_GetAttrString(module, "Engine"); Py_DECREF(module); checked(cls != nullptr);
    engine_ = PyObject_CallFunction(cls, "s", config.dump().c_str()); Py_DECREF(cls); checked(engine_ != nullptr);
    state_ = PyEval_SaveThread();
  }
  json consume(const std::string &bytes, const json &metadata) {
    const auto gil = PyGILState_Ensure();
    try {
      auto value = PyObject_CallMethod(engine_, "consume", "y#s", bytes.data(),
        static_cast<Py_ssize_t>(bytes.size()), metadata.dump().c_str());
      checked(value != nullptr);
      const char *encoded = PyUnicode_AsUTF8(value);
      if (!encoded) { Py_DECREF(value); checked(false); }
      auto result = json::parse(encoded); Py_DECREF(value);
      PyGILState_Release(gil); return result;
    } catch (...) { PyGILState_Release(gil); throw; }
  }
  ~PythonEngine() {
    if (state_) { PyEval_RestoreThread(state_); Py_XDECREF(engine_); Py_Finalize(); }
  }
};

struct Frame { std::string bytes; json metadata; };
struct Queue {
  std::mutex mutex;
  std::condition_variable condition;
  std::deque<Frame> frames;
  std::exception_ptr failure;
  std::size_t capacity;
  explicit Queue(std::size_t n) : capacity(n) { if (!n || n > 4096) throw std::runtime_error("Invalid queue capacity"); }
  void receive(const std::shared_ptr<mel::ProductRxMetadata> &meta, mel::JobDataPointer data, std::size_t count) noexcept {
    try {
      if (!meta || !count || count > rt::kMaxIqFrameSamples || !std::holds_alternative<mel::MELComplex<int16_t> *>(data))
        throw std::runtime_error("Invalid native ComplexINT16 callback");
      const auto *samples = std::get<mel::MELComplex<int16_t> *>(data);
      if (!samples) throw std::runtime_error("Null native sample pointer");
      Frame frame;
      frame.bytes.reserve(count * 4);
      for (std::size_t i = 0; i < count; ++i) {
        for (auto v : {samples[i].real(), samples[i].imag()}) {
          auto u = static_cast<std::uint16_t>(v);
          frame.bytes.push_back(static_cast<char>(u & 255)); frame.bytes.push_back(static_cast<char>(u >> 8));
        }
      }
      const auto time = meta->getFirstReceiveEventStart();
      json events = json::array();
      for (const auto &event : meta->getReceiveEvents()) events.push_back({
        {"start_fs", event.getStart().count()}, {"duration_fs", event.getDuration().count()},
        {"sample_rate_hz", event.getSampleFrequency()}, {"carrier_hz", event.getCenterFrequency()}, {"event_id", event.getEventID()}});
      frame.metadata = {{"seconds", time.getIntegralSeconds().count()},
        {"fractional_fs", time.getFractionalFemtoseconds().count()}, {"events", events},
        {"stream_ids", meta->getRxStreamIDs()}, {"job_interval_id", meta->getJobIntervalID()},
        {"job_details_id", meta->getJobDetailsID()}, {"va_instance_id", meta->getVaInstanceID()},
        {"va_definition_id", meta->getVaDefinitionID()}, {"phase_coherence_with_prior", meta->getPhaseCoherenceWithPrior()}};
      std::lock_guard lock(mutex);
      if (frames.size() >= capacity) throw std::runtime_error("Native I/Q queue overrun; refusing to drop samples");
      frames.push_back(std::move(frame)); condition.notify_one();
    } catch (...) { std::lock_guard lock(mutex); failure = std::current_exception(); condition.notify_one(); }
  }
  std::optional<Frame> next(std::chrono::steady_clock::duration wait) {
    std::unique_lock lock(mutex);
    condition.wait_for(lock, wait, [&] { return failure || !frames.empty(); });
    if (failure) std::rethrow_exception(failure);
    if (frames.empty()) return {};
    auto frame = std::move(frames.front()); frames.pop_front(); return frame;
  }
};

rt::AppConfig reference_config(const json &value) {
  rt::AppConfig config;
  config.rf_mel_registry_path = value.at("rf_mel_registry_path"); config.rf_mel_id = value.at("rf_mel_id");
  config.iq_sample_rate_hz = value.at("iq_sample_rate_hz"); config.center_frequency_hz = value.at("center_frequency_hz");
  if (!config.iq_sample_rate_hz || !(config.center_frequency_hz > 0)) throw std::runtime_error("Invalid receiver frequency/rate");
  config.virtual_aperture_definition_id = value.at("virtual_aperture_definition_id");
  config.capability_ids = value.at("capability_ids").get<std::vector<std::string>>();
  config.owp_uri = value.at("owp_uri"); config.owp_service_id = value.at("owp_service_id");
  config.owp_schema_version = value.at("owp_schema_version"); config.owp_versions = value.at("owp_versions").get<std::vector<std::string>>();
  config.position_subscription_id = value.value("position_subscription_id", "position"); config.position_topic = value.at("position_topic");
  config.signal_topic = value.at("signal_topic"); config.service_status_topic = value.at("service_status_topic");
  config.service_status_rate_hz = value.at("service_status_rate_hz");
  config.mission_name = value.at("mission_name"); config.system_name = value.at("system_name"); config.service_name = value.at("service_name");
  const auto ns = boost::uuids::string_generator{}(value.at("namespace_uuid").get<std::string>());
  boost::uuids::name_generator_sha1 id(ns);
  config.mission_uuid = boost::uuids::to_string(id(config.mission_name)); config.system_uuid = boost::uuids::to_string(id(config.system_name));
  config.service_uuid = boost::uuids::to_string(id(config.system_name + ".service." + config.service_name));
  return config;
}

int main(int argc, char **argv) {
  std::shared_ptr<c2::UciOwpClient> client;
  std::shared_ptr<mel::JobDetail> job;
  std::shared_ptr<mel::ProductRxEndpoint> endpoint;
  std::function<void(std::shared_ptr<mel::ProductRxMetadata>, mel::JobDataPointer, std::size_t)> callback;
  std::jthread receive_thread, status_thread;
  rt::AppConfig config;
  const auto started = std::chrono::steady_clock::now();
  auto ready = std::make_shared<std::atomic_bool>(false);
  auto queue = std::make_shared<Queue>(64);
  std::unique_ptr<PythonEngine> python;
  std::optional<rt::RfMelHandles> handles;
  rt::RuntimeFailure failure;
  auto cleanup = [&] {
    ready->store(false); receive_thread.request_stop(); status_thread.request_stop();
    if (job) { try { static_cast<void>(job->cancelJob()); } catch (...) {} }
    if (endpoint) { try { decltype(callback) empty; endpoint->setDataReadyCallback(empty); } catch (...) {} }
    endpoint.reset();
    if (client) client->close();
    if (receive_thread.joinable()) receive_thread.join();
    if (status_thread.joinable()) status_thread.join();
    job.reset(); python.reset(); handles.reset();
  };
  try {
    if (argc != 3 || std::string(argv[1]) != "--config") throw std::runtime_error("Usage: rf-skill --config runtime.json");
    std::signal(SIGINT, stop_signal); std::signal(SIGTERM, stop_signal); std::signal(SIGPIPE, SIG_IGN);
    json value; std::ifstream input(argv[2]); input >> value;
    config = reference_config(value);
    queue = std::make_shared<Queue>(value.at("queue_capacity"));
    python = std::make_unique<PythonEngine>(value);
    client = std::make_shared<c2::UciOwpClient>(c2::UciOwpClientConfig{
      .uri=config.owp_uri, .service_id=config.owp_service_id, .schema_version=config.owp_schema_version,
      .owp_versions=config.owp_versions, .position_subscription_id=config.position_subscription_id,
      .position_topic=config.position_topic, .signal_topic=config.signal_topic, .service_status_topic=config.service_status_topic,
      .operation_timeout=std::chrono::seconds(5)});
    client->connect_and_subscribe(); rt::publish_service_status(client, config, started, "INITIALIZING");
    handles.emplace(rt::load_rf_mel_handles(rt::load_rf_mel_registry_entry(config.rf_mel_registry_path, config.rf_mel_id)));
    endpoint = rt::create_product_rx_endpoint_with_retry(handles->data_mel, 3, std::chrono::milliseconds(100));
    callback = [weak=std::weak_ptr<Queue>(queue)](auto metadata, auto data, auto count) { if (auto q=weak.lock()) q->receive(metadata,data,count); };
    endpoint->setDataReadyCallback(callback);
    job = rt::request_idd_receive_flow(handles->c2_mel, config, endpoint->getEndpointID());
    {
      // Provider-owned control objects must die before the factory owner dlcloses
      // the library. Do not retain a bare shared_ptr beyond handles.reset().
      const auto admin = handles->admin_mel->getUCIControl();
      if (!admin || !admin->getStatusControl() || !admin->getStatusControl()->commandState(ams::iface::mel::MFA_State::Operate))
        throw std::runtime_error("RF MEL rejected Operate state");
    }
    static_cast<void>(job->finalize());
    ready->store(true);
    receive_thread = std::jthread([&](std::stop_token stop) { try { while (!stop.stop_requested()) static_cast<void>(client->receive_once()); } catch (...) { if (!stop.stop_requested()) failure.record(std::current_exception()); } });
    status_thread = std::jthread(rt::publish_periodic_service_status, client, config, ready, started, std::ref(failure));
    const auto duration_fs = value.at("receive_duration_fs").get<std::int64_t>();
    if (duration_fs <= 0 || duration_fs > 10'000'000'000'000'000LL) throw std::runtime_error("Invalid receive interval duration");
    std::uint32_t interval_id = 0;
    std::uint64_t report_id = 0;
    boost::uuids::name_generator_sha1 ids(boost::uuids::string_generator{}(config.service_uuid));
    auto next_interval = std::chrono::steady_clock::now();
    auto scheduled_until = next_interval;
    rt::RateLimiter limiter(value.at("report_rate_limit_hz"));
    while (!stopping) {
      failure.rethrow_if_set();
      auto now = std::chrono::steady_clock::now();
      if (now >= next_interval) {
        // Refill a bounded look-ahead, never a wall-clock mission deadline.
        std::vector<mel::JobInterval> intervals;
        auto span = std::max<std::int64_t>(duration_fs, 100'000'000'000'000LL);
        for (std::int64_t offset=0; offset<span && intervals.size()<128; offset+=duration_fs) {
          mel::ReceiveEvent event; event.setCenterFrequency(config.center_frequency_hz); event.setSampleFrequency(config.iq_sample_rate_hz);
          event.setDuration(ams::util::math::Femtoseconds(duration_fs));
          mel::JobInterval interval; interval.setIntervalID(++interval_id);
          interval.getSequence().getRxEvents().push_back(event); interval.getSequence().setDuration(ams::util::math::Femtoseconds(duration_fs));
          intervals.push_back(interval);
        }
        job->addJobIntervals(intervals);
        const auto queued = std::chrono::nanoseconds((static_cast<std::int64_t>(intervals.size()) * duration_fs) / 1'000'000);
        scheduled_until += queued;
        next_interval = scheduled_until - queued / 2;
      }
      const auto wait = std::clamp(next_interval - std::chrono::steady_clock::now(),
          std::chrono::steady_clock::duration::zero(),
          std::chrono::duration_cast<std::chrono::steady_clock::duration>(std::chrono::milliseconds(50)));
      auto frame = queue->next(wait); if (!frame) continue;
      for (const auto &group : python->consume(frame->bytes, frame->metadata)) {
        if (!limiter.allow(std::chrono::steady_clock::now())) continue;
        const auto epoch_ns = group.at("timestamp_ns").get<std::int64_t>();
        const auto time = std::chrono::system_clock::time_point(std::chrono::nanoseconds(epoch_ns));
        const auto seconds = std::chrono::system_clock::to_time_t(time);
        std::tm utc{}; gmtime_r(&seconds, &utc); char stamp[64];
        std::strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%S", &utc);
        const auto fraction = epoch_ns % 1'000'000'000;
        char timestamp[96]; std::snprintf(timestamp, sizeof(timestamp), "%s.%09lldZ", stamp, static_cast<long long>(fraction));
        auto message = c2::make_signal_report(config.mission_uuid, config.mission_name, config.system_uuid, config.service_uuid,
          boost::uuids::to_string(ids("SignalReport." + std::to_string(++report_id))),
          boost::uuids::to_string(ids("Signal." + std::to_string(report_id))), timestamp, config.owp_schema_version,
          config.center_frequency_hz, client->latest_position_report());
        auto prototype = message["SignalReport"]["MessageData"]["Signal"][0];
        message["SignalReport"]["MessageData"]["Signal"] = json::array();
        std::size_t index=0;
        for (const auto &signal : group.at("signals")) {
          auto output = prototype; output.merge_patch(signal);
          if (!signal.contains("SignalID")) output["SignalID"] = {{"UUID", boost::uuids::to_string(ids("Signal." + std::to_string(report_id) + "." + std::to_string(index)))}};
          ++index;
          message["SignalReport"]["MessageData"]["Signal"].push_back(output);
        }
        client->publish_signal_report(message);
      }
    }
    cleanup(); return 0;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    if (client) { try { rt::publish_service_status(client, config, started, "DEGRADED"); } catch (...) {} }
    cleanup(); return 1;
  }
}
