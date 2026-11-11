# IRTrans Direct

A Home Assistant custom integration for direct communication with IRTrans LAN devices, including models without a hardware IR database. Learn commands from an original remote, store complete device-native codes in Home Assistant, send them again, and trigger automations from recognized received commands. No separate `irserver` service is used.

Requires Home Assistant **2026.11.0 or later**. Tested with Home Assistant **2026.11.1**, Python 3.14, and a simulated IRTrans device over real binary TCP and UDP sockets. The target physical firmware is LAN **L1.10.30**, IR **E6.08.10**. Version 2.0.2 has also read the target physical device firmware successfully through TCP BINA. Physical IR learning and transmission still need confirmation.

## Install with HACS

Repository: https://github.com/Nojbi2000/HACS-IRTrans

Once the repository is public:

1. Open **HACS → ⋮ → Custom repositories**.
2. Paste `https://github.com/Nojbi2000/HACS-IRTrans` and choose **Integration**.
3. Add it, find **IRTrans Direct**, and select **Download**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration → IRTrans Direct**.
6. Enter the IRTrans device IP, TCP command port **21000**, and HA UDP receive port **21000**.

This project uses the integration domain `irtrans`. Install only one integration using that domain. Existing configuration entries from the original v1.41 integration can retain their host/port settings. The old custom send actions and infrared emitter are replaced by the standard remote actions below.

For manual installation, copy only `custom_components/irtrans` to `/config/custom_components/irtrans` and restart HA.

## Remotes and command buttons (2.2.0)

Open **Settings → Devices & services → IRTrans Direct → Configure**. Choose **Add remote** to create a remote with its own name and 1–32 buttons. Choose **Edit remote / add buttons** to rename it or change its button count. Each remote appears as a separate child device. The original eight positions become **Remote 01**, keeping existing entity IDs and stored codes.

Open the remote device page. Each position has:

- **01 Name**: edit the display name, for example `TV power`.
- **01 Learn · TV power**: press, then briefly press the original remote button when prompted.
- **01 Send · TV power**: transmit the saved code. Send is unavailable and marked `(not learned)` until a code is saved.

Names and codes survive reloads/restarts. Renaming a position does not change the code or its stable automation keys. Existing saved codes are assigned to unused positions on upgrade. Each remote supports 1–32 visible positions; reducing the count hides extra positions without deleting stored codes or names. Increasing it again restores them. Groups and code bindings have stable identifiers independent of display names.

**Last received command** and **Last sent command** show the named codes. Their details include the time, command keys, and, for outgoing commands, whether the request came from a numbered button or an HA action. With HA's Activity/Logbook enabled, named receptions and transmissions are described there; outgoing events retain HA's actor context when available.

An IR code identifies the learned command, not a unique physical handheld remote or the person pressing it. Identical codes emitted by different physical remotes cannot be distinguished by this integration. `unknown command` means received data did not match a stored code.

The standard remote actions below remain available for scripts and automations.

## First-press matching (2.3.1)

The observed 32-bit pulse-distance codes now match by their complete main frame,
decoded through each packet's timing table. A first press without appended repeat
frames can therefore match a learned capture containing those repeats. Timing
indexes may be rearranged or split into similar measured durations. Recognition
requires a supported header, all 32 payload bits, a stop pulse and validated
optional repeat suffixes; it does not trim arbitrary strings or ignore payload
bits. Original stored code bytes and sending behavior stay intact. Other formats
keep their existing comparison. This is a bounded decoder, not a universal IR
protocol recognizer or error-corrector.

## Repeat learning and held commands (2.3.0)

Each position also has **Learn repeat** and **Hold 1 s** controls:

1. Use **Learn** and briefly press the original remote button.
2. Use **Learn repeat** and hold that same button for 2–3 seconds.
3. **Send** transmits the saved main code; **Hold** sends the main code followed by the saved repeat code over one BINA TCP connection.

Both full native records are saved in HA, without guessing which bytes can be discarded. Manufacturer commands HOST_LEARNIRREPEAT (146) and HOST_LEARNIRRAWREPEAT (210) capture the repeat variant. A new main capture clears the old repeat capture, so relearn both if replacing a command. Timeout/storage failure leaves existing captures intact.

Under **Configure → IR output and hold duration**, set a hold duration of 0.1–10 seconds and a repeat interval of 0.03–1 second (default 0.11). The interval is the minimum between send starts; device ACK latency and learned firmware repeat metadata affect actual IR timing. No hardware timing precision is claimed. Hold requires an explicitly learned repeat variant, including for remotes that repeat the full code.

Standard `remote.send_command` accepts `hold_secs` for one command with `num_repeats: 1`. Native HA buttons run a configured duration after one click; holding the UI button does not provide press/release events or stop transmission early. No automatic retransmission occurs after lost acknowledgement.

Receive events and the log include `is_repeat` and `repeat_count`. Matching frames within 0.5 seconds form a repeat chain; a short repeat variant is associated only with the immediately preceding matching learned command. An isolated shared repeat is left unknown. Timing-based grouping cannot distinguish a very fast double press from a held full-code repeat and does not infer release events. Existing events still fire for each packet for compatibility; automations can filter `is_repeat: false` to act on the initial event only.

## Learn a command

Open **Developer tools → Actions → YAML mode**. Replace the entity ID if yours differs:

```yaml
action: remote.learn_command
target:
  entity_id: remote.irtrans_remote
data:
  device: television
  command:
    - power
  timeout: 30
```

Select **Perform action**, point the original remote at IRTrans and briefly press the requested button. Home Assistant shows a notification while waiting. Existing commands are replaced only after successful capture and storage. Codes survive integration reloads and HA restarts.

## Send a stored command

```yaml
action: remote.send_command
target:
  entity_id: remote.irtrans_remote
data:
  device: television
  command:
    - power
```

Optional `num_repeats` sends the full command repeatedly; `delay_secs` sets the pause. LED selection is available in the integration's options. Delete a code with `remote.delete_command` and the same `device` and `command` fields.

## Receive commands

Enable **Broadcast IR Relay** in the IRTrans web interface under **Network Settings → IR Relay**. By default, use the same LAN for IRTrans and HA; broadcasts normally do not cross routers or VLANs. Where supported, **Send IR Relay to** can direct messages to the HA address. IR reception must be enabled on the device. The integration does not change these device settings automatically.

In **Developer tools → Events**, listen to `irtrans_event` and press a learned button. Recognized commands contain `remote`, `button`, `device`, `command`, and the config entry ID. Automations can use:

```yaml
triggers:
  - trigger: event
    event_type: irtrans_event
    event_data:
      remote: television
      button: power
actions:
  - action: persistent_notification.create
    data:
      message: IR power button received
```

`irtrans_received` also reports unknown received commands with a fingerprint and `matched: false`. Receiving a passive signal does not automatically save a replayable command: use the learning action to obtain the full record. Repeated incoming packets can produce repeated events.

## Live receive log (2.1.1)

On the device page, **Activity** records every incoming datagram from the configured IRTrans, including unknown codes and invalid packets. Open the **Receive log** sensor to see the last 20 readable lines, newest first, plus the last packet's source, data, fingerprint and raw hex. Repeated identical receptions are recorded separately. Incoming packets are also logged during learning, while named command automations remain suppressed during learning.

The full in-memory buffer contains up to 100 packets and can be downloaded through the integration's **Download diagnostics** menu. The buffer resets when HA or the integration restarts; Activity retention follows HA Recorder/Logbook settings. The large rolling attributes are excluded from Recorder duplication; compact receive events provide the Activity history. Turning off the Remote entity pauses receive processing and logging.

The log is a monitor, not a learned-code store. Unknown or malformed received packets are never automatically saved as replayable commands.

## Storage and limitations

Codes are stored in HA's `.storage/irtrans_<entry_id>_codes` as complete native IRDATA records encoded in hex, including timing and carrier information. Include HA configuration in backups. Do not edit the storage file manually. HACS updates only integration files; the code library remains outside the integration directory. Deleting and re-adding the configuration entry creates a different storage key.

Unsupported features fail explicitly: IRDATA records larger than 255 bytes, unrecognized special formats, Pronto/CCF import and alternative-code captures. `command_type: raw` is available for exceptional learning cases; matching RAW receive signals also requires compatible device receive settings. Physical compatibility with every remote and hardware variant is not claimed.

The remote entity's turn-on/turn-off actions enable or disable the integration's handling; they do not transmit a TV power command.

## Documentation and validation

- [Český instalační a provozní návod](INSTALACE-CS.txt)
- [Verification record and protocol references](VERIFICATION.txt)
- [Third-party notices](THIRD-PARTY-NOTICES.txt)

Run unit tests with `python3 -m unittest discover -s tests -v`. A real-HA integration test using simulated TCP hardware and UDP relay reception is in `tests/ha_smoke.py`. GitHub workflows are included for protocol/storage tests, HACS validation, and Hassfest.

This repository is prepared for installation as a **HACS custom repository**. It has not been submitted to, reviewed by, or accepted into HACS's default catalog.
