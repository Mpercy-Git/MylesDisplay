from interstate75 import Interstate75, DISPLAY_INTERSTATE75_64X32
import time

# Simple test - just show traffic lights without WiFi/MQTT
print("Starting simple traffic light test...")

# Initialize display
i75 = Interstate75(display=DISPLAY_INTERSTATE75_64X32, stb_invert=False, panel_type=Interstate75.PANEL_GENERIC)
graphics = i75.display
graphics.set_font("bitmap8")

# Define colors
BLACK = graphics.create_pen(0, 0, 0)
RED = graphics.create_pen(255, 0, 0)
YELLOW = graphics.create_pen(255, 255, 50)
GREEN = graphics.create_pen(0, 255, 0)
GREY = graphics.create_pen(50, 50, 50)
WHITE = graphics.create_pen(255, 255, 255)

# Test pattern
print("Drawing test pattern...")
graphics.set_pen(BLACK)
graphics.clear()
graphics.set_pen(WHITE)
graphics.text("TEST", 20, 12, scale=1)
i75.update(graphics)
time.sleep(2)

# Traffic light positions
circle_y = 16
positions = {
    "red": (11, circle_y),
    "yellow": (32, circle_y),
    "green": (53, circle_y)
}

# Simple cycle through lights
lights = ["red", "yellow", "green"]
colors = {"red": RED, "yellow": YELLOW, "green": GREEN}

print("Starting traffic light cycle...")
while True:
    for active_light in lights:
        graphics.set_pen(BLACK)
        graphics.clear()
        
        # Draw all three circles
        for light, (x, y) in positions.items():
            if active_light == light:
                graphics.set_pen(colors[light])
            else:
                graphics.set_pen(GREY)
            graphics.circle(x, y, 10)
        
        i75.update(graphics)
        print(f"Showing: {active_light}")
        time.sleep(2)
