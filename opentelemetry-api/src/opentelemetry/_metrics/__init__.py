# Copyright The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# pylint: disable=too-many-ancestors
# type: ignore

# FIXME enhance the documentation of this module
"""
This module provides abstract and concrete (but noop) classes that can be used
to generate metrics.
"""


from abc import ABC, abstractmethod
from logging import getLogger
from os import environ
from threading import Lock
from typing import List, Optional, cast

from opentelemetry._metrics.instrument import (
    Counter,
    DefaultCounter,
    DefaultHistogram,
    DefaultObservableCounter,
    DefaultObservableGauge,
    DefaultObservableUpDownCounter,
    DefaultUpDownCounter,
    Histogram,
    ObservableCounter,
    ObservableGauge,
    ObservableUpDownCounter,
    UpDownCounter,
    _ProxyCounter,
    _ProxyHistogram,
    _ProxyInstrument,
    _ProxyObservableCounter,
    _ProxyObservableGauge,
    _ProxyObservableUpDownCounter,
    _ProxyUpDownCounter,
)
from opentelemetry.environment_variables import (
    _OTEL_PYTHON_METER_PROVIDER as OTEL_PYTHON_METER_PROVIDER,
)
from opentelemetry.util._once import Once
from opentelemetry.util._providers import _load_provider

_logger = getLogger(__name__)


class MeterProvider(ABC):
    @abstractmethod
    def get_meter(
        self,
        name,
        version=None,
        schema_url=None,
    ) -> "Meter":
        pass


class NoOpMeterProvider(MeterProvider):
    """The default MeterProvider used when no MeterProvider implementation is available."""

    def get_meter(
        self,
        name,
        version=None,
        schema_url=None,
    ) -> "Meter":
        """Returns a NoOpMeter."""
        super().get_meter(name, version=version, schema_url=schema_url)
        return NoOpMeter(name, version=version, schema_url=schema_url)


class _ProxyMeterProvider(MeterProvider):
    def __init__(self) -> None:
        self._lock = Lock()
        self._meters: List[_ProxyMeter] = []
        self._real_meter_provider: Optional[MeterProvider] = None

    def get_meter(
        self,
        name,
        version=None,
        schema_url=None,
    ) -> "Meter":
        with self._lock:
            if self._real_meter_provider is not None:
                return self._real_meter_provider.get_meter(
                    name, version, schema_url
                )

            meter = _ProxyMeter(name, version=version, schema_url=schema_url)
            self._meters.append(meter)
            return meter

    def on_set_meter_provider(self, meter_provider: MeterProvider) -> None:
        with self._lock:
            self._real_meter_provider = meter_provider
            for meter in self._meters:
                meter.on_set_meter_provider(meter_provider)


class Meter(ABC):
    def __init__(self, name, version=None, schema_url=None):
        super().__init__()
        self._name = name
        self._version = version
        self._schema_url = schema_url
        self._instrument_names = set()

    @property
    def name(self):
        return self._name

    @property
    def version(self):
        return self._version

    @property
    def schema_url(self):
        return self._schema_url

    # FIXME check that the instrument name has not been used already

    @abstractmethod
    def create_counter(self, name, unit="", description="") -> Counter:
        """Creates a `Counter` instrument

        Args:
            name: The name of the instrument to be created
            unit: The unit for measurements this instrument reports. For
                example, ``By`` for bytes. UCUM units are recommended.
            description: A description for this instrument and what it measures.
        """

    @abstractmethod
    def create_up_down_counter(
        self, name, unit="", description=""
    ) -> UpDownCounter:
        """Creates an `UpDownCounter` instrument

        Args:
            name: The name of the instrument to be created
            unit: The unit for measurements this instrument reports. For
                example, ``By`` for bytes. UCUM units are recommended.
            description: A description for this instrument and what it measures.
        """

    @abstractmethod
    def create_observable_counter(
        self, name, callback, unit="", description=""
    ) -> ObservableCounter:
        """Creates an `ObservableCounter` instrument

        An observable counter observes a monotonically increasing count by
        calling a provided callback which returns multiple
        :class:`~opentelemetry._metrics.measurement.Measurement`.

        For example, an observable counter could be used to report system CPU
        time periodically. Here is a basic implementation::

            def cpu_time_callback() -> Iterable[Measurement]:
                measurements = []
                with open("/proc/stat") as procstat:
                    procstat.readline()  # skip the first line
                    for line in procstat:
                        if not line.startswith("cpu"): break
                        cpu, *states = line.split()
                        measurements.append(Measurement(int(states[0]) // 100, {"cpu": cpu, "state": "user"}))
                        measurements.append(Measurement(int(states[1]) // 100, {"cpu": cpu, "state": "nice"}))
                        measurements.append(Measurement(int(states[2]) // 100, {"cpu": cpu, "state": "system"}))
                        # ... other states
                return measurements

            meter.create_observable_counter(
                "system.cpu.time",
                callback=cpu_time_callback,
                unit="s",
                description="CPU time"
            )

        To reduce memory usage, you can use generator callbacks instead of
        building the full list::

            def cpu_time_callback() -> Iterable[Measurement]:
                with open("/proc/stat") as procstat:
                    procstat.readline()  # skip the first line
                    for line in procstat:
                        if not line.startswith("cpu"): break
                        cpu, *states = line.split()
                        yield Measurement(int(states[0]) // 100, {"cpu": cpu, "state": "user"})
                        yield Measurement(int(states[1]) // 100, {"cpu": cpu, "state": "nice"})
                        # ... other states

        Alternatively, you can pass a generator directly instead of a callback,
        which should return iterables of
        :class:`~opentelemetry._metrics.measurement.Measurement`::

            def cpu_time_callback(states_to_include: set[str]) -> Iterable[Iterable[Measurement]]:
                while True:
                    measurements = []
                    with open("/proc/stat") as procstat:
                        procstat.readline()  # skip the first line
                        for line in procstat:
                            if not line.startswith("cpu"): break
                            cpu, *states = line.split()
                            if "user" in states_to_include:
                                measurements.append(Measurement(int(states[0]) // 100, {"cpu": cpu, "state": "user"}))
                            if "nice" in states_to_include:
                                measurements.append(Measurement(int(states[1]) // 100, {"cpu": cpu, "state": "nice"}))
                            # ... other states
                    yield measurements

            meter.create_observable_counter(
                "system.cpu.time",
                callback=cpu_time_callback({"user", "system"}),
                unit="s",
                description="CPU time"
            )

        Args:
            name: The name of the instrument to be created
            callback: A callback that returns an iterable of
                :class:`~opentelemetry._metrics.measurement.Measurement`.
                Alternatively, can be a generator that yields iterables of
                :class:`~opentelemetry._metrics.measurement.Measurement`.
            unit: The unit for measurements this instrument reports. For
                example, ``By`` for bytes. UCUM units are recommended.
            description: A description for this instrument and what it measures.
        """

    @abstractmethod
    def create_histogram(self, name, unit="", description="") -> Histogram:
        """Creates a `Histogram` instrument

        Args:
            name: The name of the instrument to be created
            unit: The unit for measurements this instrument reports. For
                example, ``By`` for bytes. UCUM units are recommended.
            description: A description for this instrument and what it measures.
        """

    @abstractmethod
    def create_observable_gauge(
        self, name, callback, unit="", description=""
    ) -> ObservableGauge:
        """Creates an `ObservableGauge` instrument

        Args:
            name: The name of the instrument to be created
            callback: A callback that returns an iterable of
                :class:`~opentelemetry._metrics.measurement.Measurement`.
                Alternatively, can be a generator that yields iterables of
                :class:`~opentelemetry._metrics.measurement.Measurement`.
            unit: The unit for measurements this instrument reports. For
                example, ``By`` for bytes. UCUM units are recommended.
            description: A description for this instrument and what it measures.
        """

    @abstractmethod
    def create_observable_up_down_counter(
        self, name, callback, unit="", description=""
    ) -> ObservableUpDownCounter:
        """Creates an `ObservableUpDownCounter` instrument

        Args:
            name: The name of the instrument to be created
            callback: A callback that returns an iterable of
                :class:`~opentelemetry._metrics.measurement.Measurement`.
                Alternatively, can be a generator that yields iterables of
                :class:`~opentelemetry._metrics.measurement.Measurement`.
            unit: The unit for measurements this instrument reports. For
                example, ``By`` for bytes. UCUM units are recommended.
            description: A description for this instrument and what it measures.
        """


class _ProxyMeter(Meter):
    def __init__(
        self,
        name,
        version=None,
        schema_url=None,
    ):
        super().__init__(name, version=version, schema_url=schema_url)
        self._lock = Lock()
        self._instruments: List[_ProxyInstrument] = []
        self._real_meter: Optional[Meter] = None

    def on_set_meter_provider(self, meter_provider: MeterProvider) -> None:
        """Called when a real meter provider is set on the creating _ProxyMeterProvider

        Creates a real backing meter for this instance and notifies all created
        instruments so they can create real backing instruments.
        """
        real_meter = meter_provider.get_meter(
            self._name, self._version, self._schema_url
        )

        with self._lock:
            self._real_meter = real_meter
            # notify all proxy instruments of the new meter so they can create
            # real instruments to back themselves
            for instrument in self._instruments:
                instrument.on_meter_set(real_meter)

    def create_counter(self, name, unit="", description="") -> Counter:
        with self._lock:
            if self._real_meter:
                return self._real_meter.create_counter(name, unit, description)
            proxy = _ProxyCounter(name, unit, description)
            self._instruments.append(proxy)
            return proxy

    def create_up_down_counter(
        self, name, unit="", description=""
    ) -> UpDownCounter:
        with self._lock:
            if self._real_meter:
                return self._real_meter.create_up_down_counter(
                    name, unit, description
                )
            proxy = _ProxyUpDownCounter(name, unit, description)
            self._instruments.append(proxy)
            return proxy

    def create_observable_counter(
        self, name, callback, unit="", description=""
    ) -> ObservableCounter:
        with self._lock:
            if self._real_meter:
                return self._real_meter.create_observable_counter(
                    name, callback, unit, description
                )
            proxy = _ProxyObservableCounter(
                name, callback, unit=unit, description=description
            )
            self._instruments.append(proxy)
            return proxy

    def create_histogram(self, name, unit="", description="") -> Histogram:
        with self._lock:
            if self._real_meter:
                return self._real_meter.create_histogram(
                    name, unit, description
                )
            proxy = _ProxyHistogram(name, unit, description)
            self._instruments.append(proxy)
            return proxy

    def create_observable_gauge(
        self, name, callback, unit="", description=""
    ) -> ObservableGauge:
        with self._lock:
            if self._real_meter:
                return self._real_meter.create_observable_gauge(
                    name, callback, unit, description
                )
            proxy = _ProxyObservableGauge(
                name, callback, unit=unit, description=description
            )
            self._instruments.append(proxy)
            return proxy

    def create_observable_up_down_counter(
        self, name, callback, unit="", description=""
    ) -> ObservableUpDownCounter:
        with self._lock:
            if self._real_meter:
                return self._real_meter.create_observable_up_down_counter(
                    name,
                    callback,
                    unit,
                    description,
                )
            proxy = _ProxyObservableUpDownCounter(
                name, callback, unit=unit, description=description
            )
            self._instruments.append(proxy)
            return proxy


class NoOpMeter(Meter):
    """The default Meter used when no Meter implementation is available.

    All operations are no-op.
    """

    def create_counter(self, name, unit="", description="") -> Counter:
        """Returns a no-op Counter."""
        super().create_counter(name, unit=unit, description=description)
        return DefaultCounter(name, unit=unit, description=description)

    def create_up_down_counter(
        self, name, unit="", description=""
    ) -> UpDownCounter:
        """Returns a no-op UpDownCounter."""
        super().create_up_down_counter(
            name, unit=unit, description=description
        )
        return DefaultUpDownCounter(name, unit=unit, description=description)

    def create_observable_counter(
        self, name, callback, unit="", description=""
    ) -> ObservableCounter:
        """Returns a no-op ObservableCounter."""
        super().create_observable_counter(
            name, callback, unit=unit, description=description
        )
        return DefaultObservableCounter(
            name,
            callback,
            unit=unit,
            description=description,
        )

    def create_histogram(self, name, unit="", description="") -> Histogram:
        """Returns a no-op Histogram."""
        super().create_histogram(name, unit=unit, description=description)
        return DefaultHistogram(name, unit=unit, description=description)

    def create_observable_gauge(
        self, name, callback, unit="", description=""
    ) -> ObservableGauge:
        """Returns a no-op ObservableGauge."""
        super().create_observable_gauge(
            name, callback, unit=unit, description=description
        )
        return DefaultObservableGauge(
            name,
            callback,
            unit=unit,
            description=description,
        )

    def create_observable_up_down_counter(
        self, name, callback, unit="", description=""
    ) -> ObservableUpDownCounter:
        """Returns a no-op ObservableUpDownCounter."""
        super().create_observable_up_down_counter(
            name, callback, unit=unit, description=description
        )
        return DefaultObservableUpDownCounter(
            name,
            callback,
            unit=unit,
            description=description,
        )


_METER_PROVIDER_SET_ONCE = Once()
_METER_PROVIDER: Optional[MeterProvider] = None
_PROXY_METER_PROVIDER = _ProxyMeterProvider()


def get_meter(
    name: str,
    version: str = "",
    meter_provider: Optional[MeterProvider] = None,
) -> "Meter":
    """Returns a `Meter` for use by the given instrumentation library.

    This function is a convenience wrapper for
    opentelemetry.trace.MeterProvider.get_meter.

    If meter_provider is omitted the current configured one is used.
    """
    if meter_provider is None:
        meter_provider = get_meter_provider()
    return meter_provider.get_meter(name, version)


def _set_meter_provider(meter_provider: MeterProvider, log: bool) -> None:
    def set_mp() -> None:
        global _METER_PROVIDER  # pylint: disable=global-statement
        _METER_PROVIDER = meter_provider

        # gives all proxies real instruments off the newly set meter provider
        _PROXY_METER_PROVIDER.on_set_meter_provider(meter_provider)

    did_set = _METER_PROVIDER_SET_ONCE.do_once(set_mp)

    if log and not did_set:
        _logger.warning("Overriding of current MeterProvider is not allowed")


def set_meter_provider(meter_provider: MeterProvider) -> None:
    """Sets the current global :class:`~.MeterProvider` object.

    This can only be done once, a warning will be logged if any furter attempt
    is made.
    """
    _set_meter_provider(meter_provider, log=True)


def get_meter_provider() -> MeterProvider:
    """Gets the current global :class:`~.MeterProvider` object."""

    if _METER_PROVIDER is None:
        if OTEL_PYTHON_METER_PROVIDER not in environ.keys():
            return _PROXY_METER_PROVIDER

        meter_provider: MeterProvider = _load_provider(
            OTEL_PYTHON_METER_PROVIDER, "meter_provider"
        )
        _set_meter_provider(meter_provider, log=False)

    # _METER_PROVIDER will have been set by one thread
    return cast("MeterProvider", _METER_PROVIDER)
