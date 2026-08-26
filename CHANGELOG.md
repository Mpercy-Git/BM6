# Changelog

<!--next-version-placeholder-->

## 1.0.9
### Fixed
- The warning logged when a reading fails said the device was probably not transmitting. It is usually the connection that fails while the device is transmitting perfectly well, so it now says what actually went wrong.
- While a BM6 could not be read, it was retried at the configured interval, and every attempt held a connection slot on the Bluetooth proxy for as long as the connect attempts took. The interval is doubled after each failed reading, up to five minutes, and returns to the configured one as soon as the device answers again.
- All sensors could report zero. The BM6 can answer with a real-time frame that carries no reading, and it was decoded and published as an actual measurement of 0.00 V, 0 °C and 0%, which looks like a flat battery and can set off automations that watch for one. Such a frame is ignored now, and the reading is asked for again within the same update, so a device that answers with an empty frame first still reports its real values. When only empty frames arrive, the sensors become unavailable instead of reporting zero.

## 1.0.8
### Fixed
- A BM6 that could not be read while Home Assistant started stopped the integration from setting up at all, so its sensors disappeared from dashboards and automations until the device was reachable again. The sensors are created again and report themselves unavailable until a reading arrives.

## 1.0.7
### Fixed
- Multiple Bluetooth scanners/gateways: a scanner that could not be reached aborted the whole update instead of falling back to the next one, and a scanner that failed after another one had already delivered a reading discarded it. Connections now go through Home Assistant's own connection handling, which picks the scanner with the best signal that still has a free connection slot and retries with a backoff.
- An update could wait forever when the BM6 connected but never sent its data. It now gives up after 10 seconds and reports why.
- The battery state was always reported as unknown when the "calculated by the BM6 device" algorithm was selected.
- The battery percentage was calculated from the previous battery state, so the same reading could be reported first as 0% and then as 100%.
- Selecting a LiFePO4 battery together with 6V made every update fail, leaving all sensors without a value.
- The battery percentage sensor was published without its percent unit.
- BM6 devices were not recognised by their service UUID while adding the integration, only by their manufacturer data.
- Adding the integration failed to read manifest.json when Home Assistant was not started from its configuration directory.
- The device state sensor raised an error when the BM6 reported a state code it did not know.
- The device state sensor now keeps the raw state code as an attribute.

### Changed
- pycryptodome is declared as a requirement, so it is installed together with the integration instead of having to be present already.
- The last reading is kept for at most 15 minutes while the BM6 cannot be read. After that the sensors become unavailable instead of showing an old value indefinitely.
- Voltage, temperature and percentage are reported as regular sensor values, so unit conversion and display precision configured in Home Assistant apply to them.
- A first reading is now awaited while the integration is set up, and setup is retried when it fails, instead of creating sensors without data.
- Home Assistant 2025.1.4 or newer is required, which is what HACS already required to install this integration.

## 1.0.6
- Restored the service UUID matcher, so BM6 devices are discovered again.
## 1.0.5
- Removed device identifiers that matched too broadly and picked up other devices.
- Keep using the last reading when the BM6 cannot be read.
## 1.0.4
- First release of this fork.

## 1.0.3
- Added support for multiple Bluetooth scanners/gateways. Now the BM6 device is supported by more than one Bluetooth scanner/gateway. The scanner with the best signal strength is automatically selected to connect to the BM6. If you have more than one scanner/gateway, this version is just for you.
## 1.0.2
- Improvement of code especially translation.
## 1.0.1
- Improvement of code especially translation.
## 1.0.0
- First release.