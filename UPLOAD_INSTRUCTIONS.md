# Upload Instructions for Traffic Light

## Required Files to Upload

You need to upload these 3 files to your Pico root directory:

1. **mqtt_simple.py** - MQTT library
2. **tl_secrets.py** - WiFi/MQTT credentials and host IPs (private, edit before uploading)
3. **trafficlight_toggle.py** - Main program (can be renamed to main.py)

## Upload Steps

### Simple Method (Recommended):

1. **Upload mqtt_simple.py:**
   - Open `mqtt_simple.py` in VS Code
   - Right-click → "Upload current file to Pico"

2. **Upload the main program:**
   - Open `trafficlight_toggle.py`
   - Right-click → "Upload current file to Pico"
   - When asked for filename, name it `main.py` if you want auto-start

3. **Reset Pico:**
   - Unplug and replug USB
   - If named `main.py`, it will start automatically

## Verify Installation

In MicroPico vREPL:
```python
import os
print('Files:', os.listdir('/'))

# Test imports
from mqtt_simple import MQTTClient
print('MQTT library OK!')
```

## To Run Manually

If you didn't name it main.py:
```python
import trafficlight_toggle
```

That's it - just 3 files!

## Changing the timezone remotely

Publish to `trafficlight/timezone/set` (standard UTC offset, e.g. `0` for UK, `1` for CET)
and `trafficlight/dst/set` (`ON`/`OFF`). Both also appear in Home Assistant as config entities
and are saved on the Pico (`timezone.cfg`). The clock re-syncs itself daily, and a watchdog
reboots the board if the main loop stalls.


## Thonny: "connection lost -- EOF" during upload

The program previously enabled an eight-second hardware watchdog. Stopping the
program in Thonny stops feeding it, so the board may reset and disconnect USB
while a file is uploading. `WATCHDOG_ENABLED` now defaults to `False` for editing.
This also disables automatic recovery from a hung program.

Changing the local file does not stop a watchdog already running on the board.
If the board keeps disconnecting:

1. Reconnect in Thonny and press Stop. In the Shell, promptly run the following
   to temporarily prevent the existing program from starting on the next boot:

   ```python
   import os
   os.rename('main.py', 'main_upload_backup.py')
   ```

   Use a different backup name if that name already exists. This step assumes
   `main.py` is the startup file; it works with either the small import wrapper
   in this project or a main program saved directly as `main.py`.
2. Disconnect **all** board power, including USB and any external supply, then
   reconnect. This clears the existing watchdog and boots without the old app.
3. Upload `trafficlight_toggle.py` using Thonny's **Save as > Raspberry Pi Pico**.
   Keep its filename if using this project's `main.py` import wrapper. Ensure
   `mqtt_simple.py` and `tl_secrets.py` are also present on the board.
4. Restore the startup wrapper by uploading this project's `main.py`. Restart
   the board and check the Shell for startup errors.

If EOF still occurs with the startup program disabled after a full power cycle,
check Thonny's selected interpreter/serial port, the USB data cable, and the board
power supply. Capture the full Thonny error and whether it happens while saving
or only after the program starts.
