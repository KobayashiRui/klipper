# Load Cell Implementation
#
# Copyright (C) 2024 Gareth Farrington <gareth@waves.ky>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import hx71x
from . import ads1220
from .bulk_sensor import BatchWebhooksClient

# Helper for event driven webhooks (i.e. non polling based data source)
class WebhooksHelper(object):
    def __init__(self, printer):
        self.printer = printer
        self.client_cbs = []
        self.webhooks_start_resp = {}
    # send data to clients
    def send(self, msg):
        for client_cb in list(self.client_cbs):
            res = client_cb(msg)
            if not res:
                # This client no longer needs updates - unregister it
                self.client_cbs.remove(client_cb)
    # Client registration
    def add_client(self, client_cb):
        self.client_cbs.append(client_cb)
    # Webhooks registration
    def _add_api_client(self, web_request):
        whbatch = BatchWebhooksClient(web_request)
        self.add_client(whbatch.handle_batch)
        web_request.send(self.webhooks_start_resp)
    def add_mux_endpoint(self, path, key, value, webhooks_start_resp):
        self.webhooks_start_resp = webhooks_start_resp
        wh = self.printer.lookup_object('webhooks')
        wh.register_mux_endpoint(path, key, value, self._add_api_client)

# Adapter for WebhooksHelper that transforms the response using a function
# Anything that implements the add_client contact can be a mgs source
# outputs to its own clients
class WebhooksTransformer(WebhooksHelper):
    def __init__(self, printer, msg_source, transform_fn):
        super(WebhooksTransformer, self).__init__(printer)
        self.msg_source = msg_source
        self.transform_fn = transform_fn
        self.is_started = False
    def _start(self):
        if self.is_started:
            return
        self.is_started = True
        self.msg_source.add_client(self._transform_batch)
    def _stop(self):
        self.is_started = False
        del self.client_cbs[:]
    def _transform_batch(self, msg):
        try:
            msg_transformed = self.transform_fn(msg)
        except self.printer.command_error:
            logging.exception("BatchBulkTransformer transform_batch error")
            self._stop()
            return self.is_started
        if not msg_transformed:
            return self.is_started
        self.send(msg_transformed)
        if len(self.client_cbs) == 0:
            self._stop()
        return self.is_started
    def add_client(self, client_cb):
        self.client_cbs.append(client_cb)
        self._start()


class LoadCellCommandHelper:
    def __init__(self, config, load_cell):
        try:
            import numpy as np
        except:
            raise config.error("LoadCell requires the numpy module")
        self.printer = config.get_printer()
        self.load_cell = load_cell
        name_parts = config.get_name().split()
        self.name = name_parts[-1]
        self.register_commands(self.name)
        logging.info("Registering commands as: %s" % (self.name))
        if len(name_parts) == 1:
            # TODO: when this is a [load_cell_probe], what should happen here?
            # TODO: What if there are multiple probes?
            # TODO: Duplicate names: [load_cell foo] [load_cell_probe foo] ??
            if (self.name == "load_cell"
                    or not config.has_section("load_cell")):
                logging.info("Registering default commands for: %s" % (self.name))
                #self.register_commands(None)
    def register_commands(self, name):
        # Register commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_mux_command("TARE_LOAD_CELL", "LOAD_CELL", name,
                                   self.cmd_TARE_LOAD_CELL,
                                   desc=self.cmd_TARE_LOAD_CELL_help)
        #gcode.register_mux_command("CALIBRATE_LOAD_CELL", "LOAD_CELL", name,
        #                           self.cmd_CALIBRATE_LOAD_CELL,
        #                           desc=self.cmd_CALIBRATE_LOAD_CELL_help)
        gcode.register_mux_command("READ_LOAD_CELL", "LOAD_CELL", name,
                                   self.cmd_READ_LOAD_CELL,
                                   desc=self.cmd_READ_LOAD_CELL_help)
        gcode.register_mux_command("LOAD_CELL_DIAGNOSTIC", "LOAD_CELL", name,
                                   self.cmd_LOAD_CELL_DIAGNOSTIC,
                                   desc=self.cmd_LOAD_CELL_DIAGNOSTIC_help)
    cmd_TARE_LOAD_CELL_help = "Set the Zero point of the load cell"
    def cmd_TARE_LOAD_CELL(self, gcmd):
        tare_counts = self.load_cell.avg_counts()
        tare_percent = self.load_cell.counts_to_percent(tare_counts)
        self.load_cell.tare(tare_counts)
        gcmd.respond_info("Load cell tare value: %.2f%% (%i)"
                          % (tare_percent, tare_counts))
    #cmd_CALIBRATE_LOAD_CELL_help = "Start interactive calibration tool"
    #def cmd_CALIBRATE_LOAD_CELL(self, gcmd):
    #    LoadCellGuidedCalibrationHelper(self.printer, self.load_cell)
    cmd_READ_LOAD_CELL_help = "Take a reading from the load cell"
    def cmd_READ_LOAD_CELL(self, gcmd):
        counts = self.load_cell.avg_counts()
        percent = self.load_cell.counts_to_percent(counts)
        force = self.load_cell.counts_to_grams(counts)
        if percent >= 100 or percent <= -100:
            gcmd.respond_info("Err (%.2f%%)" % (percent,))
        if force is None:
            gcmd.respond_info("---.-g (%.2f%%)" % (percent,))
        else:
            gcmd.respond_info("%.1fg (%.2f%%)" % (force, percent))
    cmd_LOAD_CELL_DIAGNOSTIC_help = "Check the health of the load cell"
    def cmd_LOAD_CELL_DIAGNOSTIC(self, gcmd):
        import numpy as np
        gcmd.respond_info("Collecting load cell data for 10 seconds...")
        collector = self.load_cell.get_collector()
        reactor = self.printer.get_reactor()
        collector.start_collecting()
        reactor.pause(reactor.monotonic() + 10.)
        samples = collector.stop_collecting()
        counts = np.asarray(samples)[:, 2].astype(int)
        range_min, range_max = self.load_cell.saturation_range()
        good_count = 0
        saturation_count = 0
        for sample in counts:
            if sample >= range_max or sample <= range_min:
                saturation_count += 1
            else:
                good_count += 1
        unique_counts = np.unique(counts)
        gcmd.respond_info("Samples Collected: %i" % (len(samples)))
        if len(samples) > 2:
            sensor_sps = self.load_cell.sensor.get_samples_per_second()
            sps = float(len(samples)) / (samples[-1][0] - samples[0][0])
            gcmd.respond_info("Measured samples per second: %.1f, "
                              "configured: %.1f" % (sps, sensor_sps))
        gcmd.respond_info("Good samples: %i, Saturated samples: %i, Unique"
                          " values: %i" % (good_count, saturation_count,
                          len(unique_counts)))
        max_pct = self.load_cell.counts_to_percent(np.amax(counts))
        min_pct = self.load_cell.counts_to_percent(np.amin(counts))
        gcmd.respond_info("Sample range: [%.2f%% to %.2f%%]"
                          % (min_pct, max_pct))
        gcmd.respond_info("Sample range / sensor capacity: %.5f%%"
                          % ((max_pct - min_pct) / 2.))

RETRY_DELAY = 0.05  # 20Hz
class LoadCellSampleCollector:
    def __init__(self, printer, load_cell):
        self._printer = printer
        self._load_cell = load_cell
        self._reactor = printer.get_reactor()
        self._mcu = load_cell.sensor.get_mcu()
        self.min_time = 0.
        self.max_time = float("inf")
        self.min_count = float("inf")  # In Python 3.5 math.inf is better
        self.is_started = False
        self._samples = []
    def _on_samples(self, msg):
        if not self.is_started:
            return False  # already stopped, ignore
        samples = msg['data']
        for sample in samples:
            time = sample[0]
            if self.min_time <= time <= self.max_time:
                self._samples.append(sample)
            if time > self.max_time:
                self.is_started = False
        if len(self._samples) >= self.min_count:
            self.is_started = False
        return self.is_started
    def _finish_collecting(self):
        self.is_started = False
        self.min_time = 0.
        self.max_time = float("inf")
        self.min_count = float("inf")  # In Python 3.5 math.inf is better
        samples = self._samples
        self._samples = []
        return samples
    def _collect_until(self, timeout):
        self.start_collecting()
        while self.is_started:
            now = self._reactor.monotonic()
            if self._mcu.estimated_print_time(now) > timeout:
                raise self._printer.command_error(
                                Exception("LoadCellSampleCollector timed out"))
            self._reactor.pause(now + RETRY_DELAY)
        return self._finish_collecting()
    # start collecting with no automatic end to collection
    def start_collecting(self, min_time=None):
        if self.is_started:
            return
        self.min_time = min_time if min_time is not None else self.min_time
        self.is_started = True
        self._load_cell.add_client(self._on_samples)
    # stop collecting immediately and return results
    def stop_collecting(self):
        return self._finish_collecting()
    # block execution until at least min_count samples are collected
    def collect_min(self, min_count=1):
        self.min_count = min_count
        if len(self._samples) >= min_count:
            return self._finish_collecting()
        now = self._reactor.monotonic()
        print_time = self._mcu.estimated_print_time(now)
        sps = self._load_cell.sensor.get_samples_per_second()
        return self._collect_until(print_time + 1. + (min_count / sps))
    # block execution until a sample is returned with a timestamp after max_time
    def collect_until(self, max_time=None):
        self.max_time = max_time
        if len(self._samples) and self._samples[-1][0] >= max_time:
            return self._finish_collecting()
        return self._collect_until(self.max_time + 1.)

# Printer class that controls a load cell
class LoadCell:
    def __init__(self, config, sensor):
        self.printer = printer = config.get_printer()
        self.sensor = sensor   # must implement BulkAdcSensor

        LoadCellCommandHelper(config, self)

        # webhooks support
        self.wh_transformer = WebhooksTransformer(printer, sensor,
                                                  self._sensor_data_event)
        header = {"header": ["time", "force (g)", "counts", "tare_counts"]}
        self.wh_transformer.add_mux_endpoint("load_cell/dump_force",
                                             "load_cell", self.name, header)
        # startup, when klippy is ready, start capturing data
        printer.register_event_handler("klippy:ready", self._handle_ready)

    def _handle_ready(self):
        self.add_client(self._on_sample)
        # announce calibration status on ready
        if self.is_calibrated():
            self.printer.send_event("load_cell:calibrate", self)
        if self.is_tared():
            self.printer.send_event("load_cell:tare", self)

    # convert raw counts to grams and broadcast to clients
    def _sensor_data_event(self, msg):
        data = msg.get("data")
        if data is None:
            return None
        samples = []
        for row in data:
            # [time, grams, counts, tare_counts]
            samples.append([row[0], self.counts_to_grams(row[1]), row[1],
                            self.tare_counts])
        return {'data': samples}
    # get internal events of force data
    def add_client(self, callback):
        self.wh_transformer.add_client(callback)

    def saturation_range(self):
        return self.sensor.get_range()

    def counts_to_percent(self, counts):
        range_min, range_max = self.saturation_range()
        return (float(counts) / float(range_max)) * 100.
    # read 1 second of load cell data and average it
    # performs safety checks for saturation
    def avg_counts(self, num_samples=None):
        import numpy as np
        if num_samples is None:
            num_samples = self.sensor.get_samples_per_second()
        samples = self.get_collector().collect_min(num_samples)
        # check samples for saturated readings
        range_min, range_max = self.saturation_range()
        for sample in samples:
            if sample[2] >= range_max or sample[2] <= range_min:
                raise self.printer.command_error(
                    "Some samples are saturated (+/-100%)")
        counts = np.asarray(samples)[:, 2].astype(float)
        return np.average(counts)

    def _on_sample(self, msg):
        return True

    def get_collector(self):
        return LoadCellSampleCollector(self.printer, self)

    def get_sensor(self):
        return self.sensor

def load_config(config):
    # Sensor types
    sensors = {}
    sensors.update(hx71x.HX71X_SENSOR_TYPES)
    sensors.update(ads1220.ADS1220_SENSOR_TYPE)
    sensor_class = config.getchoice('sensor_type', sensors)
    return LoadCell(config, sensor_class(config))

def load_config_prefix(config):
    return load_config(config)
