"""Fetch exact public interface/reference revisions; never track a floating branch."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
for name, item in json.loads((root / 'dependencies.json').read_text()).items():
    target = root / 'vendor' / name
    if not target.exists():
        target.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(target)], check=True)
        subprocess.run(['git', '-C', str(target), 'fetch', '--depth', '1', item['url'], item['sha']], check=True)
        subprocess.run(['git', '-C', str(target), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
    actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != item['sha']:
        raise RuntimeError(f'{name}: dependency revision mismatch; expected {item["sha"]}, got {actual}')
    # Keep upstream notices in every exported project/image.
    notices = root / 'third_party' / name
    notices.mkdir(parents=True, exist_ok=True)
    for filename in ('LICENSE', 'NOTICE', 'INTENT.md'):
        source = target / filename
        if source.is_file():
            (notices / filename).write_bytes(source.read_bytes())

# Idle subscriptions have no mission deadline; connection and publication
# transactions remain bounded. A pending read is cancelled explicitly on close.
c2_path = root / 'vendor/rf-fm-demod/src/c2.cpp'
c2_source = c2_path.read_text()
if 'read_subscription_frame' not in c2_source:
    marker = 'void write_text_frame(WebSocketStream &ws, const std::string &frame,'
    subscription = r'''
std::string read_subscription_frame(WebSocketStream &ws) {
  beast::flat_buffer buffer;
  auto &ioc = static_cast<net::io_context &>(ws.get_executor().context());
  ioc.restart();
  boost::system::error_code result;
  ws.async_read(buffer, [&](boost::system::error_code error, std::size_t) { result = error; });
  ioc.run();
  if (result) throw boost::system::system_error(result);
  if (!ws.got_text()) throw std::runtime_error("OWP subscription requires a text frame");
  return beast::buffers_to_string(buffer.data());
}

'''
    old_read = 'read_text_frame(impl_->receive_ws, config_.operation_timeout));\n  if (parsed.op'
    old_close = '  std::scoped_lock lock{receive_mutex_, publish_mutex_};\n  close_websocket(impl_->receive_ws'
    if c2_source.count(marker) != 1 or c2_source.count(old_read) != 1 or c2_source.count(old_close) != 1:
        raise RuntimeError('Pinned OWP source no longer matches subscription cancellation patch')
    c2_source = '#include <boost/asio/post.hpp>\n' + c2_source.replace(marker, subscription + marker)
    c2_source = c2_source.replace(old_read, 'read_subscription_frame(impl_->receive_ws));\n  if (parsed.op')
    c2_source = c2_source.replace(old_close, '''  net::post(impl_->receive_ioc, [impl=impl_.get()] {
    boost::system::error_code ignored;
    beast::get_lowest_layer(impl->receive_ws).socket().cancel(ignored);
  });
''' + old_close)
    c2_path.write_text(c2_source)

# Derive the test provider from the reference skill's pinned interface fixture.
# This is a fixture only: it is never copied to the production image.
source = (root / 'vendor/rf-fm-demod/tests/rf_mel_success_factory_fixture.cpp').read_text()
extension = r'''
#include <rfmel/data/ProductRxEndpoint.h>
#include <rfmel/endpoints/RDMAMemoryRegionParams.h>
#include <nlohmann/json.hpp>
#include <fstream>
#include <chrono>
#include <rfmel/jobs/CommonModulations.h>
class FixtureEndpoint final : public ams::iface::rfmel::ProductRxEndpoint {
  std::function<void(std::shared_ptr<ams::iface::rfmel::ProductRxMetadata>, ams::iface::rfmel::JobDataPointer, size_t)> callback_;
  nlohmann::json config_;
  bool delivered_ = false;
public:
  explicit FixtureEndpoint(const std::string &path) { std::ifstream input(path); input >> config_; }
  ams::iface::rfmel::EndpointID getEndpointID() const override { return 17; }
  ams::iface::rfmel::JobDataFormat getAssignedDataFormat() const override { return ams::iface::rfmel::JobDataFormat::ComplexINT16; }
  ams::iface::rfmel::RDMAMemoryRegionParams getRDMAMemoryRegionParams() const override { return {}; }
  void setDataReadyCallback(decltype(callback_) &value) override { callback_ = value; }
  void deliver(const std::vector<ams::iface::rfmel::JobInterval> &intervals) {
    if (delivered_) return;
    delivered_ = true;
    if (intervals.empty() || intervals[0].getSequence().getRxEvents().empty()) throw std::runtime_error("No native receive schedule");
    if (!callback_) throw std::runtime_error("No native callback");
    if (config_.contains("expected_tx")) {
      const auto &interval = intervals[0]; const auto &sequence = interval.getSequence();
      if (sequence.getTxEvents().size()!=1 || sequence.getRxEvents().size()!=1 || interval.getModulations().size()!=1)
        throw std::runtime_error("Expected one native TX waveform and one RX event");
      const auto &tx = sequence.getTxEvents()[0]; const auto &rx = sequence.getRxEvents()[0];
      if (tx.getStart().count()!=0 || tx.getDuration().count() >= rx.getStart().count() ||
          rx.getStart().count()+rx.getDuration().count() >= sequence.getDuration().count() || tx.getModIndex()!=std::vector<int>{0})
        throw std::runtime_error("TX/guard/RX/guard schedule is invalid");
      const auto &common = std::get<ams::iface::rfmel::CommonModulations>(interval.getModulations()[0]);
      const auto &buffer = std::get<ams::iface::rfmel::WaveformBuffer>(common);
      const auto &samples = *std::get<ams::iface::rfmel::ComplexI16VecPtr>(buffer.getTxSamples());
      nlohmann::json encoded = nlohmann::json::array();
      for (const auto &sample : samples) encoded.push_back({sample.real(), sample.imag()});
      if (encoded != config_.at("expected_tx")) throw std::runtime_error("Native TX samples differ from graph-generated IQ");
      if (tx.getDuration().count() != samples.size()*1000000000000000LL/config_.at("sample_rate_hz").get<std::int64_t>())
        throw std::runtime_error("TX sample rate/duration mismatch");
      std::ofstream output(config_.at("tx_log").get<std::string>());
      output << nlohmann::json{{"samples",samples.size()},{"tx_duration_fs",tx.getDuration().count()},
        {"rx_start_fs",rx.getStart().count()},{"sequence_duration_fs",sequence.getDuration().count()},
        {"iq_matches_graph",true}};
    }
    for (const auto &frame : config_.at("frames")) {
      auto meta = std::make_shared<ams::iface::rfmel::ProductRxMetadata>();
      const auto ns = frame.at("timestamp_ns").get<std::int64_t>();
      meta->setFirstReceiveEventStart(ams::util::math::UTCTime(std::chrono::nanoseconds(ns)));
      meta->setReceiveEvents(std::vector<ams::iface::rfmel::StreamID>{frame.at("stream_id")});
      auto event = intervals[0].getSequence().getRxEvents()[0];
      const auto count = frame.at("samples").size();
      event.setDuration(ams::util::math::Femtoseconds(count * 1000000000000000LL / config_.at("sample_rate_hz").get<std::int64_t>()));
      meta->setReceiveEvents(std::vector<ams::iface::rfmel::ReceiveEvent>{event});
      std::vector<ams::iface::rfmel::MELComplex<std::int16_t>> data;
      for (const auto &sample : frame.at("samples")) data.emplace_back(sample[0].get<std::int16_t>(), sample[1].get<std::int16_t>());
      callback_(meta, data.data(), data.size());
    }
  }
};
std::shared_ptr<FixtureEndpoint> fixture_endpoint;
'''
source = extension + source
old = 'throw std::logic_error("test DataMEL endpoint method was not expected");'
new = '''fixture_endpoint = std::make_shared<FixtureEndpoint>(config_);
    std::promise<ams::iface::mel::ErrorOr<std::shared_ptr<ams::iface::rfmel::ProductRxEndpoint>>> promise;
    promise.set_value(ams::iface::mel::ErrorOr<std::shared_ptr<ams::iface::rfmel::ProductRxEndpoint>>(fixture_endpoint));
    return promise.get_future();'''
if source.count(old) != 1:
    raise RuntimeError('Pinned MEL fixture no longer matches the adapter')
source = source.replace(old, new)
old = 'const std::vector<ams::iface::rfmel::JobInterval> &) override {}'
if source.count(old) != 1:
    raise RuntimeError('Pinned JobInterval fixture no longer matches the adapter')
source = source.replace(old, 'const std::vector<ams::iface::rfmel::JobInterval> &intervals) override { fixture_endpoint->deliver(intervals); }')
(root / 'fixture.cpp').write_text('// Derived from pinned RF FM Demod test fixture; see third_party/rf-fm-demod/LICENSE\n' + source)
