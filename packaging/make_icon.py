"""Generate packaging/blockhost.ico and docs/icon.png: an original pixel-art server rack (no Mojang assets)."""
import struct
import zlib

GRID = [
    "................",
    ".SSSSSSSSSSSSSS.",
    ".SkkkkkkkkkkLkS.",
    ".SkkkkkkkkkkkkS.",
    ".SSSSSSSSSSSSSS.",
    "................",
    ".SSSSSSSSSSSSSS.",
    ".SkkkkkkkkkkLkS.",
    ".SkkkkkkkkkkkkS.",
    ".SSSSSSSSSSSSSS.",
    "................",
    ".SSSSSSSSSSSSSS.",
    ".SkkkkkkkkkkLkS.",
    ".SkkkkkkkkkkkkS.",
    ".SSSSSSSSSSSSSS.",
    "................",
]
COLORS = {".": (0, 0, 0, 0), "S": (90, 95, 99, 255), "k": (38, 40, 42, 255),
          "L": (82, 196, 53, 255)}


def png(size: int) -> bytes:
    scale = size // 16
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            row += bytes(COLORS[GRID[y // scale][x // scale]])
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def ico(sizes=(16, 32, 48, 256)) -> bytes:
    images = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, images, strict=True):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return head + entries + b"".join(images)


if __name__ == "__main__":
    with open("packaging/blockhost.ico", "wb") as fh:
        fh.write(ico())
    with open("docs/icon.png", "wb") as fh:
        fh.write(png(256))
    print("wrote packaging/blockhost.ico and docs/icon.png")
