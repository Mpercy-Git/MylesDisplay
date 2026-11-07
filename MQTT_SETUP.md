# MQTT Setup Guide

## Automatic Discovery (Recommended)

The traffic light now uses **Home Assistant MQTT auto-discovery**. No manual configuration needed!

### Quick Setup:

1. **Update trafficlight_toggle.py** with your MQTT broker IP
2. **Upload to Pico** and run
3. **Check Home Assistant:**
   - Go to Settings > Devices & Services > MQTT
   - Look for "Traffic Light Display" 
   - All controls will be automatically created!

---

## Configuration Steps

### 1. Update trafficlight_toggle.py
Edit these lines in the code:
```python
MQTT_BROKER = "192.168.1.100"  # Your Home Assistant IP address
MQTT_PORT = 1883               # Default MQTT port
MQTT_USER = ""                 # MQTT username (if required)
MQTT_PASSWORD = ""             # MQTT password (if required)
MQTT_ENABLED = True            # Set to False to disable MQTT
```

### 2. Home Assistant Auto-Discovery

**No configuration needed!** The device will automatically appear in Home Assistant.

After the Pico connects, check:
- Settings > Devices & Services > MQTT
- Click on "X devices" (where X is a number)
- Find "Traffic Light Display"

You'll see:
- **Sensors**: Traffic Light Mode, Traffic Light Color
- **Buttons**: Red, Yellow, Green, Auto, Clock, Toggle

**Optional:** If auto-discovery doesn't work, check that MQTT discovery is enabled in Home Assistant:
- In configuration.yaml, ensure mqtt: section doesn't have `discovery: false`
- Default discovery prefix should be `homeassistant`

### 3. MQTT Topics

**Command Topic:** `trafficlight/command`
- Send `traffic` = Switch to automatic traffic light mode (cycling)
- Send `manual` = Switch to manual mode (hold specific light)
- Send `clock` = Switch to clock mode  
- Send `toggle` = Cycle through modes (traffic → manual → clock)
- Send `red` = Switch to manual mode and show red light
- Send `yellow` or `amber` = Switch to manual mode and show yellow light
- Send `green` = Switch to manual mode and show green light

**State Topic:** `trafficlight/state`
- Receives `traffic`, `manual`, `clock`, `tractor`, `racecar`, or `gif` based on current mode

**Light Topic:** `trafficlight/light`
- Receives `red`, `yellow`, or `green` when in manual mode

### 4. Test Connection

From Home Assistant Developer Tools > MQTT:
- **Publish** to `trafficlight/command` with payload `red` (should show red light only)
- **Publish** to `trafficlight/command` with payload `green` (should show green light only)
- **Publish** to `trafficlight/command` with payload `traffic` (should start automatic cycling)
- **Publish** to `trafficlight/command` with payload `next` and retain disabled (advances one mode)
- **Subscribe** to `trafficlight/state` to see current mode
- **Subscribe** to `trafficlight/light` to see current light color in manual mode

### 5. Troubleshooting

If MQTT doesn't connect:
- Check Home Assistant IP address is correct
- Verify MQTT broker is running (Settings > Devices & Services > MQTT)
- Check username/password if authentication is enabled
- Look at MicroPico vREPL output for error messages
- Set `MQTT_ENABLED = False` to disable and run without MQTT

### 6. Home Assistant Controls (Auto-Created)

After the device connects, these will automatically appear in Home Assistant:

**Sensors:**
- **Traffic Light Mode** - Shows current mode (traffic/manual/clock)
- **Traffic Light Color** - Shows current light color in manual mode

**Buttons:**
- **Traffic Light Red** - Instantly show red light
- **Traffic Light Yellow** - Instantly show yellow light
- **Traffic Light Green** - Instantly show green light
- **Traffic Light Auto** - Start automatic cycling mode
- **Traffic Light Clock** - Switch to clock display
- **Traffic Light Toggle** - Cycle through modes

All entities will be grouped under the "Traffic Light Display" device.


## Play a GIF on the panel

Upload `trafficlight_toggle.py`, `gifdecoder.py`, and the updated `mqtt_simple.py`
to the Pico. Keep the `main.py` startup wrapper and your `tl_secrets.py` on the board.

Publish a GIF to `trafficlight/gif/set` with retain **off**. The GIF must fit within
**64 � 32 pixels** and be at most **16 KiB** (16,384 bytes). Smaller images are
centred. Both raw GIF bytes and a single line of base64 text work. For example,
run this from the project directory using your broker hostname and credentials:

```sh
mosquitto_pub -h BROKER_HOST -u MQTT_USER -P MQTT_PASSWORD -t trafficlight/gif/set -f nancat_small.gif
```

To publish from Home Assistant, base64-encode the file and paste the resulting
text as the MQTT publish payload. In PowerShell, this command copies the sample
GIF's base64 text to the clipboard:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes((Resolve-Path 'nancat_small.gif').Path)) | Set-Clipboard
```

The GIF starts looping as soon as it arrives. Use the button or publish `next`
to `trafficlight/command` to return to Traffic. The most recent GIF stays in RAM
until you replace it, send `stop` to `trafficlight/gif/set`, or reboot. Select
GIF from the Home Assistant mode selector or send `gif` to `trafficlight/command`
to show it again. While a GIF is loaded, `next` includes GIF after Racecar.
