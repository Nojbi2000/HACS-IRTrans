# Changelog

## 2.3.0

- Add Learn repeat using manufacturer normal/raw repeat capture commands.
- Persist separate repeat records transactionally with existing main codes.
- Add timed Hold buttons and standard remote.send_command hold_secs.
- Send initial and subsequent repeat codes on one acknowledged BINA connection.
- Add configurable hold duration and minimum repeat interval.
- Mark contextual receive repeat chains without attributing isolated shared repeats.

## 2.2.0

- Add independently named remotes, each shown as a child device with 1–32 command positions.
- Add Configure menus for adding, renaming and resizing remotes and selecting LED output.
- Move existing positions into Remote 01 while preserving unique IDs and stored codes.
- Preserve hidden positions and names when reducing button counts.
- Use current HA device relationships via via_device_id.

## 2.1.1

- Log every received device datagram, including unknown, repeated and invalid data, in Activity.
- Add Receive log with the last 20 readable entries and detailed last-packet data.
- Keep a bounded 100-packet in-memory diagnostics buffer.
- Monitor receive traffic during learning without firing named command automations.

## 2.1.0

- Add named command positions to the device page with editable names and Learn/Send buttons.
- Default to eight positions, configurable from 1 to 32.
- Preserve names and bindings with the HA code library, including existing-code migration.
- Show Last sent command and describe named IR receive/send events in Activity.
- Retain the HA actor context on transmissions; include position labels in events.

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
