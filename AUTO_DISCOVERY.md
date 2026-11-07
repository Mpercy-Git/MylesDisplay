# Traffic Light - Auto-Discovery Update

## What's New?

Your traffic light now automatically registers with Home Assistant using **MQTT Discovery**!

### Before (Manual Setup):
- Required copying YAML configuration
- Manual entity creation
- Configuration reload needed

### Now (Automatic):
- Zero configuration needed
- Automatically creates all controls
- Instant recognition by Home Assistant

## How It Works

When the Pico connects to MQTT, it publishes discovery messages that Home Assistant automatically detects. Within seconds, you'll see:

**In Home Assistant:**
1. Go to: Settings > Devices & Services > MQTT
2. Find: "Traffic Light Display" device
3. See: All sensors and controls already configured!

**Device Info:**
- **Name:** Traffic Light Display
- **Model:** Interstate75
- **Manufacturer:** Pimoroni
- **Unique ID:** Based on your Pico's hardware ID

## Available Controls (Auto-Created)

### Sensors
- Traffic Light Mode (shows: traffic/manual/clock)
- Traffic Light Color (shows: red/yellow/green)

### Buttons
- Traffic Light Red (manual red)
- Traffic Light Yellow (manual yellow)
- Traffic Light Green (manual green)
- Traffic Light Auto (automatic cycling)
- Traffic Light Clock (show time)
- Traffic Light Toggle (cycle modes)

## Using in Automations

Since entities are automatically created, you can use them in automations:

```yaml
automation:
  - alias: "Red light when door opens"
    trigger:
      - platform: state
        entity_id: binary_sensor.front_door
        to: 'on'
    action:
      - service: button.press
        target:
          entity_id: button.traffic_light_display_red
```

## No Configuration File Needed!

The `home_assistant_config.yaml` file is now **optional** - only needed if you have MQTT discovery disabled in Home Assistant (which is rare).

## Requirements

- Home Assistant with MQTT integration enabled
- MQTT discovery enabled (default setting)
- Discovery prefix set to "homeassistant" (default)

That's it! Just upload and run the code. Home Assistant handles the rest! 🎉
