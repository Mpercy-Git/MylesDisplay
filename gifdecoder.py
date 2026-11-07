"""Small GIF decoder for the Interstate75 display."""

from struct import unpack
import time


class GifFrames:
    """Decode a frame at a time into a composited RGB canvas."""

    MAX_BYTES = 16 * 1024

    def __init__(self, data, max_width=64, max_height=32):
        if len(data) > self.MAX_BYTES:
            raise ValueError("GIF exceeds 16 KiB")
        self.data = data
        self.position = 0
        if self.read(6) not in (b"GIF87a", b"GIF89a"):
            raise ValueError("Invalid GIF signature")
        self.width, self.height, flags, background, _ = unpack("<HHBBB", self.read(7))
        if not (0 < self.width <= max_width and 0 < self.height <= max_height):
            raise ValueError("GIF dimensions exceed the display")
        self.palette = self.read_palette(flags)
        offset = background * 3
        self.background = (self.palette[offset:offset + 3]
                           if self.palette and offset + 3 <= len(self.palette) else b"\0\0\0")
        self.first_block = self.position
        self.canvas = bytearray(self.width * self.height * 3)
        self.rewind()

    def read(self, count):
        end = self.position + count
        if end > len(self.data):
            raise ValueError("Truncated GIF")
        data = self.data[self.position:end]
        self.position = end
        return data

    def read_byte(self):
        if self.position >= len(self.data):
            raise ValueError("Truncated GIF")
        value = self.data[self.position]
        self.position += 1
        return value

    def read_palette(self, flags):
        return self.read(3 << ((flags & 7) + 1)) if flags & 128 else None

    def sub_blocks(self, collect=False):
        data = bytearray() if collect else None
        size = self.read_byte()
        while size:
            block = self.read(size)
            if collect:
                data.extend(block)
            size = self.read_byte()
        return data

    def rewind(self):
        self.position = self.first_block
        self.previous = None
        self.first_frame = True

    def fill(self, x, y, width, height, colour):
        row = colour * width
        for line in range(y, y + height):
            offset = (line * self.width + x) * 3
            self.canvas[offset:offset + len(row)] = row

    def next_frame(self):
        """Return the frame delay in milliseconds, or None at the GIF trailer."""
        delay, disposal, transparent = 100, 0, -1
        while True:
            block = self.read_byte()
            if block == 0x3B:
                return None
            if block == 0x2C:
                break
            if block != 0x21:
                raise ValueError("Unknown GIF block")
            if self.read_byte() == 0xF9:
                if self.read_byte() != 4:
                    raise ValueError("Invalid GIF graphics control block")
                flags, centiseconds, index = unpack("<BHB", self.read(4))
                if self.read_byte() != 0:
                    raise ValueError("Invalid GIF graphics control terminator")
                delay = max(20, centiseconds * 10) if centiseconds else 100
                disposal = (flags >> 2) & 7
                transparent = index if flags & 1 else -1
            else:
                self.sub_blocks()

        x, y, width, height, flags = unpack("<HHHHB", self.read(9))
        if not width or not height or x + width > self.width or y + height > self.height:
            raise ValueError("GIF frame exceeds the canvas")
        palette = self.read_palette(flags) or self.palette
        if not palette:
            raise ValueError("GIF frame has no palette")
        minimum = self.read_byte()
        pixels = self.decode_lzw(self.sub_blocks(True), minimum, width * height)

        if self.first_frame:
            self.fill(0, 0, self.width, self.height,
                      b"\0\0\0" if transparent >= 0 else self.background)
            self.first_frame = False
        elif self.previous:
            method, px, py, pw, ph, clear, backup = self.previous
            if method == 2:
                self.fill(px, py, pw, ph, clear)
            elif method == 3:
                self.canvas[:] = backup
        clear = b"\0\0\0" if transparent >= 0 else self.background
        self.previous = (disposal, x, y, width, height, clear,
                         bytes(self.canvas) if disposal == 3 else None)

        passes = ((0, 8), (4, 8), (2, 4), (1, 2)) if flags & 64 else ((0, 1),)
        source = 0
        for start, step in passes:
            for line in range(start, height, step):
                dest = ((y + line) * self.width + x) * 3
                for _ in range(width):
                    index = pixels[source]
                    source += 1
                    if index != transparent:
                        colour = index * 3
                        if colour + 3 > len(palette):
                            raise ValueError("Invalid GIF palette index")
                        self.canvas[dest:dest + 3] = palette[colour:colour + 3]
                    dest += 3
        return delay

    @staticmethod
    def decode_lzw(data, minimum, count):
        if not 2 <= minimum <= 8:
            raise ValueError("Invalid GIF LZW code size")
        clear = 1 << minimum
        end = clear + 1
        capacity = min(4096, end + 1 + count)
        prefix = bytearray(capacity * 2)
        suffix = bytearray(capacity)
        stack = bytearray(count + 1)
        output = bytearray(count)
        written = 0
        code_size = minimum + 1
        next_code = end + 1
        previous = -1
        bits = bit_count = position = 0
        first = 0
        while True:
            while bit_count < code_size:
                if position >= len(data):
                    raise ValueError("Missing GIF LZW end code")
                bits |= data[position] << bit_count
                bit_count += 8
                position += 1
            code = bits & ((1 << code_size) - 1)
            bits >>= code_size
            bit_count -= code_size
            if code == clear:
                code_size = minimum + 1
                next_code = end + 1
                previous = -1
                continue
            if code == end:
                if written != count:
                    raise ValueError("Incomplete GIF frame")
                return output
            current = code
            depth = 0
            if code == next_code and previous >= 0:
                stack[depth] = first
                depth += 1
                code = previous
            elif code >= next_code:
                raise ValueError("Invalid GIF LZW code")
            while code >= clear:
                if code <= end or depth >= count:
                    raise ValueError("Invalid GIF LZW chain")
                stack[depth] = suffix[code]
                depth += 1
                index = code * 2
                code = prefix[index] | (prefix[index + 1] << 8)
            first = code
            if written + depth + 1 > count:
                raise ValueError("GIF frame expands past its dimensions")
            output[written] = first
            written += 1
            while depth:
                depth -= 1
                output[written] = stack[depth]
                written += 1
            if previous >= 0 and next_code < capacity:
                index = next_code * 2
                prefix[index] = previous & 255
                prefix[index + 1] = previous >> 8
                suffix[next_code] = first
                next_code += 1
                if next_code == (1 << code_size) and code_size < 12:
                    code_size += 1
            previous = current

    def draw(self, graphics, display, brightness=100):
        width, height = graphics.get_bounds()
        left, top = (width - self.width) // 2, (height - self.height) // 2
        graphics.set_pen(graphics.create_pen(0, 0, 0))
        graphics.clear()
        pens = {}
        offset = 0
        for y in range(self.height):
            x = 0
            while x < self.width:
                colour = bytes(self.canvas[offset:offset + 3])
                end = x + 1
                offset += 3
                while end < self.width and self.canvas[offset:offset + 3] == colour:
                    end += 1
                    offset += 3
                pen = pens.get(colour)
                if pen is None:
                    r, g, b = colour
                    pen = graphics.create_pen(r * brightness // 100, g * brightness // 100,
                                              b * brightness // 100)
                    if len(pens) < 256:
                        pens[colour] = pen
                graphics.set_pen(pen)
                graphics.rectangle(left + x, top + y, end - x, 1)
                x = end
        display.update(graphics)


class Gif:
    """Blocking compatibility wrapper for the older demo."""

    def __init__(self, graphics, i75):
        self.graphics = graphics
        self.i75 = i75

    def play(self, filename_or_stream):
        if isinstance(filename_or_stream, str):
            with open(filename_or_stream, "rb") as stream:
                self.play(stream)
        else:
            self.play_from_bytes(filename_or_stream.read(GifFrames.MAX_BYTES + 1))

    def play_from_bytes(self, data):
        frames = GifFrames(data, *self.graphics.get_bounds())
        delay = frames.next_frame()
        while delay is not None:
            frames.draw(self.graphics, self.i75)
            time.sleep_ms(delay)
            delay = frames.next_frame()
