# MylesDisplay

MicroPython firmware for a Pimoroni Interstate 75 with a 64 x 32 LED matrix.
The display can show a traffic light, clock, scrolling message, tractor, racecar,
or a GIF received over MQTT. It supports Home Assistant MQTT discovery.

## Install

1. Install Pimoroni MicroPython for your Interstate 75 board.
2. Copy `tl_secrets.example.py` to `tl_secrets.py` and fill in your Wi-Fi and MQTT settings.
3. Upload `main.py`, `trafficlight_toggle.py`, `mqtt_simple.py`, `gifdecoder.py`,
   and your local `tl_secrets.py` to the Pico, then restart it.

The display button advances modes. To do the same over MQTT, publish `next`
to `trafficlight/command`. To play an animation, publish a GIF of up to 64 x 32
pixels and 16 KiB to `trafficlight/gif/set` as raw bytes or base64 text.

See [MQTT setup](MQTT_SETUP.md) for Home Assistant and GIF examples, and
[upload instructions](UPLOAD_INSTRUCTIONS.md) for troubleshooting.
