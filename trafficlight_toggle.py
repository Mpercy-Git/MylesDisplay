from interstate75 import Interstate75, DISPLAY_INTERSTATE75_64X32
import time
import machine
import network
import ntptime
import ubinascii
import gc
try:
    import ujson as json
    JSON_AVAILABLE = True
except ImportError:
    try:
        import json
        JSON_AVAILABLE = True
    except ImportError:
        json = None
        JSON_AVAILABLE = False

try:
    import uos as os
except ImportError:
    import os

APP_VERSION = "trafficlight v3.9 brightness-mode"

# Try to import MQTT library (optional)
try:
    from mqtt_simple import MQTTClient
    MQTT_AVAILABLE = True
except ImportError:
    print('MQTT library not found - MQTT features disabled')
    MQTT_AVAILABLE = False
    MQTTClient = None

# WiFi/MQTT credentials and hosts live in tl_secrets.py (upload it next to this file).
try:
    import tl_secrets as _secrets
except ImportError:
    print('tl_secrets.py not found - WiFi/MQTT will not connect')
    _secrets = None

def _secret(name, default=""):
    return getattr(_secrets, name, default)

WIFI_SSID = _secret("WIFI_SSID")
WIFI_PASSWORD = _secret("WIFI_PASSWORD")
WIFI_ENABLED = True  # Set to False to skip WiFi (faster startup, no clock/MQTT)
WIFI_CONNECT_ON_STARTUP = False  # Keep False to avoid blocking startup on flaky WiFi init
TIME_SYNC_ENABLED = True  # Set to True to enable NTP time sync (now with proper timeout!)
NETWORK_DIAGNOSTICS_ENABLED = False  # Set True only when troubleshooting network issues
WIFI_RECONNECT_INTERVAL_SECONDS = 30  # Retry runtime reconnect every N seconds while disconnected
SERVICE_RETRY_INTERVAL_SECONDS = 20  # Retry sync/MQTT checks in runtime loop
DEFER_NETWORK_SERVICES_TO_RUNTIME = True  # Prevent long startup stalls on flaky networks
# Standard (winter) offset in hours from UTC (e.g., 0 for London/GMT, 1 for CET).
# The RTC always holds UTC; local time = UTC + this offset (+1h while DST is active).
# Both can be changed over MQTT; the values are saved to TIMEZONE_FILE and survive reboots.
TIMEZONE_OFFSET = 0
# EU/UK daylight saving: +1h from last Sunday of March to last Sunday of October (01:00 UTC).
DST_ENABLED = True
TIMEZONE_FILE = 'timezone.cfg'
TIME_RESYNC_INTERVAL_SECONDS = 86400  # Re-sync the clock daily to correct drift
TIME_RESYNC_RETRY_SECONDS = 3600  # Wait this long after a failed re-sync
# Keep disabled while using Thonny/MicroPico: stopping the program stops feeding
# the hardware watchdog, which can reset USB during a file upload (EOF).
WATCHDOG_ENABLED = False  # Enable only for unattended use; requires a power cycle to clear
WATCHDOG_TIMEOUT_MS = 8000  # RP2040 maximum is 8388

wdt = None

def wdt_feed():
    if wdt:
        wdt.feed()

def pause(seconds):
    """time.sleep that keeps feeding the watchdog."""
    end = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
    while True:
        wdt_feed()
        remaining = time.ticks_diff(end, time.ticks_ms())
        if remaining <= 0:
            return
        time.sleep_ms(min(remaining, 100))

def load_timezone():
    global TIMEZONE_OFFSET, DST_ENABLED
    try:
        with open(TIMEZONE_FILE) as f:
            offset, dst = f.read().strip().split(',')
        TIMEZONE_OFFSET = int(offset)
        DST_ENABLED = dst == '1'
    except Exception:
        pass

def save_timezone():
    try:
        with open(TIMEZONE_FILE, 'w') as f:
            f.write('{},{}'.format(TIMEZONE_OFFSET, 1 if DST_ENABLED else 0))
    except Exception as e:
        print('Timezone save failed:', e)

load_timezone()

def _last_sunday(year, month):
    """Day of month of the last Sunday (March and October both have 31 days)."""
    wd = time.gmtime(time.mktime((year, month, 31, 0, 0, 0, 0, 0)))[6]  # Mon=0..Sun=6
    return 31 - (wd + 1) % 7

def local_offset_seconds(utc=None):
    if utc is None:
        utc = time.time()
    offset = TIMEZONE_OFFSET
    if DST_ENABLED:
        year = time.gmtime(utc)[0]
        start = time.mktime((year, 3, _last_sunday(year, 3), 1, 0, 0, 0, 0))
        end = time.mktime((year, 10, _last_sunday(year, 10), 1, 0, 0, 0, 0))
        if start <= utc < end:
            offset += 1
    return offset * 3600

def local_now():
    utc = time.time()
    return time.localtime(utc + local_offset_seconds(utc))

# Auto clock schedule (24-hour time). Set AUTO_CLOCK_ENABLED=True to use.
# Example: 21:30 to 06:30 will show the clock overnight.
AUTO_CLOCK_ENABLED = True
AUTO_CLOCK_START_HOUR = 18
AUTO_CLOCK_START_MINUTE = 30
AUTO_CLOCK_END_HOUR = 7
AUTO_CLOCK_END_MINUTE = 00
AUTO_CLOCK_START_BRIGHTNESS = 10  # Brightness (%) when auto clock window starts
AUTO_CLOCK_END_BRIGHTNESS = 100  # Brightness (%) when auto clock window ends
AUTO_CLOCK_LOCK_BRIGHTNESS = True  # Keep start brightness enforced while auto clock is active
AUTO_CLOCK_BRIGHTNESS_ENFORCE_SECONDS = 5  # Re-apply lock brightness every N seconds
AUTO_CLOCK_FORCE_BRIGHTNESS_IN_CLOCK_MODE = True  # Fail-safe: enforce dim level in clock mode too

# MQTT Configuration (UPDATE THESE!)
MQTT_BROKER = _secret("MQTT_BROKER")
MQTT_PORT = 1883
MQTT_USER = _secret("MQTT_USER")
MQTT_PASSWORD = _secret("MQTT_PASSWORD")
MQTT_CLIENT_ID = ubinascii.hexlify(machine.unique_id())
MQTT_DEVICE_ID = MQTT_CLIENT_ID.decode('utf-8')
MQTT_TOPIC_COMMAND = "trafficlight/command"  # Mode and manual light commands (including next/toggle)
MQTT_TOPIC_STATE = "trafficlight/state"  # Publish: current mode
MQTT_TOPIC_LIGHT = "trafficlight/light"  # Publish: current light color in manual mode
MQTT_TOPIC_BRIGHTNESS_COMMAND = "trafficlight/brightness/set"  # Subscribe: 10-100
MQTT_TOPIC_BRIGHTNESS_STATE = "trafficlight/brightness/state"  # Publish: current brightness 10-100
MQTT_TOPIC_CLOCK_COLOR_COMMAND = "trafficlight/clock_color/set"  # Subscribe: blue/white/red/green/yellow
MQTT_TOPIC_CLOCK_COLOR_STATE = "trafficlight/clock_color/state"  # Publish: current clock color
MQTT_TOPIC_CLOCK_COLOR_RGB_COMMAND = "trafficlight/clock_color/rgb/set"  # Subscribe: "r,g,b"
MQTT_TOPIC_CLOCK_COLOR_RGB_STATE = "trafficlight/clock_color/rgb/state"  # Publish: "r,g,b"
MQTT_TOPIC_CLOCK_COLOR_POWER_COMMAND = "trafficlight/clock_color/power/set"  # Subscribe: ON/OFF
MQTT_TOPIC_CLOCK_COLOR_POWER_STATE = "trafficlight/clock_color/power/state"  # Publish: ON/OFF
MQTT_TOPIC_AUTO_CLOCK_ENABLED_COMMAND = "trafficlight/auto_clock/enabled/set"  # Subscribe: ON/OFF
MQTT_TOPIC_AUTO_CLOCK_ENABLED_STATE = "trafficlight/auto_clock/enabled/state"  # Publish: ON/OFF
MQTT_TOPIC_AUTO_CLOCK_START_COMMAND = "trafficlight/auto_clock/start/set"  # Subscribe: HH:MM
MQTT_TOPIC_AUTO_CLOCK_START_STATE = "trafficlight/auto_clock/start/state"  # Publish: HH:MM
MQTT_TOPIC_AUTO_CLOCK_END_COMMAND = "trafficlight/auto_clock/end/set"  # Subscribe: HH:MM
MQTT_TOPIC_AUTO_CLOCK_END_STATE = "trafficlight/auto_clock/end/state"  # Publish: HH:MM
MQTT_TOPIC_GIF = "trafficlight/gif/set"  # Raw GIF bytes or base64
GIF_MAX_BYTES = 16 * 1024
GIF_MAX_PAYLOAD = ((GIF_MAX_BYTES + 2) // 3) * 4
MQTT_TOPIC_MESSAGE_COMMAND = "trafficlight/message/set"  # Subscribe: scrolling message text
MQTT_TOPIC_MESSAGE_STATE = "trafficlight/message/state"  # Publish: current/last message text
MQTT_TOPIC_TIMEZONE_COMMAND = "trafficlight/timezone/set"  # Subscribe: standard UTC offset in hours (-12..14)
MQTT_TOPIC_TIMEZONE_STATE = "trafficlight/timezone/state"  # Publish: standard UTC offset in hours
MQTT_TOPIC_DST_COMMAND = "trafficlight/dst/set"  # Subscribe: ON/OFF (automatic daylight saving)
MQTT_TOPIC_DST_STATE = "trafficlight/dst/state"  # Publish: ON/OFF
MQTT_DISCOVERY_PREFIX = "homeassistant"  # Home Assistant discovery prefix
MQTT_ENABLED = True  # Set to True to enable MQTT/Home Assistant (traffic light works without this)
STARTUP_CLEANUP_MARKER = '.ha_startup_cleanup_v2'

# Scrolling message settings
MESSAGE_DISPLAY_SECONDS = 30  # 0 keeps message mode active until cleared
MESSAGE_SCROLL_STEP_MS = 60
MESSAGE_START_X = 64
MESSAGE_TEXT_Y = 12

# Home Assistant HTTP time fallback (can be different from MQTT_BROKER)
HOME_ASSISTANT_HOST = _secret("HOME_ASSISTANT_HOST")
HOME_ASSISTANT_PORTS = (8123, 80)  # Try HA default and reverse-proxy port

# Initialize display
i75 = Interstate75(display=DISPLAY_INTERSTATE75_64X32, stb_invert=False, panel_type=Interstate75.PANEL_GENERIC)
graphics = i75.display
graphics.set_font("bitmap8")

# Brightness (percent)
brightness_percent = 100
brightness_supported = True  # Software brightness via color scaling
brightness_method = 'software'
BRIGHTNESS_MIN_PERCENT = 1  # Allow very dim levels for night mode

def apply_brightness_to_color(r, g, b, brightness_pct):
    """Scale RGB values by brightness percentage (software dimming)."""
    factor = brightness_pct / 100.0
    return (int(r * factor), int(g * factor), int(b * factor))

def set_display_brightness(percent):
    """Set display brightness via software color scaling."""
    global brightness_percent

    if not brightness_supported:
        return False
    
    try:
        value = int(percent)
    except Exception:
        print('Invalid brightness value:', percent)
        return False

    if value < BRIGHTNESS_MIN_PERCENT:
        value = BRIGHTNESS_MIN_PERCENT
    if value > 100:
        value = 100

    brightness_percent = value
    recreate_color_pens()
    # Also update clock color pen with new brightness
    if clock_color_rgb:
        r_dim, g_dim, b_dim = apply_brightness_to_color(clock_color_rgb[0], clock_color_rgb[1], clock_color_rgb[2], brightness_percent)
        global clock_color_pen
        clock_color_pen = graphics.create_pen(r_dim, g_dim, b_dim)
    print('Brightness set: {}% (software scaling)'.format(brightness_percent))
    return True

print('Brightness control: software color scaling')

# Base colors (full brightness)
BASE_COLORS = {
    'black': (0, 0, 0),
    'red': (255, 0, 0),
    'yellow': (255, 255, 50),
    'green': (0, 255, 0),
    'grey': (50, 50, 50),
    'white': (255, 255, 255),
    'blue': (0, 100, 255)
}

# Reference-derived palette, rebuilt with the other brightness-adjusted pens.
RACECAR_PALETTE = ((190, 169, 91), (174, 117, 93), (200, 94, 67), (100, 90, 77), (213, 61, 65), (161, 57, 56), (100, 58, 61), (41, 55, 58), (176, 24, 46), (55, 14, 22), (0, 29, 34), (0, 0, 0))
RACECAR_PENS = ()

# Color pens (brightness-adjusted)
BLACK = None
RED = None
YELLOW = None
GREEN = None
GREY = None
WHITE = None
BLUE = None

def recreate_color_pens():
    """Recreate all color pens with current brightness applied."""
    global BLACK, RED, YELLOW, GREEN, GREY, WHITE, BLUE, RACECAR_PENS
    RACECAR_PENS = tuple(graphics.create_pen(*apply_brightness_to_color(*rgb, brightness_percent))
                         for rgb in RACECAR_PALETTE)
    
    BLACK = graphics.create_pen(0, 0, 0)  # Keep black always black
    r, g, b = apply_brightness_to_color(*BASE_COLORS['red'], brightness_percent)
    RED = graphics.create_pen(r, g, b)
    r, g, b = apply_brightness_to_color(*BASE_COLORS['yellow'], brightness_percent)
    YELLOW = graphics.create_pen(r, g, b)
    r, g, b = apply_brightness_to_color(*BASE_COLORS['green'], brightness_percent)
    GREEN = graphics.create_pen(r, g, b)
    r, g, b = apply_brightness_to_color(*BASE_COLORS['grey'], brightness_percent)
    GREY = graphics.create_pen(r, g, b)
    r, g, b = apply_brightness_to_color(*BASE_COLORS['white'], brightness_percent)
    WHITE = graphics.create_pen(r, g, b)
    r, g, b = apply_brightness_to_color(*BASE_COLORS['blue'], brightness_percent)
    BLUE = graphics.create_pen(r, g, b)

# Initialize color pens at startup brightness
recreate_color_pens()

# Clock color control
CLOCK_COLORS = {
    'blue': BLUE,
    'white': WHITE,
    'red': RED,
    'green': GREEN,
    'yellow': YELLOW,
}
clock_color_name = 'blue'
clock_color_rgb = (0, 100, 255)
clock_color_pen = BLUE
clock_color_enabled = True

def set_clock_color_rgb(r, g, b):
    """Set clock color from RGB values 0-255."""
    global clock_color_rgb, clock_color_pen, clock_color_name, clock_color_enabled
    
    try:
        rr = max(0, min(255, int(r)))
        gg = max(0, min(255, int(g)))
        bb = max(0, min(255, int(b)))
    except Exception:
        print('Invalid RGB color:', r, g, b)
        return False

    clock_color_rgb = (rr, gg, bb)
    # Apply current brightness to clock color
    r_dim, g_dim, b_dim = apply_brightness_to_color(rr, gg, bb, brightness_percent)
    clock_color_pen = graphics.create_pen(r_dim, g_dim, b_dim)
    clock_color_enabled = True
    # Keep name-based state only for known presets.
    clock_color_name = 'custom'
    print(f'Clock RGB set: {clock_color_rgb}')
    return True

def set_clock_color(name):
    """Set clock text color from supported names."""
    global clock_color_name
    value = str(name).lower().strip()
    if value in CLOCK_COLORS:
        clock_color_name = value
        if value == 'blue':
            set_clock_color_rgb(0, 100, 255)
        elif value == 'white':
            set_clock_color_rgb(255, 255, 255)
        elif value == 'red':
            set_clock_color_rgb(255, 0, 0)
        elif value == 'green':
            set_clock_color_rgb(0, 255, 0)
        elif value == 'yellow':
            set_clock_color_rgb(255, 255, 50)
        clock_color_name = value
        print(f'Clock color set: {clock_color_name}')
        return True
    print('Invalid clock color:', name)
    return False

def parse_rgb_payload(payload_text):
    """Parse common HA RGB payload formats into an (r, g, b) tuple."""
    text = str(payload_text).strip()

    # Format: "r,g,b"
    try:
        parts = text.split(',')
        if len(parts) == 3 and '{' not in text and '}' not in text:
            return (int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip()))
    except Exception:
        pass

    # Format: "[r,g,b]"
    if text.startswith('[') and text.endswith(']'):
        inner = text[1:-1]
        try:
            parts = inner.split(',')
            if len(parts) == 3:
                return (int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip()))
        except Exception:
            pass

    # Formats: {"r":...,"g":...,"b":...} or {"color":{"r":...}}
    if JSON_AVAILABLE and (text.startswith('{') or text.startswith('[')):
        try:
            data = json.loads(text)
            if isinstance(data, list) and len(data) >= 3:
                return (int(data[0]), int(data[1]), int(data[2]))
            if isinstance(data, dict):
                if 'color' in data and isinstance(data['color'], dict):
                    color = data['color']
                    return (int(color.get('r', 0)), int(color.get('g', 0)), int(color.get('b', 0)))
                if 'r' in data and 'g' in data and 'b' in data:
                    return (int(data['r']), int(data['g']), int(data['b']))
        except Exception:
            pass

    return None

# Button constants
BUTTON_A = 0

# Circle positions (centered horizontally)
circle_y = 16
circle_x = [11, 32, 53]

positions = {
    "red": (11, circle_y),
    "yellow": (32, circle_y),
    "green": (53, circle_y)
}

# WiFi and Time Sync Functions
def show_status(message):
    """Display status message on screen"""
    graphics.set_pen(BLACK)
    graphics.clear()
    graphics.set_pen(WHITE)
    graphics.text(message, 2, 12, scale=1)
    i75.update(graphics)

def connect_wifi():
    """Connect to WiFi network"""
    if not WIFI_ENABLED:
        print('WiFi disabled')
        return False
    
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    # Confirmed stable baseline for this board/network.
    if wlan.isconnected():
        print('Already connected:', wlan.ifconfig()[0])
        return True

    show_status('WiFi...')
    print('Connecting to WiFi (baseline mode)...')
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    max_wait = 20
    while max_wait > 0:
        if wlan.isconnected():
            break
        max_wait -= 1
        pause(1)
    
    if wlan.isconnected():
        print('WiFi connected:', wlan.ifconfig()[0])
        try:
            print('WiFi status:', wlan.status(), 'RSSI:', wlan.status('rssi'))
        except Exception:
            print('WiFi status:', wlan.status())
        show_status('WiFi OK')
        pause(1)
        return True
    else:
        print('WiFi connection failed, status:', wlan.status())
        show_status('WiFi Fail')
        pause(2)
        return False

def wifi_connected():
    """Return True when STA interface is connected."""
    try:
        return network.WLAN(network.STA_IF).isconnected()
    except Exception:
        return False

def ensure_wifi_runtime():
    """Recover WiFi during runtime without rebooting the board."""
    global last_wifi_reconnect_attempt, mqtt_client

    if not WIFI_ENABLED:
        return False

    if wifi_connected():
        return True

    now = time.time()
    if last_wifi_reconnect_attempt and (now - last_wifi_reconnect_attempt) < WIFI_RECONNECT_INTERVAL_SECONDS:
        return False

    last_wifi_reconnect_attempt = now
    print('WiFi lost - attempting runtime reconnect...')

    if mqtt_client:
        try:
            mqtt_client.disconnect()
        except Exception:
            pass
        mqtt_client = None

    if connect_wifi():
        print('Runtime WiFi reconnect successful')
        if TIME_SYNC_ENABLED:
            sync_time()
        if MQTT_ENABLED and MQTT_AVAILABLE and not mqtt_client:
            connect_mqtt()
        return True

    print('Runtime WiFi reconnect failed')
    return False

def ensure_online_services_runtime():
    """Attempt time sync and MQTT connect during runtime without blocking startup."""
    global last_service_attempt, time_synced_once

    if not WIFI_ENABLED or not wifi_connected():
        return

    now = time.time()
    if last_service_attempt and (now - last_service_attempt) < SERVICE_RETRY_INTERVAL_SECONDS:
        return
    last_service_attempt = now

    if TIME_SYNC_ENABLED and not time_synced_once:
        print('Runtime service: attempting time sync...')
        if sync_time():
            time_synced_once = True
            apply_auto_clock_schedule()

    if TIME_SYNC_ENABLED and time_synced_once and resync_due_ticks is not None \
            and time.ticks_diff(time.ticks_ms(), resync_due_ticks) >= 0:
        print('Runtime service: periodic time re-sync...')
        if not sync_time():
            schedule_resync(TIME_RESYNC_RETRY_SECONDS)

    if MQTT_ENABLED and MQTT_AVAILABLE and not mqtt_client:
        print('Runtime service: attempting MQTT connect...')
        connect_mqtt()

def test_tcp_connect(host, port, timeout_s=3):
    """Simple TCP probe with timeout for connectivity diagnostics."""
    s = None
    try:
        import usocket as socket
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(timeout_s)
        s.connect(addr)
        return True, 'ok'
    except Exception as e:
        return False, str(e)
    finally:
        try:
            if s:
                s.close()
        except Exception:
            pass
        gc.collect()

def network_diagnostics():
    """Print basic network diagnostics to identify routing/connectivity issues."""
    try:
        wlan = network.WLAN(network.STA_IF)
        print('WiFi ifconfig:', wlan.ifconfig())
    except Exception as e:
        print('Could not read ifconfig:', e)

    checks = (
        (HOME_ASSISTANT_HOST, 8123, 'HA:8123'),
        (HOME_ASSISTANT_HOST, 80, 'HA:80'),
        (MQTT_BROKER, MQTT_PORT, 'MQTT broker'),
        ('1.1.1.1', 80, 'Cloudflare 1.1.1.1:80')
    )

    failed = 0
    for host, port, label in checks:
        gc.collect()
        ok, msg = test_tcp_connect(host, port)
        if ok:
            print(f'NET CHECK OK {label} ({host}:{port})')
        else:
            failed += 1
            print(f'NET CHECK FAIL {label} ({host}:{port}) -> {msg}')
        pause(0.05)

    # If every probe fails, force-reset WLAN and try once more.
    if failed == len(checks):
        print('All TCP checks failed; resetting WLAN interface and retrying checks...')
        try:
            wlan = network.WLAN(network.STA_IF)
            wlan.active(False)
            pause(0.5)
            wlan.active(True)
            pause(0.5)
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)

            max_wait = 10
            while max_wait > 0 and not wlan.isconnected():
                max_wait -= 1
                pause(1)

            if wlan.isconnected():
                print('WLAN reset reconnect OK:', wlan.ifconfig()[0])
            else:
                print('WLAN reset reconnect failed, status:', wlan.status())
        except Exception as e:
            print('WLAN reset error:', e)

        failed2 = 0
        for host, port, label in checks:
            gc.collect()
            ok, msg = test_tcp_connect(host, port)
            if ok:
                print(f'NET RECHECK OK {label} ({host}:{port})')
            else:
                failed2 += 1
                print(f'NET RECHECK FAIL {label} ({host}:{port}) -> {msg}')
            pause(0.05)

        if failed2 == len(checks):
            print('Network path still blocked after WLAN reset (likely AP/client isolation or firewall policy).')

def verify_dependencies():
    """Print dependency/runtime checks from the device itself."""
    print('Dependency check start')

    required_modules = ('interstate75', 'machine', 'network', 'ntptime', 'ubinascii')
    missing = []
    for mod in required_modules:
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)

    if missing:
        print('Missing required modules:', ','.join(missing))
    else:
        print('All required modules present')

    # Optional MQTT helper file check.
    try:
        files = os.listdir('/')
        if 'mqtt_simple.py' in files:
            print('Optional dependency present: mqtt_simple.py')
        else:
            print('Optional dependency missing: mqtt_simple.py (MQTT features disabled unless uploaded)')
    except Exception as e:
        print('Filesystem check failed:', e)

def http_get_with_timeout(host, port, path, timeout_ms=5000):
    """HTTP GET with blocking socket timeout (more reliable on Pico W)."""
    import usocket as socket
    wdt_feed()
    addr = socket.getaddrinfo(host, port)[0][-1]
    wdt_feed()
    s = socket.socket()
    s.settimeout(max(1, int(timeout_ms / 1000)))

    try:
        s.connect(addr)
        wdt_feed()

        req = (
            'GET ' + path + ' HTTP/1.0\r\n'
            'Host: ' + host + '\r\n'
            'Connection: close\r\n\r\n'
        )
        s.send(req.encode())

        response = b''
        while True:
            wdt_feed()
            try:
                chunk = s.recv(256)
            except OSError:
                break

            if not chunk:
                break
            response += chunk

        if not response:
            raise Exception('read timeout')

        return response.decode()
    finally:
        try:
            s.close()
        except Exception:
            pass
        gc.collect()

# Ports count seconds from 2000 or 1970; HTTP time sources report Unix (1970) seconds.
EPOCH_DELTA = 946684800 if time.gmtime(0)[0] == 2000 else 0

NTP_HOSTS = (
    "pool.ntp.org",
    "time.google.com",
    "time.cloudflare.com",
    "129.6.15.28",     # NIST
    "216.239.35.0"     # Google Public NTP anycast
)

MONTHS = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,
    'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8,
    'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
}

def set_rtc_utc(year, month, day, hour, minute, second):
    """Store a UTC time in the RTC; the local offset/DST is applied at display time."""
    weekday = time.gmtime(time.mktime((year, month, day, hour, minute, second, 0, 0)))[6]
    machine.RTC().datetime((year, month, day, weekday, hour, minute, second, 0))

def set_rtc_from_unix(unix_time):
    tm = time.gmtime(unix_time - EPOCH_DELTA)
    set_rtc_utc(tm[0], tm[1], tm[2], tm[3], tm[4], tm[5])

def parse_http_date(text):
    """Parse the Date header of an HTTP response into a UTC (y, m, d, h, mi, s) tuple."""
    header = text.split('\r\n\r\n', 1)[0]
    for line in header.split('\r\n'):
        if line.lower().startswith('date:'):
            # Example: Tue, 01 Apr 2026 14:23:45 GMT
            parts = line.split(':', 1)[1].split()
            hh, mm, ss = parts[4].split(':')
            return int(parts[3]), MONTHS[parts[2]], int(parts[1]), int(hh), int(mm), int(ss)
    raise Exception('Date header not found')

def sync_via_ntp():
    """Try each NTP host in turn, re-checking WiFi first."""
    for attempt, host in enumerate(NTP_HOSTS):
        try:
            gc.collect()
            show_status(f'NTP {attempt + 1}/{len(NTP_HOSTS)}')

            # If WiFi dropped after initial connect, recover before trying NTP.
            if not network.WLAN(network.STA_IF).isconnected():
                print('WiFi dropped before NTP sync, reconnecting...')
                if not connect_wifi():
                    raise Exception('WiFi reconnect failed')

            ntptime.host = host
            print(f'Syncing time via {host} (attempt {attempt + 1}/{len(NTP_HOSTS)})...')
            ntptime.settime()
            return True
        except Exception as e:
            print(f'NTP sync attempt {attempt + 1} failed:', e)
            pause(1)
    return False

def sync_via_home_assistant():
    """LAN fallback: Date header from the Home Assistant web server."""
    if not HOME_ASSISTANT_HOST:
        raise Exception('HOME_ASSISTANT_HOST not set')
    last_error = None
    for port in HOME_ASSISTANT_PORTS:
        try:
            print(f'Trying Home Assistant LAN time on {HOME_ASSISTANT_HOST}:{port}...')
            set_rtc_utc(*parse_http_date(http_get_with_timeout(HOME_ASSISTANT_HOST, port, '/', 4000)))
            return
        except Exception as e:
            print(f'Home Assistant time sync failed on {port}:', e)
            last_error = e
    raise last_error

def sync_via_cloudflare_ip():
    """DNS-free internet fallback: Cloudflare trace by IP, parse the epoch."""
    text = http_get_with_timeout('1.1.1.1', 80, '/cdn-cgi/trace', 5000)
    for line in text.split('\r\n\r\n', 1)[-1].split('\n'):
        if line.startswith('ts='):
            set_rtc_from_unix(int(float(line[3:])))
            return
    raise Exception('ts not found in Cloudflare trace response')

def sync_via_date_header():
    """Last resort: Date header from a regular web server."""
    set_rtc_utc(*parse_http_date(http_get_with_timeout('clients3.google.com', 80, '/generate_204', 5000)))

# (status text, label, function) tried in order when NTP fails.
TIME_FALLBACKS = (
    ('HA Time', 'Home Assistant LAN', sync_via_home_assistant),
    ('CF Time', 'Cloudflare IP', sync_via_cloudflare_ip),
    ('HTTP Time', 'HTTP Date header', sync_via_date_header),
)

resync_due_ticks = None

def schedule_resync(seconds):
    global resync_due_ticks
    resync_due_ticks = time.ticks_add(time.ticks_ms(), seconds * 1000)

def sync_time():
    """Sync the RTC to UTC via NTP, falling back to HTTP sources."""
    print(f'Sync strategy: {APP_VERSION}')
    synced = sync_via_ntp()

    if not synced:
        print('NTP failed on all hosts, trying fallbacks...')
        for status, label, fallback in TIME_FALLBACKS:
            show_status(status)
            gc.collect()
            try:
                fallback()
                print(f'Time synced via {label}')
                synced = True
                break
            except Exception as e:
                print(f'{label} time sync failed:', e)

    if synced:
        now = local_now()
        print(f'Time synced: {now[3]:02d}:{now[4]:02d}:{now[5]:02d}')
        show_status('Time OK')
        pause(1)
        schedule_resync(TIME_RESYNC_INTERVAL_SECONDS)
        return True

    print('Continuing without time sync...')
    show_status('No Sync')
    pause(0.5)
    return False

# MQTT Functions
mqtt_client = None
last_wifi_reconnect_attempt = 0
last_service_attempt = 0
time_synced_once = False
message_text = ''
message_active = False
message_scroll_x = MESSAGE_START_X
message_last_step_ms = 0
message_timeout_at = 0
message_restore_mode = 0

def mqtt_callback(topic, msg):
    """Handle incoming MQTT messages"""
    global current_mode, traffic_timer, traffic_index, manual_light_state, tractor_x
    global AUTO_CLOCK_ENABLED, AUTO_CLOCK_START_HOUR, AUTO_CLOCK_START_MINUTE
    global AUTO_CLOCK_END_HOUR, AUTO_CLOCK_END_MINUTE
    
    try:
        topic_str = topic.decode('utf-8')
        if topic_str == MQTT_TOPIC_GIF:
            receive_gif(msg)
            return
        payload = msg.decode('utf-8')
        command = payload.strip().lower()
        print(f'MQTT received [{topic_str}]: {payload}')

        if topic_str == MQTT_TOPIC_BRIGHTNESS_COMMAND:
            if not brightness_supported:
                print('Ignoring brightness command: brightness not supported')
                return

            if AUTO_CLOCK_ENABLED and AUTO_CLOCK_LOCK_BRIGHTNESS and is_auto_clock_window(local_now()):
                print('Ignoring brightness command: auto clock brightness lock active')
                if set_display_brightness(AUTO_CLOCK_START_BRIGHTNESS):
                    publish_brightness_state()
                return

            try:
                requested = int(command)
                if set_display_brightness(requested):
                    publish_brightness_state()
            except Exception as e:
                print('Brightness command error:', e)
            return

        if topic_str == MQTT_TOPIC_CLOCK_COLOR_COMMAND:
            try:
                if set_clock_color(command):
                    publish_clock_color_state()
                    publish_clock_color_light_state()
            except Exception as e:
                print('Clock color command error:', e)
            return

        if topic_str == MQTT_TOPIC_CLOCK_COLOR_RGB_COMMAND:
            try:
                rgb = parse_rgb_payload(payload)
                if rgb:
                    r, g, b = rgb
                    if set_clock_color_rgb(r, g, b):
                        publish_clock_color_state()
                        publish_clock_color_light_state()
                else:
                    print('Unsupported RGB payload format:', payload)
            except Exception as e:
                print('Clock color RGB command error:', e)
            return

        if topic_str == MQTT_TOPIC_CLOCK_COLOR_POWER_COMMAND:
            global clock_color_enabled
            if command == 'on':
                clock_color_enabled = True
            elif command == 'off':
                clock_color_enabled = False
            publish_clock_color_light_state()
            return

        if topic_str == MQTT_TOPIC_AUTO_CLOCK_ENABLED_COMMAND:
            global auto_clock_active
            if command in ('on', 'true', '1'):
                AUTO_CLOCK_ENABLED = True
            elif command in ('off', 'false', '0'):
                AUTO_CLOCK_ENABLED = False
            else:
                print('Invalid auto clock enable command:', command)
                return
            # Force schedule transition recalculation after enable/disable changes.
            auto_clock_active = False
            print('Auto clock enabled:', AUTO_CLOCK_ENABLED)
            apply_auto_clock_schedule()
            publish_auto_clock_state()
            return

        if topic_str == MQTT_TOPIC_AUTO_CLOCK_START_COMMAND:
            parsed = parse_hhmm(command)
            if not parsed:
                print('Invalid auto clock start time:', command)
                return
            AUTO_CLOCK_START_HOUR, AUTO_CLOCK_START_MINUTE = parsed
            print('Auto clock start set: {:02d}:{:02d}'.format(AUTO_CLOCK_START_HOUR, AUTO_CLOCK_START_MINUTE))
            apply_auto_clock_schedule()
            publish_auto_clock_state()
            return

        if topic_str == MQTT_TOPIC_AUTO_CLOCK_END_COMMAND:
            parsed = parse_hhmm(command)
            if not parsed:
                print('Invalid auto clock end time:', command)
                return
            AUTO_CLOCK_END_HOUR, AUTO_CLOCK_END_MINUTE = parsed
            print('Auto clock end set: {:02d}:{:02d}'.format(AUTO_CLOCK_END_HOUR, AUTO_CLOCK_END_MINUTE))
            apply_auto_clock_schedule()
            publish_auto_clock_state()
            return

        if topic_str == MQTT_TOPIC_TIMEZONE_COMMAND:
            global TIMEZONE_OFFSET
            try:
                offset = int(float(command))
            except ValueError:
                offset = None
            if offset is None or offset < -12 or offset > 14:
                print('Invalid timezone offset:', command)
                return
            TIMEZONE_OFFSET = offset
            save_timezone()
            print('Timezone offset set:', TIMEZONE_OFFSET)
            publish_timezone_state()
            return

        if topic_str == MQTT_TOPIC_DST_COMMAND:
            global DST_ENABLED
            if command in ('on', 'true', '1'):
                DST_ENABLED = True
            elif command in ('off', 'false', '0'):
                DST_ENABLED = False
            else:
                print('Invalid DST command:', command)
                return
            save_timezone()
            print('DST enabled:', DST_ENABLED)
            publish_timezone_state()
            return

        if topic_str == MQTT_TOPIC_MESSAGE_COMMAND:
            message_value = payload.strip()
            if message_value == '' or command in ('off', 'clear', 'none'):
                clear_scrolling_message('mqtt clear')
            else:
                activate_scrolling_message(message_value)
            publish_message_state()
            return
        
        if command == 'traffic':
            deactivate_scrolling_message()
            current_mode = MODE_TRAFFIC
            traffic_timer = time.time()
            traffic_index = 0
            publish_state()
        elif command == 'clock':
            deactivate_scrolling_message()
            current_mode = MODE_CLOCK
            publish_state()
        elif command == 'manual':
            deactivate_scrolling_message()
            current_mode = MODE_MANUAL
            publish_state()
        elif command == 'racecar':
            deactivate_scrolling_message()
            current_mode = MODE_RACECAR
            reset_racecar()
            publish_state()
        elif command == 'tractor':
            deactivate_scrolling_message()
            current_mode = MODE_TRACTOR
            tractor_x = -TRACTOR_WIDTH
            publish_state()
        elif command == 'gif':
            if gif_animation is not None:
                enter_gif_mode()
            else:
                print('Upload a GIF to', MQTT_TOPIC_GIF, 'first')
        elif command in ('next', 'toggle'):
            advance_mode()
        elif command in ['red', 'yellow', 'amber', 'green']:
            deactivate_scrolling_message()
            # Switch to manual mode and set specific light
            current_mode = MODE_MANUAL
            if command == 'amber':
                manual_light_state = 'yellow'
            else:
                manual_light_state = command
            publish_state()
    except Exception as e:
        print('MQTT callback error:', e)

def connect_mqtt():
    """Connect to MQTT broker"""
    global mqtt_client
    
    if not MQTT_ENABLED or not MQTT_AVAILABLE:
        if not MQTT_AVAILABLE:
            print('MQTT library not available')
        else:
            print('MQTT disabled')
        return False
    
    try:
        show_status('MQTT...')
        print('Connecting to MQTT broker...')
        
        mqtt_client = MQTTClient(
            MQTT_CLIENT_ID,
            MQTT_BROKER,
            port=MQTT_PORT,
            user=MQTT_USER if MQTT_USER else None,
            password=MQTT_PASSWORD if MQTT_PASSWORD else None
        )
        
        mqtt_client.max_incoming_packet = GIF_MAX_PAYLOAD + 256
        mqtt_client.set_callback(mqtt_callback)
        mqtt_client.connect()
        wdt_feed()
        mqtt_client.subscribe(MQTT_TOPIC_COMMAND)
        if brightness_supported:
            # Clear any stale retained message on brightness command topic
            mqtt_client.publish(MQTT_TOPIC_BRIGHTNESS_COMMAND, '', retain=True)
            mqtt_client.subscribe(MQTT_TOPIC_BRIGHTNESS_COMMAND)
            # If auto-clock is already active at connect time, immediately force the
            # dim level to override any stale retained message that was just delivered.
            if auto_clock_active:
                set_display_brightness(AUTO_CLOCK_START_BRIGHTNESS)
                publish_brightness_state()
                print('Auto clock: forced brightness to', AUTO_CLOCK_START_BRIGHTNESS)
        mqtt_client.subscribe(MQTT_TOPIC_CLOCK_COLOR_COMMAND)
        mqtt_client.subscribe(MQTT_TOPIC_CLOCK_COLOR_RGB_COMMAND)
        mqtt_client.subscribe(MQTT_TOPIC_CLOCK_COLOR_POWER_COMMAND)
        mqtt_client.subscribe(MQTT_TOPIC_AUTO_CLOCK_ENABLED_COMMAND)
        mqtt_client.subscribe(MQTT_TOPIC_AUTO_CLOCK_START_COMMAND)
        mqtt_client.subscribe(MQTT_TOPIC_AUTO_CLOCK_END_COMMAND)
        mqtt_client.subscribe(MQTT_TOPIC_MESSAGE_COMMAND)
        mqtt_client.subscribe(MQTT_TOPIC_GIF)
        mqtt_client.subscribe(MQTT_TOPIC_TIMEZONE_COMMAND)
        mqtt_client.subscribe(MQTT_TOPIC_DST_COMMAND)

        # Clear stale retained discovery topics once, then publish fresh config.
        run_startup_cleanup_once()
        wdt_feed()

        # Publish Home Assistant auto-discovery configuration
        publish_discovery()
        wdt_feed()
        if brightness_supported:
            publish_brightness_state()
        publish_clock_color_state()
        publish_clock_color_light_state()
        publish_auto_clock_state()
        publish_message_state()
        publish_timezone_state()

        print('MQTT connected')
        show_status('MQTT OK')
        pause(1)
        return True
    except Exception as e:
        print('MQTT connection failed:', e)
        show_status('MQTT Fail')
        pause(1)  # Reduced delay
        return False

def run_startup_cleanup_once():
    """Clear stale retained HA discovery topics one time after firmware updates."""
    global mqtt_client

    if not mqtt_client or not MQTT_ENABLED:
        return

    try:
        os.stat(STARTUP_CLEANUP_MARKER)
        return
    except OSError:
        pass

    try:
        print('Running one-time HA cleanup...')

        stale_topics = [
            '%s/light/%s/clock_color_wheel/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID),
            '%s/light/%s/clock_color/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID),
            '%s/button/%s/auto/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID),
            '%s/button/%s/clock/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID),
            '%s/button/%s/toggle/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID),
        ]

        for topic in stale_topics:
            # Retained empty payload tells HA to remove stale discovery entity.
            mqtt_client.publish(topic, '', retain=True)
            pause(0.05)

        with open(STARTUP_CLEANUP_MARKER, 'w') as marker:
            marker.write('done')

        print('One-time HA cleanup complete')
    except Exception as e:
        print('Startup cleanup skipped:', e)

def publish_discovery():
    """Publish Home Assistant MQTT discovery messages"""
    global mqtt_client
    
    if not mqtt_client or not MQTT_ENABLED:
        return
    
    try:
        print('Publishing HA discovery...')
        
        # Device information (shared across all entities)
        device_config = '"device":{"identifiers":["%s"],"name":"Traffic Light Display","model":"Interstate75","manufacturer":"Pimoroni"}' % MQTT_DEVICE_ID
        
        # Sensor: Mode
        mode_config = '{"name":"Traffic Light Mode","unique_id":"%s_mode","state_topic":"%s","icon":"mdi:traffic-light",%s}' % (
            MQTT_DEVICE_ID, MQTT_TOPIC_STATE, device_config)
        mqtt_client.publish('%s/sensor/%s/mode/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), mode_config, retain=True)
        
        # Sensor: Light Color
        light_config = '{"name":"Traffic Light Color","unique_id":"%s_color","state_topic":"%s","icon":"mdi:lightbulb",%s}' % (
            MQTT_DEVICE_ID, MQTT_TOPIC_LIGHT, device_config)
        mqtt_client.publish('%s/sensor/%s/color/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), light_config, retain=True)
        
        # Button: Red Light
        red_config = '{"name":"Traffic Light Red","unique_id":"%s_red","command_topic":"%s","payload_press":"red","icon":"mdi:circle","entity_category":"config",%s}' % (
            MQTT_DEVICE_ID, MQTT_TOPIC_COMMAND, device_config)
        mqtt_client.publish('%s/button/%s/red/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), red_config, retain=True)
        
        # Button: Yellow Light
        yellow_config = '{"name":"Traffic Light Yellow","unique_id":"%s_yellow","command_topic":"%s","payload_press":"yellow","icon":"mdi:circle","entity_category":"config",%s}' % (
            MQTT_DEVICE_ID, MQTT_TOPIC_COMMAND, device_config)
        mqtt_client.publish('%s/button/%s/yellow/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), yellow_config, retain=True)
        
        # Button: Green Light
        green_config = '{"name":"Traffic Light Green","unique_id":"%s_green","command_topic":"%s","payload_press":"green","icon":"mdi:circle","entity_category":"config",%s}' % (
            MQTT_DEVICE_ID, MQTT_TOPIC_COMMAND, device_config)
        mqtt_client.publish('%s/button/%s/green/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), green_config, retain=True)
        
        # Select: Mode (dropdown)
        mode_select_config = '{"name":"Traffic Light Mode Select","unique_id":"%s_mode_select","command_topic":"%s","state_topic":"%s","options":["traffic","manual","clock","tractor","racecar","gif"],"icon":"mdi:form-dropdown",%s}' % (
            MQTT_DEVICE_ID,
            MQTT_TOPIC_COMMAND,
            MQTT_TOPIC_STATE,
            device_config
        )
        mqtt_client.publish('%s/select/%s/mode_select/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), mode_select_config, retain=True)

        if brightness_supported:
            # Number: Brightness
            brightness_config = '{"name":"Traffic Light Brightness","unique_id":"%s_brightness","command_topic":"%s","state_topic":"%s","min":1,"max":100,"step":1,"mode":"slider","unit_of_measurement":"%%","icon":"mdi:brightness-6",%s}' % (
                MQTT_DEVICE_ID, MQTT_TOPIC_BRIGHTNESS_COMMAND, MQTT_TOPIC_BRIGHTNESS_STATE, device_config)
            mqtt_client.publish('%s/number/%s/brightness/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), brightness_config, retain=True)

        # Select: Clock Color
        clock_color_config = '{"name":"Traffic Light Clock Color","unique_id":"%s_clock_color","command_topic":"%s","state_topic":"%s","options":["blue","white","red","green","yellow"],"icon":"mdi:palette",%s}' % (
            MQTT_DEVICE_ID, MQTT_TOPIC_CLOCK_COLOR_COMMAND, MQTT_TOPIC_CLOCK_COLOR_STATE, device_config)
        mqtt_client.publish('%s/select/%s/clock_color/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), clock_color_config, retain=True)

        # Light: Clock Color Wheel (Home Assistant color picker)
        clock_color_light_config = '{"name":"Traffic Light Clock Color Wheel","unique_id":"%s_clock_color_wheel","command_topic":"%s","state_topic":"%s","payload_on":"ON","payload_off":"OFF","state_on":"ON","state_off":"OFF","rgb_command_topic":"%s","rgb_state_topic":"%s","optimistic":false,"icon":"mdi:palette",%s}' % (
            MQTT_DEVICE_ID,
            MQTT_TOPIC_CLOCK_COLOR_POWER_COMMAND,
            MQTT_TOPIC_CLOCK_COLOR_POWER_STATE,
            MQTT_TOPIC_CLOCK_COLOR_RGB_COMMAND,
            MQTT_TOPIC_CLOCK_COLOR_RGB_STATE,
            device_config
        )
        mqtt_client.publish('%s/light/%s/clock_color_wheel/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), clock_color_light_config, retain=True)

        # Switch: Auto Clock Enable
        auto_clock_switch_config = '{"name":"Traffic Light Auto Clock","unique_id":"%s_auto_clock_enabled","command_topic":"%s","state_topic":"%s","payload_on":"ON","payload_off":"OFF","state_on":"ON","state_off":"OFF","icon":"mdi:clock-time-four-outline",%s}' % (
            MQTT_DEVICE_ID,
            MQTT_TOPIC_AUTO_CLOCK_ENABLED_COMMAND,
            MQTT_TOPIC_AUTO_CLOCK_ENABLED_STATE,
            device_config
        )
        mqtt_client.publish('%s/switch/%s/auto_clock_enabled/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), auto_clock_switch_config, retain=True)

        # Text: Auto Clock Start time (HH:MM)
        auto_clock_start_config = '{"name":"Traffic Light Auto Clock Start","unique_id":"%s_auto_clock_start","command_topic":"%s","state_topic":"%s","icon":"mdi:clock-start",%s}' % (
            MQTT_DEVICE_ID,
            MQTT_TOPIC_AUTO_CLOCK_START_COMMAND,
            MQTT_TOPIC_AUTO_CLOCK_START_STATE,
            device_config
        )
        mqtt_client.publish('%s/text/%s/auto_clock_start/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), auto_clock_start_config, retain=True)

        # Text: Auto Clock End time (HH:MM)
        auto_clock_end_config = '{"name":"Traffic Light Auto Clock End","unique_id":"%s_auto_clock_end","command_topic":"%s","state_topic":"%s","icon":"mdi:clock-end",%s}' % (
            MQTT_DEVICE_ID,
            MQTT_TOPIC_AUTO_CLOCK_END_COMMAND,
            MQTT_TOPIC_AUTO_CLOCK_END_STATE,
            device_config
        )
        mqtt_client.publish('%s/text/%s/auto_clock_end/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), auto_clock_end_config, retain=True)

        # Text: Scrolling Message
        message_config = '{"name":"Traffic Light Message","unique_id":"%s_message","command_topic":"%s","state_topic":"%s","icon":"mdi:message-text",%s}' % (
            MQTT_DEVICE_ID,
            MQTT_TOPIC_MESSAGE_COMMAND,
            MQTT_TOPIC_MESSAGE_STATE,
            device_config
        )
        mqtt_client.publish('%s/text/%s/message/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), message_config, retain=True)

        # Number: Timezone offset (standard time, hours from UTC)
        timezone_config = '{"name":"Traffic Light Timezone Offset","unique_id":"%s_timezone","command_topic":"%s","state_topic":"%s","min":-12,"max":14,"step":1,"mode":"box","unit_of_measurement":"h","entity_category":"config","icon":"mdi:map-clock",%s}' % (
            MQTT_DEVICE_ID, MQTT_TOPIC_TIMEZONE_COMMAND, MQTT_TOPIC_TIMEZONE_STATE, device_config)
        mqtt_client.publish('%s/number/%s/timezone/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), timezone_config, retain=True)

        # Switch: Automatic daylight saving
        dst_config = '{"name":"Traffic Light Daylight Saving","unique_id":"%s_dst","command_topic":"%s","state_topic":"%s","payload_on":"ON","payload_off":"OFF","state_on":"ON","state_off":"OFF","entity_category":"config","icon":"mdi:weather-sunny-alert",%s}' % (
            MQTT_DEVICE_ID, MQTT_TOPIC_DST_COMMAND, MQTT_TOPIC_DST_STATE, device_config)
        mqtt_client.publish('%s/switch/%s/dst/config' % (MQTT_DISCOVERY_PREFIX, MQTT_DEVICE_ID), dst_config, retain=True)

        print('HA discovery published')
        
    except Exception as e:
        print('Discovery publish error:', e)

def publish_state():
    """Publish current mode to MQTT"""
    global mqtt_client
    
    if mqtt_client and MQTT_ENABLED:
        try:
            if current_mode == MODE_TRAFFIC:
                state = 'traffic'
            elif current_mode == MODE_CLOCK:
                state = 'clock'
            elif current_mode == MODE_TRACTOR:
                state = 'tractor'
            elif current_mode == MODE_RACECAR:
                state = 'racecar'
            elif current_mode == MODE_GIF:
                state = 'gif'
            else:
                state = 'manual'
            
            mqtt_client.publish(MQTT_TOPIC_STATE, state)
            print(f'Published state: {state}')
            
            # Also publish light color if in manual mode
            if current_mode == MODE_MANUAL:
                mqtt_client.publish(MQTT_TOPIC_LIGHT, manual_light_state)
                print(f'Published light: {manual_light_state}')
        except Exception as e:
            print('MQTT publish error:', e)

def publish_brightness_state():
    """Publish current brightness percentage to MQTT."""
    global mqtt_client

    if mqtt_client and MQTT_ENABLED:
        try:
            mqtt_client.publish(MQTT_TOPIC_BRIGHTNESS_STATE, str(brightness_percent), retain=True)
            print(f'Published brightness: {brightness_percent}%')
        except Exception as e:
            print('MQTT brightness publish error:', e)

def publish_clock_color_state():
    """Publish current clock color to MQTT."""
    global mqtt_client

    if mqtt_client and MQTT_ENABLED:
        try:
            mqtt_client.publish(MQTT_TOPIC_CLOCK_COLOR_STATE, clock_color_name)
            print(f'Published clock color: {clock_color_name}')
        except Exception as e:
            print('MQTT clock color publish error:', e)

def publish_clock_color_light_state():
    """Publish current clock color/power in MQTT light format for HA color wheel."""
    global mqtt_client

    if mqtt_client and MQTT_ENABLED:
        try:
            # Publish base RGB values (full brightness) - brightness slider controls dimming
            mqtt_client.publish(MQTT_TOPIC_CLOCK_COLOR_RGB_STATE, '{},{},{}'.format(clock_color_rgb[0], clock_color_rgb[1], clock_color_rgb[2]))
            mqtt_client.publish(MQTT_TOPIC_CLOCK_COLOR_POWER_STATE, 'ON' if clock_color_enabled else 'OFF')
            print('Published clock color wheel state')
        except Exception as e:
            print('MQTT clock color light publish error:', e)

def format_hhmm(hour, minute):
    """Return HH:MM string."""
    return '{:02d}:{:02d}'.format(hour, minute)

def parse_hhmm(value):
    """Parse HH:MM and return (hour, minute) tuple or None."""
    try:
        parts = value.strip().split(':')
        if len(parts) != 2:
            return None
        hour = int(parts[0])
        minute = int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return (hour, minute)
    except Exception:
        return None

def publish_auto_clock_state():
    """Publish current auto-clock enable/start/end settings."""
    global mqtt_client

    if mqtt_client and MQTT_ENABLED:
        try:
            mqtt_client.publish(MQTT_TOPIC_AUTO_CLOCK_ENABLED_STATE, 'ON' if AUTO_CLOCK_ENABLED else 'OFF')
            mqtt_client.publish(MQTT_TOPIC_AUTO_CLOCK_START_STATE, format_hhmm(AUTO_CLOCK_START_HOUR, AUTO_CLOCK_START_MINUTE))
            mqtt_client.publish(MQTT_TOPIC_AUTO_CLOCK_END_STATE, format_hhmm(AUTO_CLOCK_END_HOUR, AUTO_CLOCK_END_MINUTE))
            print('Published auto clock settings')
        except Exception as e:
            print('MQTT auto clock publish error:', e)

def publish_message_state():
    """Publish current scrolling message text to MQTT."""
    global mqtt_client

    if mqtt_client and MQTT_ENABLED:
        try:
            mqtt_client.publish(MQTT_TOPIC_MESSAGE_STATE, message_text)
            print('Published message state')
        except Exception as e:
            print('MQTT message publish error:', e)

def publish_timezone_state():
    """Publish the standard UTC offset and DST setting to MQTT."""
    global mqtt_client

    if mqtt_client and MQTT_ENABLED:
        try:
            mqtt_client.publish(MQTT_TOPIC_TIMEZONE_STATE, str(TIMEZONE_OFFSET), retain=True)
            mqtt_client.publish(MQTT_TOPIC_DST_STATE, 'ON' if DST_ENABLED else 'OFF', retain=True)
            print('Published timezone settings')
        except Exception as e:
            print('MQTT timezone publish error:', e)

def check_mqtt():
    """Check for MQTT messages (non-blocking)"""
    global mqtt_client
    
    if mqtt_client and MQTT_ENABLED:
        try:
            if not wifi_connected():
                print('MQTT paused: WiFi disconnected')
                try:
                    mqtt_client.disconnect()
                except Exception:
                    pass
                mqtt_client = None
                return
            mqtt_client.check_msg()  # Non-blocking check
        except Exception as e:
            print('MQTT check error:', e)
            # Try to reconnect
            try:
                mqtt_client.disconnect()
            except:
                pass
            mqtt_client = None

# Initialize time
print(f'Starting initialization... ({APP_VERSION})')
verify_dependencies()
show_status('Starting')
pause(0.5)

try:
    if WIFI_ENABLED:
        if not WIFI_CONNECT_ON_STARTUP:
            print('Deferring WiFi connect to runtime loop')
            show_status('Ready')
            pause(0.5)
        elif connect_wifi():
            if NETWORK_DIAGNOSTICS_ENABLED:
                network_diagnostics()
            if DEFER_NETWORK_SERVICES_TO_RUNTIME:
                print('Deferring time sync/MQTT to runtime loop')
                show_status('Ready')
                pause(0.5)
            else:
                if TIME_SYNC_ENABLED:
                    time_synced_once = sync_time()
                else:
                    print('Time sync disabled')
                if MQTT_ENABLED and MQTT_AVAILABLE:
                    connect_mqtt()
        else:
            print('WiFi startup connect failed; continuing runtime retries')
            show_status('Ready')
            pause(0.5)
    else:
        print('WiFi disabled - running standalone')
        show_status('Standalone')
        pause(1)
except Exception as e:
    print('Init error:', e)
    show_status('Init Fail')
    pause(1)

print('Starting traffic light...')

# Print auto-clock configuration
print('Auto-clock config: Enabled={}, Window={:02d}:{:02d}-{:02d}:{:02d}, Brightness={}%->{}%, Lock={}'.format(
    AUTO_CLOCK_ENABLED,
    AUTO_CLOCK_START_HOUR, AUTO_CLOCK_START_MINUTE,
    AUTO_CLOCK_END_HOUR, AUTO_CLOCK_END_MINUTE,
    AUTO_CLOCK_START_BRIGHTNESS, AUTO_CLOCK_END_BRIGHTNESS,
    AUTO_CLOCK_LOCK_BRIGHTNESS
))

# Mode tracking
MODE_TRAFFIC = 0
MODE_MANUAL = 1
MODE_CLOCK = 2
MODE_MESSAGE = 3
MODE_TRACTOR = 4
MODE_RACECAR = 5
MODE_GIF = 6
current_mode = MODE_TRAFFIC
auto_clock_active = False
mode_before_auto_clock = MODE_TRAFFIC
last_auto_clock_brightness_enforce = 0

# Manual light control
manual_light_state = "red"  # Default light when entering manual mode

# Traffic light state
traffic_state = "red"
traffic_timer = 0
traffic_durations = {
    "red": 10,
    "yellow_after_red": 2,
    "green": 10,
    "yellow_after_green": 2
}
traffic_sequence = ["red", "yellow_after_red", "green", "yellow_after_green"]
traffic_index = 0

# Button debounce
last_button_time = 0
DEBOUNCE_MS = 300

def draw_traffic_light(state):
    graphics.set_pen(BLACK)
    graphics.clear()

    # Determine which light to show
    if "yellow" in state:
        active = "yellow"
    else:
        active = state

    for light, (x, y) in positions.items():
        if active == light:
            graphics.set_pen(eval(light.upper()))
        else:
            graphics.set_pen(GREY)
        graphics.circle(x, y, 10)

    i75.update(graphics)

def draw_clock():
    graphics.set_pen(BLACK)
    graphics.clear()
    
    # Get current time with timezone offset applied.
    current_time = local_now()
    hour = current_time[3]
    minute = current_time[4]
    second = current_time[5]
    
    # Convert to 12-hour format
    am_pm = "AM" if hour < 12 else "PM"
    hour_12 = hour % 12
    if hour_12 == 0:
        hour_12 = 12
    
    # Format time string
    time_str = "{:2d}:{:02d}".format(hour_12, minute)
    
    # Draw time
    graphics.set_pen(clock_color_pen)
    graphics.text(time_str, 8, 8, scale=2)
    graphics.text(am_pm, 18, 24, scale=1)

    i75.update(graphics)

# Red tractor driving across the display
TRACTOR_WIDTH = 50
TRACTOR_STEP_MS = 50  # Lower = faster
tractor_x = -TRACTOR_WIDTH
tractor_last_step_ms = 0
tractor_frame = 0

def draw_tractor():
    """Draw a red tractor that drives left to right and loops."""
    global tractor_x, tractor_last_step_ms, tractor_frame

    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, tractor_last_step_ms) < TRACTOR_STEP_MS:
        return
    tractor_last_step_ms = now_ms
    tractor_x += 1
    tractor_frame += 1
    if tractor_x > 64:
        tractor_x = -TRACTOR_WIDTH

    x = tractor_x
    bounce = (tractor_frame // 3) % 2  # Small engine wobble

    graphics.set_pen(BLACK)
    graphics.clear()

    # Ground
    graphics.set_pen(GREEN)
    graphics.rectangle(0, 29, 64, 3)

    # Exhaust pipe, hood, cab roof, cab
    graphics.set_pen(GREY)
    graphics.rectangle(x + 22, 11 + bounce, 2, 6)
    graphics.set_pen(RED)
    graphics.rectangle(x + 13, 16 + bounce, 13, 7)
    graphics.rectangle(x + 2, 8 + bounce, 12, 2)
    graphics.rectangle(x + 3, 9 + bounce, 10, 13)
    # Dark cab window behind Santa, facing the front of the tractor.
    graphics.set_pen(GREY)
    graphics.rectangle(x + 4, 10 + bounce, 9, 9)
    santa = (
        "..RRR....",
        ".WRRRR...",
        "..WWWWW..",
        "..WYYBY..",
        "..WYYYYY.",
        "..WWWY...",
        "...WWWW..",
        "..RRRWW.Y",
        "..RRRRRY.",
    )
    pens = {'R': RED, 'W': WHITE, 'Y': YELLOW, 'B': BLACK}
    for row_y, row in enumerate(santa):
        for column, colour in enumerate(row):
            if colour != '.':
                graphics.set_pen(pens[colour])
                graphics.pixel(x + 4 + column, 10 + bounce + row_y)

    # Christmas bulbs along the cab roof and hood. A different pair twinkles off
    # every 250 ms, and the lights move with the tractor's engine bounce.
    bulbs = ((3, 7), (6, 7), (9, 7), (12, 7), (14, 16), (17, 16), (20, 16), (25, 16))
    colours = (YELLOW, GREEN, BLUE, WHITE)
    twinkle = (tractor_frame // 5) % len(colours)
    for index, (bulb_x, bulb_y) in enumerate(bulbs):
        colour_index = index % len(colours)
        if (colour_index + index // len(colours)) % len(colours) != twinkle:
            graphics.set_pen(colours[colour_index])
            graphics.pixel(x + bulb_x, bulb_y + bounce)

    # Big rear wheel and small front wheel with hubs
    graphics.set_pen(GREY)
    graphics.circle(x + 8, 23, 6)
    graphics.circle(x + 23, 26, 3)
    graphics.set_pen(WHITE)
    graphics.circle(x + 8, 23, 2)
    graphics.circle(x + 23, 26, 1)

    i75.update(graphics)

# Red race car: drives in to the middle, revs, then speeds off
RACECAR_WIDTH = 56
RACECAR_STEP_MS = 30  # Frame time
RACECAR_PAUSE_MS = 1500  # Time spent revving in the middle
RACECAR_GAP_MS = 800  # Empty road before the next run
RACECAR_CENTER_X = (64 - RACECAR_WIDTH) // 2
RACE_IN, RACE_REV, RACE_GO, RACE_GAP = 0, 1, 2, 3
racecar_x = -RACECAR_WIDTH
racecar_phase = RACE_IN
racecar_speed = 1
racecar_phase_started_ms = 0
racecar_last_step_ms = 0
racecar_frame = 0

def reset_racecar():
    global racecar_x, racecar_phase, racecar_speed, racecar_phase_started_ms
    racecar_x = -RACECAR_WIDTH
    racecar_phase = RACE_IN
    racecar_speed = 1
    racecar_phase_started_ms = time.ticks_ms()

RACECAR_TOP_Y = 9  # Reference image sampled to 56x19 pixels
# Sampled directly from the supplied McQueen side-view reference.
RACECAR_SPRITE = (
    "LLLLJIIIIJLLLLLLLLLLLLJJGGGDDGGJLLLLLLLLLLLLLLLLLLLLLLLL",
    "LLLLFECCEEIJLLLLLLLHFCBCCCCACCCBBJLLLLLLLLLLLLLLLLLLLLLL",
    "FECCCCAAEEEJLLLKHDBCEEIIFFFFFEEEBBLLLLLLLLLLLLLLLLLLLLLL",
    "GECCCCCEEEGFBBBBBEIFGGIFHHDDDDGIFBBLLLLLLLLLLLLLLLLLLLLL",
    "LIIIFFFIFFFBBBEEGHHHHHGIHDDDDDDDIBADLLLLLLLLLLLLLLLLLLLL",
    "LLJGGFIFCCCCCCEEFFFGGHHEHDDDDDDDGEAAABBBBBBBBCFGJLLLLLLL",
    "LLJEEEFHFEECCCCEIIIIEIFEFGDDDDDDDIEAABBAAAAAAAABBDJLLLLL",
    "LLJEEEFDEEECCCCCCIFFIIIIIIIIIIIIFIIEEEEEEEEEEECBBABFLLLL",
    "LLLEEEEEEEEEFFFCCCCCCCCFFCACFCAAIEEIEEEEEIEEIIIIEECBBGLL",
    "LLLIEECCEEFJKKKKJFCCCCCCAAAAAAABEEEECAAAAEEGJHHHJIEFBABL",
    "LLJIIIIFEIKHKJJHHKFCCCCCCCFCCCDGEEEEEAABBIKKKHHHHJFFFBAD",
    "LLJIIIIIILDHJJJJJDLFFCCCGFEFDFECFCIIEABBFJKJJLLJHHJFFFBB",
    "LLLJGIIIJHDJJKHHJHHJFCCCGGIFDFIAAAAFIFBFJKJJKHHKJDHFFFBG",
    "LLLLLJGIJHHJKGFHKJHLFFCGIIGGIIGAAAAAFIIIJDJLHFGHJGHJFFFG",
    "LLLLLLLJJHHJKHGHKGDLGCCFGGFGGDBAABCAABIIJDJLHGGKGGHJIGFG",
    "LLLLLLLLLHDJJKKKJGDLGCCCCAAAEEECCFIIFBDILDGJHKKHIGHJIGFJ",
    "LLLLLLLLLLDGIGGFFDHLJHHJJJGHGGFIGGIIIIIILHDGGGGIGHLJGGJL",
    "LLLLLLLLLLKDDGGGHHLKHHHKHGDDDBBBDDFGDDDGLLHDGGGGHLLJJLLL",
    "LLLLLLLLLLLLHHHHLLLLKLLHKLJJJJJJJJJJJJJJLLLHDDHKLLLLLLLL",
)

def draw_racecar_sprite(x, wobble):
    top = RACECAR_TOP_Y + wobble
    for row_index, row in enumerate(RACECAR_SPRITE):
        start = 0
        while start < len(row):
            char = row[start]
            end = start + 1
            while end < len(row) and row[end] == char:
                end += 1
            graphics.set_pen(RACECAR_PENS[ord(char) - ord('A')])
            graphics.rectangle(x + start, top + row_index, end - start, 1)
            start = end


def draw_racecar():
    """Advance and draw the race car animation."""
    global racecar_x, racecar_phase, racecar_speed, racecar_phase_started_ms
    global racecar_last_step_ms, racecar_frame

    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, racecar_last_step_ms) < RACECAR_STEP_MS:
        return
    racecar_last_step_ms = now_ms
    racecar_frame += 1
    in_phase_ms = time.ticks_diff(now_ms, racecar_phase_started_ms)

    if racecar_phase == RACE_IN:
        # Brake smoothly: big steps at first, easing down to 1px
        racecar_x += max(1, (RACECAR_CENTER_X - racecar_x) // 6)
        if racecar_x >= RACECAR_CENTER_X:
            racecar_x = RACECAR_CENTER_X
            racecar_phase = RACE_REV
            racecar_phase_started_ms = now_ms
    elif racecar_phase == RACE_REV:
        if in_phase_ms >= RACECAR_PAUSE_MS:
            racecar_phase = RACE_GO
            racecar_speed = 1
            racecar_phase_started_ms = now_ms
    elif racecar_phase == RACE_GO:
        racecar_speed = min(racecar_speed + 1, 14)
        racecar_x += racecar_speed
        if racecar_x > 64:
            racecar_phase = RACE_GAP
            racecar_phase_started_ms = now_ms
    elif in_phase_ms >= RACECAR_GAP_MS:
        reset_racecar()

    graphics.set_pen(BLACK)
    graphics.clear()

    # Road
    graphics.set_pen(GREY)
    graphics.rectangle(0, 28, 64, 2)

    if racecar_phase == RACE_GAP:
        i75.update(graphics)
        return

    wobble = 0
    if racecar_phase == RACE_REV:
        wobble = (in_phase_ms // 90) % 2  # Visible engine shake while revving
        # Exhaust puffs behind the car
        graphics.set_pen(WHITE)
        graphics.rectangle(racecar_x - 3 - (racecar_frame % 3), 24, 2, 2)
    elif racecar_phase == RACE_GO:
        # Speed lines trailing behind the car
        graphics.set_pen(WHITE)
        trail = min(racecar_speed * 2, 22)
        for y in (17, 21, 25):
            graphics.rectangle(racecar_x - trail, y, trail, 1)

    draw_racecar_sprite(racecar_x, wobble)
    i75.update(graphics)

def deactivate_scrolling_message():
    """Disable message mode state without changing the current mode."""
    global message_active, message_text, message_timeout_at

    message_active = False
    message_text = ''
    message_timeout_at = 0

def activate_scrolling_message(text):
    """Enable scrolling message mode and preserve current mode for restore."""
    global message_text, message_active, message_scroll_x, message_last_step_ms, message_timeout_at
    global message_restore_mode, current_mode

    message_value = str(text)
    if message_value.strip() == '':
        return

    if current_mode != MODE_MESSAGE:
        message_restore_mode = current_mode

    message_text = message_value
    message_active = True
    message_scroll_x = MESSAGE_START_X
    message_last_step_ms = time.ticks_ms()
    message_timeout_at = (time.time() + MESSAGE_DISPLAY_SECONDS) if MESSAGE_DISPLAY_SECONDS > 0 else 0
    current_mode = MODE_MESSAGE
    print('Message mode ON:', message_text)
    publish_state()

def clear_scrolling_message(reason=''):
    """Clear scrolling message and return to previous mode."""
    global current_mode, message_active, message_text, message_timeout_at, traffic_timer, traffic_index

    if reason:
        print('Message mode OFF:', reason)

    message_active = False
    message_text = ''
    message_timeout_at = 0

    if current_mode == MODE_MESSAGE:
        if (message_restore_mode in (MODE_TRAFFIC, MODE_MANUAL, MODE_CLOCK, MODE_TRACTOR, MODE_RACECAR)
                or (message_restore_mode == MODE_GIF and gif_animation is not None)):
            current_mode = message_restore_mode
        else:
            current_mode = MODE_TRAFFIC

        if current_mode == MODE_TRAFFIC:
            traffic_timer = time.time()
            traffic_index = 0

        publish_state()

def draw_scrolling_message():
    """Draw scrolling message and handle timeout/looping."""
    global message_scroll_x, message_last_step_ms

    if not message_active:
        clear_scrolling_message('inactive')
        return

    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, message_last_step_ms) >= MESSAGE_SCROLL_STEP_MS:
        message_last_step_ms = now_ms
        message_scroll_x -= 1

        approx_width = len(message_text) * 8
        if message_scroll_x < -approx_width:
            message_scroll_x = 64

    graphics.set_pen(BLACK)
    graphics.clear()
    graphics.set_pen(clock_color_pen)
    graphics.text(message_text, message_scroll_x, MESSAGE_TEXT_Y, scale=1)
    i75.update(graphics)

    if message_timeout_at and time.time() >= message_timeout_at:
        clear_scrolling_message('timeout')
        publish_message_state()

# The last GIF stays in RAM so it can be selected again until stopped or rebooted.
gif_animation = None
gif_frame_delay = 100
gif_next_frame_ms = None


def enter_gif_mode():
    global current_mode, gif_next_frame_ms
    deactivate_scrolling_message()
    current_mode = MODE_GIF
    gif_next_frame_ms = None
    publish_state()


def receive_gif(payload):
    global gif_animation, gif_frame_delay
    if payload in (b'', b'stop'):
        gif_animation = None
        if current_mode == MODE_GIF:
            advance_mode()
        return

    try:
        if len(payload) > GIF_MAX_PAYLOAD:
            raise ValueError('GIF payload is too large')
        gc.collect()
        data = payload if payload[:3] == b'GIF' else ubinascii.a2b_base64(payload)
        from gifdecoder import GifFrames
        animation = GifFrames(data)
        delay = animation.next_frame()
        if delay is None:
            raise ValueError('GIF contains no frames')
    except Exception as exc:
        print('GIF rejected:', exc)
        return

    gif_animation = animation
    gif_frame_delay = delay
    enter_gif_mode()


def draw_gif():
    global gif_animation, gif_frame_delay, gif_next_frame_ms
    if gif_animation is None:
        advance_mode()
        return

    now_ms = time.ticks_ms()
    if gif_next_frame_ms is not None and time.ticks_diff(now_ms, gif_next_frame_ms) < 0:
        return

    try:
        if gif_next_frame_ms is not None:
            delay = gif_animation.next_frame()
            if delay is None:
                gif_animation.rewind()
                delay = gif_animation.next_frame()
            gif_frame_delay = delay
        gif_animation.draw(graphics, i75, brightness_percent)
        gif_next_frame_ms = time.ticks_add(now_ms, gif_frame_delay)
    except Exception as exc:
        print('GIF playback failed:', exc)
        gif_animation = None
        advance_mode()


def advance_mode():
    """Step through the regular modes, leaving any scrolling message."""
    global current_mode, traffic_timer, traffic_index, tractor_x

    deactivate_scrolling_message()
    if current_mode == MODE_TRAFFIC:
        current_mode = MODE_MANUAL
    elif current_mode == MODE_MANUAL:
        current_mode = MODE_CLOCK
    elif current_mode == MODE_CLOCK:
        current_mode = MODE_TRACTOR
        tractor_x = -TRACTOR_WIDTH
    elif current_mode == MODE_TRACTOR:
        current_mode = MODE_RACECAR
        reset_racecar()
    elif current_mode == MODE_RACECAR and gif_animation is not None:
        enter_gif_mode()
        return
    else:
        current_mode = MODE_TRAFFIC
        traffic_timer = time.time()
        traffic_index = 0
    publish_state()


def check_button():
    global last_button_time

    current_time = time.ticks_ms()
    if i75.switch_pressed(BUTTON_A):
        if time.ticks_diff(current_time, last_button_time) > DEBOUNCE_MS:
            last_button_time = current_time
            advance_mode()


def is_auto_clock_window(now_tuple):
    """Return True when current local time is inside the auto-clock window."""
    current_minutes = (now_tuple[3] * 60) + now_tuple[4]
    start_minutes = (AUTO_CLOCK_START_HOUR * 60) + AUTO_CLOCK_START_MINUTE
    end_minutes = (AUTO_CLOCK_END_HOUR * 60) + AUTO_CLOCK_END_MINUTE

    if start_minutes == end_minutes:
        return False

    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes

    # Overnight window that crosses midnight.
    return current_minutes >= start_minutes or current_minutes < end_minutes

def apply_auto_clock_schedule():
    """Automatically switch to clock mode inside configured schedule window."""
    global current_mode, auto_clock_active, mode_before_auto_clock, traffic_timer, traffic_index
    global last_auto_clock_brightness_enforce

    if message_active:
        return

    if not AUTO_CLOCK_ENABLED:
        auto_clock_active = False
        return

    now_tuple = local_now()
    in_window = is_auto_clock_window(now_tuple)

    if in_window and not auto_clock_active:
        mode_before_auto_clock = current_mode
        auto_clock_active = True
        print('Auto-clock activating: dimming to {}% and changing to RED'.format(AUTO_CLOCK_START_BRIGHTNESS))

        if brightness_supported:
            # Set brightness (software color scaling)
            if set_display_brightness(AUTO_CLOCK_START_BRIGHTNESS):
                publish_brightness_state()
        
        # Change clock color to RED for night mode visibility
        set_clock_color_rgb(255, 0, 0)
        publish_clock_color_state()
        publish_clock_color_light_state()

        if current_mode != MODE_CLOCK:
            current_mode = MODE_CLOCK
            print('Auto clock ON ({:02d}:{:02d})'.format(now_tuple[3], now_tuple[4]))
            publish_state()
        return

    if (not in_window) and auto_clock_active:
        auto_clock_active = False
        print('Auto-clock deactivating: restoring brightness to {}% and color to BLUE'.format(AUTO_CLOCK_END_BRIGHTNESS))

        if brightness_supported and set_display_brightness(AUTO_CLOCK_END_BRIGHTNESS):
            publish_brightness_state()
        
        # Restore clock color to blue
        set_clock_color_rgb(0, 100, 255)
        publish_clock_color_state()
        publish_clock_color_light_state()

        if current_mode == MODE_CLOCK:
            current_mode = mode_before_auto_clock
            if current_mode == MODE_TRAFFIC:
                traffic_timer = time.time()
                traffic_index = 0
            print('Auto clock OFF ({:02d}:{:02d})'.format(now_tuple[3], now_tuple[4]))
            publish_state()

    if auto_clock_active and AUTO_CLOCK_LOCK_BRIGHTNESS and brightness_supported:
        now_ts = time.time()
        if brightness_percent != AUTO_CLOCK_START_BRIGHTNESS or \
                (now_ts - last_auto_clock_brightness_enforce) >= AUTO_CLOCK_BRIGHTNESS_ENFORCE_SECONDS:
            if set_display_brightness(AUTO_CLOCK_START_BRIGHTNESS):
                last_auto_clock_brightness_enforce = now_ts
                print('Auto clock brightness enforced:', AUTO_CLOCK_START_BRIGHTNESS)
                publish_brightness_state()

# Main loop
print('Entering main loop - traffic mode')
traffic_timer = time.time()

if WATCHDOG_ENABLED:
    wdt = machine.WDT(timeout=WATCHDOG_TIMEOUT_MS)
    print('Watchdog enabled:', WATCHDOG_TIMEOUT_MS, 'ms')

loop_count = 0
while True:
    if loop_count == 0:
        # Print diagnostic info every ~5 seconds
        now_tuple = local_now()
        in_window = is_auto_clock_window(now_tuple)
        print('--- Status: {:02d}:{:02d} | Window: {} | Auto-clock: {} | Mode: {} | Brightness: {}% ---'.format(
            now_tuple[3], now_tuple[4], 
            'YES' if in_window else 'NO',
            'ACTIVE' if auto_clock_active else 'inactive',
            ['traffic','manual','clock','message','tractor','racecar','gif'][current_mode],
            brightness_percent
        ))
    loop_count = (loop_count + 1) % 50  # Print every ~5 seconds
    
    wdt_feed()
    apply_auto_clock_schedule()
    ensure_wifi_runtime()
    ensure_online_services_runtime()
    check_button()
    check_mqtt()  # Check for MQTT commands

    # Guard: if auto-clock activated, prevent retained MQTT messages or any other
    # override from raising brightness above the intended dim level.
    if auto_clock_active and brightness_supported and brightness_percent != AUTO_CLOCK_START_BRIGHTNESS:
        set_display_brightness(AUTO_CLOCK_START_BRIGHTNESS)
        publish_brightness_state()

    if current_mode == MODE_TRAFFIC:
        # Traffic light mode - automatic cycling
        current_time = time.time()
        elapsed = current_time - traffic_timer
        
        current_state = traffic_sequence[traffic_index]
        duration = traffic_durations[current_state]
        
        if elapsed >= duration:
            # Move to next state
            traffic_index = (traffic_index + 1) % len(traffic_sequence)
            traffic_timer = current_time
            current_state = traffic_sequence[traffic_index]
        
        draw_traffic_light(current_state)
        pause(0.1)
    
    elif current_mode == MODE_MANUAL:
        # Manual mode - hold specific light
        draw_traffic_light(manual_light_state)
        pause(0.1)

    elif current_mode == MODE_TRACTOR:
        draw_tractor()
        pause(0.02)

    elif current_mode == MODE_RACECAR:
        draw_racecar()
        pause(0.02)

    elif current_mode == MODE_MESSAGE:
        # Scrolling message mode
        draw_scrolling_message()
        pause(0.05)
        
    else:
        # Clock mode
        draw_clock()
        pause(0.1)
