# Changelog

## 2.0.2

- Use binary TCP BINA sessions for firmware queries, learning and transmission.
- Keep passive IR Relay reception on UDP 21000.
- Parse complete TCP frames and skip relay packets before requested replies.
- Verify the firmware query against the reported L1.10.30 / E6.08.10 device.
- Preserve the HA command library and existing host/port configuration.

Actual infrared learning and transmission still require confirmation with the physical remote.

## 2.0.1

- Show the failed startup stage, target and transport timeout instead of an empty error.

## 2.0.0

- Initial direct binary integration with HA-side learning/storage and receive events.
