"""Normalize legacy libultrahdr ISO metadata for browser interoperability.

libultrahdr 1.5.1 can emit a common-denominator layout using reserved flag
0x08. Skia's ISO 21496-1 reader expects independent numerator/denominator
pairs. Expand that layout without changing either JPEG's compressed pixels.
Current libultrahdr already writes independent pairs and needs no change.
"""
import struct

ISO_NAMESPACE = b'urn:iso:std:iso:ts:21496:-1\0'


def _segments(data, start=0):
    """Yield JPEG header markers; stop before entropy-coded scan data."""
    if data[start:start + 2] != b'\xff\xd8':
        raise ValueError('Missing JPEG start marker')
    pos = start + 2
    while pos < len(data):
        if data[pos] != 0xff:
            raise ValueError('Malformed JPEG header')
        while pos + 1 < len(data) and data[pos + 1] == 0xff:
            pos += 1
        if pos + 4 > len(data):
            raise ValueError('Truncated JPEG header')
        marker = data[pos + 1]
        if marker in (0xda, 0xd9):
            return
        size = int.from_bytes(data[pos + 2:pos + 4], 'big')
        end = pos + 2 + size
        if size < 2 or end > len(data):
            raise ValueError('Invalid JPEG segment length')
        yield marker, pos, end, data[pos + 4:end]
        pos = end


def _gainmap_entry(data):
    """Find the second JPEG's absolute offset and its MPF size field."""
    for marker, start, end, payload in _segments(data):
        if marker != 0xe2 or not payload.startswith(b'MPF\0'):
            continue
        tiff = start + 8
        order = data[tiff:tiff + 2]
        if order not in (b'II', b'MM'):
            raise ValueError('Invalid MPF byte order')
        endian = '<' if order == b'II' else '>'

        def read(fmt, pos):
            if pos < tiff or pos + struct.calcsize(fmt) > end:
                raise ValueError('Truncated MPF directory')
            return struct.unpack_from(endian + fmt, data, pos)

        if read('H', tiff + 2)[0] != 42:
            raise ValueError('Invalid MPF TIFF header')
        directory = tiff + read('I', tiff + 4)[0]
        count = read('H', directory)[0]
        entries = None
        image_count = None
        for index in range(count):
            tag, kind, length, value = read('HHII', directory + 2 + 12 * index)
            if tag == 0xb001 and kind == 4 and length == 1:
                image_count = value
            if tag == 0xb002 and kind == 7 and length == 32:
                entries = tiff + value
        if image_count != 2 or entries is None:
            raise ValueError('Expected a two-image Ultra HDR MPF index')
        _, size, offset, _, _ = read('IIIHH', entries + 16)
        absolute = tiff + offset
        if size < 2 or absolute + size > len(data):
            raise ValueError('Gain map outside JPEG container')
        return absolute, size, entries + 20, endian
    raise ValueError('Ultra HDR output is missing its MPF index')


def normalize_iso_metadata(data):
    """Expand only the legacy common-denominator packet and update MPF size."""
    gain_start, gain_size, size_field, endian = _gainmap_entry(data)
    for marker, start, end, payload in _segments(data, gain_start):
        if marker != 0xe2 or not payload.startswith(ISO_NAMESPACE):
            continue
        metadata = payload[len(ISO_NAMESPACE):]
        if len(metadata) < 5:
            raise ValueError('Truncated ISO gain map metadata')
        if not metadata[4] & 0x08:
            return data
        if metadata[:2] != b'\0\0' or metadata[4] & 0x37:
            raise ValueError('Unsupported legacy ISO gain map flags/version')
        channels = 3 if metadata[4] & 0x80 else 1
        fields = 2 + 5 * channels
        if len(metadata) != 9 + 4 * fields or end > gain_start + gain_size:
            raise ValueError('Invalid common-denominator gain map metadata')
        denominator = metadata[5:9]
        if denominator == b'\0\0\0\0':
            raise ValueError('ISO gain map denominator cannot be zero')
        expanded = metadata[:4] + bytes([metadata[4] & ~0x08])
        expanded += b''.join(metadata[pos:pos + 4] + denominator
                             for pos in range(9, len(metadata), 4))
        packet = ISO_NAMESPACE + expanded
        segment = b'\xff\xe2' + struct.pack('>H', len(packet) + 2) + packet
        difference = len(segment) - (end - start)
        output = bytearray(data[:start] + segment + data[end:])
        struct.pack_into(endian + 'I', output, size_field, gain_size + difference)
        return bytes(output)
    # XMP-only libultrahdr builds have no ISO packet to normalize.
    return data
