"""This module implements communication with BM6 device."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import logging
from enum import Enum
from typing import Optional
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from Crypto.Cipher import AES

from habluetooth import BaseHaScanner, BluetoothScannerDevice
from homeassistant.core import HomeAssistant
from homeassistant.components.bluetooth import async_scanner_devices_by_address

from .const import (
    CHARACTERISTIC_UUID_NOTIFY,
    CHARACTERISTIC_UUID_WRITE,
    CRYPT_KEY,
    BLEAK_NOTIFY_TIMEOUT,
    CONNECT_MAX_ATTEMPTS,
    REALTIME_READ_ATTEMPTS,
    GATT_DATA_REALTIME,
    GATT_NOTIFY_REALTIME_PREFIX,
    GATT_NOTIFY_VERSION_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

# Sorting value for a scanner that has not reported a signal strength yet
NO_RSSI_VALUE = -127


class BM6RealTimeState(Enum):
    """Enumeration for BM6 device status."""

    BatteryOk = 0
    LowVoltage = 1
    Charging = 2


@dataclass
class BM6RealTimeData:
    """Class to store the real-time data read from the BM6 device."""

    Voltage: float = 0.0  # Battery voltage in V
    Temperature: int = 0  # Temperature in °C
    Percent: int = 0  # Percentage of power in %
    RapidAcceleration: int = 0
    RapidDeceleration: int = 0
    State: BM6RealTimeState = BM6RealTimeState.BatteryOk  # Status of the battery

    def __init__(self, data: str):
        """Initialize BM6ReadTimeData from a hex string."""
        self.Voltage = int(data[14:18], 16) / 100
        temperature_sign = int(data[6:8], 16)
        self.Temperature = int(data[8:10], 16)
        if temperature_sign == 1:
            self.Temperature = -self.Temperature
        self.Percent = int(data[12:14], 16)
        self.RapidAcceleration = int(data[18:22], 16)
        self.RapidDeceleration = int(data[22:26], 16)
        self.State = int(data[10:12], 16)


@dataclass
class BM6Firmware:
    """Class to store the firmware version of the BM6 device."""

    Version: str = None

    def __init__(self, data: str):
        """Initialize BM6Firmware with version data."""
        self.Version = data


@dataclass
class BM6Advertisement:
    """Class to store the advertisement data of the BM6 device."""

    RSSI: int = None
    Scanner: str = None

    def __init__(
        self,
        advertisement_data: Optional[AdvertisementData],
        ha_scanner: Optional[BaseHaScanner],
    ):
        """Initialize BM6Advertisement with advertisement data."""
        self.RSSI = advertisement_data.rssi if advertisement_data else None
        self.Scanner = ha_scanner.name if ha_scanner else None


@dataclass
class BM6Data:
    """Class to store all data read from the BM6 device."""

    Firmware: Optional[BM6Firmware] = None
    RealTime: Optional[BM6RealTimeData] = None
    Advertisement: Optional[BM6Advertisement] = None

    def __init__(
        self,
        advertisement_data: Optional[AdvertisementData],
        ha_scanner: Optional[BaseHaScanner],
    ):
        """Initialize BM6Data with advertisement data."""
        self.Advertisement = BM6Advertisement(advertisement_data, ha_scanner)


class BM6DeviceError(RuntimeError): ...


class BM6Connector:
    """Class to manage the connection to the BM6 device."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str
    ):
        """Initialize the BM6Connector for a device address."""
        self.hass = hass
        self._address: str = address
        self._data: BM6Data | None = None
        self._empty_answers: int = 0

    def _scanner_device(self) -> Optional[BluetoothScannerDevice]:
        """Return the connectable scanner that sees the BM6 with the best signal.

        This only describes where the device is seen from. Which scanner carries
        the connection is decided by Home Assistant itself, which also takes
        free connection slots and earlier connection failures into account.
        """
        scanners: list[BluetoothScannerDevice] = async_scanner_devices_by_address(
            self.hass,
            self._address,
            connectable=True,
        )
        if not scanners:
            return None
        _LOGGER.debug(
            "Device BM6 at %s is seen by scanners %s",
            self._address,
            [
                {
                    "scanner": scanner.scanner.name,
                    "rssi": scanner.advertisement.rssi,
                }
                for scanner in scanners
            ],
        )
        return max(
            scanners,
            key=lambda scanner: (
                NO_RSSI_VALUE
                if scanner.advertisement.rssi is None
                else scanner.advertisement.rssi
            ),
        )

    def _decrypt(self, data: bytearray) -> bytearray:
        """Decrypt the received data using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.decrypt(data)

    def _encrypt(self, data: bytearray) -> bytearray:
        """Encrypt the data to be sent using AES."""
        cipher = AES.new(CRYPT_KEY, AES.MODE_CBC, 16 * b"\0")
        return cipher.encrypt(data)

    async def _notify_callback(
            self, 
            sender: BleakGATTCharacteristic, 
            data: bytearray
    ):
        """Callback function to handle notifications from the BM6 device."""
        message = self._decrypt(data).hex()
        _LOGGER.debug("Received data from BM6 at %s: %s", self._address, message)
        if message.startswith(GATT_NOTIFY_REALTIME_PREFIX):
            real_time = BM6RealTimeData(message)
            if real_time.Voltage <= 0:
                self._empty_answers += 1
                # The BM6 is powered by the battery it measures, so it cannot
                # measure zero volts. A frame like this is one the device sent
                # before it had a reading, not a measurement, and reporting it
                # would look exactly like a flat battery. Wait for a real one.
                _LOGGER.debug(
                    "Ignoring real-time data without a reading from BM6 at %s: %s",
                    self._address,
                    message,
                )
                return
            self._data.RealTime = real_time
            _LOGGER.debug(
                "Decoded real-time data from BM6 at %s: %s",
                self._address,
                self._data.RealTime,
            )
        elif message.startswith(GATT_NOTIFY_VERSION_PREFIX):
            self._data.Firmware = BM6Firmware(message)
            _LOGGER.debug(
                "Decoded firmware version from BM6 at %s: %s",
                self._address,
                self._data.Firmware,
            )

    async def get_data(self) -> BM6Data:
        """Retrieve data from the BM6 device."""
        scanner_device = self._scanner_device()
        if scanner_device is None:
            raise BM6DeviceError(f"Bluetooth device {self._address} not found")
        self._data = BM6Data(scanner_device.advertisement, scanner_device.scanner)
        _LOGGER.debug("Start getting data from the BM6 at %s", self._address)
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                scanner_device.ble_device,
                self._address,
                max_attempts=CONNECT_MAX_ATTEMPTS,
            )
        except Exception as e:
            raise BM6DeviceError(
                f"Could not connect to BM6 at {self._address}: {e}"
            ) from e
        try:
            await self._read_real_time_data(client)
        except BM6DeviceError:
            raise
        except Exception as e:
            raise BM6DeviceError(
                f"Error while reading BM6 at {self._address}: {e}"
            ) from e
        finally:
            with suppress(Exception):
                await client.disconnect()
        return self._data

    async def _read_real_time_data(self, client: BleakClientWithServiceCache) -> None:
        """Ask the BM6 for its real time data and wait for the notification.

        The device can answer with a frame that carries no reading. Asking
        again gets a real one, so the request is repeated inside the same
        timeout instead of failing the whole update.

        The same characteristic also carries the firmware version, which is
        requested with GATT_DATA_VERSION and decoded into BM6Data.Firmware.
        """
        self._data.RealTime = None
        _LOGGER.debug(
            "Subscribe to BM6 at %s characteristic %s",
            self._address,
            CHARACTERISTIC_UUID_NOTIFY,
        )
        # Subscribe before asking, so a fast reply cannot arrive unnoticed
        await client.start_notify(CHARACTERISTIC_UUID_NOTIFY, self._notify_callback)
        try:
            async with asyncio.timeout(BLEAK_NOTIFY_TIMEOUT):
                for attempt in range(1, REALTIME_READ_ATTEMPTS + 1):
                    answered = self._empty_answers
                    _LOGGER.debug(
                        "Write to BM6 at %s characteristic %s, attempt %s",
                        self._address,
                        CHARACTERISTIC_UUID_WRITE,
                        attempt,
                    )
                    await client.write_gatt_char(
                        CHARACTERISTIC_UUID_WRITE,
                        self._encrypt(bytearray.fromhex(GATT_DATA_REALTIME)),
                        response=True,
                    )
                    _LOGGER.debug("Wait for data from BM6 at %s", self._address)
                    while (
                        self._data.RealTime is None
                        and self._empty_answers == answered
                    ):
                        await asyncio.sleep(0.1)
                    if self._data.RealTime is not None:
                        _LOGGER.debug(
                            "Finishing wait for data from BM6 at %s", self._address
                        )
                        return
                    _LOGGER.debug(
                        "BM6 at %s answered without a reading, asking again",
                        self._address,
                    )
        except TimeoutError as e:
            raise BM6DeviceError(
                f"No data received from BM6 at {self._address} "
                f"within {BLEAK_NOTIFY_TIMEOUT} s"
            ) from e
        finally:
            with suppress(Exception):
                await client.stop_notify(CHARACTERISTIC_UUID_NOTIFY)
        raise BM6DeviceError(
            f"BM6 at {self._address} answered without a reading "
            f"{REALTIME_READ_ATTEMPTS} times"
        )
