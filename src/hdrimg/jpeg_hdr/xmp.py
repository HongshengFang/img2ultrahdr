"""Google Ultra HDR v1.1 XMP packets, alongside reference-codec ISO data.

Field names, units and container layout follow:
https://developer.android.com/media/platform/hdr-image-format
The XML structure also matches libultrahdr's generateXmpForPrimaryImage and
generateXmpForSecondaryImage. This prototype uses scalar gain metadata.
"""
import math
import struct
import xml.etree.ElementTree as ET

from .metadata import _gainmap_entry, _segments

XMP_NAMESPACE = b'http://ns.adobe.com/xap/1.0/\0'
NS = {
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'Container': 'http://ns.google.com/photos/1.0/container/',
    'Item': 'http://ns.google.com/photos/1.0/container/item/',
    'hdrgm': 'http://ns.adobe.com/hdr-gain-map/1.0/',
}


def _packet(xml):
    payload = XMP_NAMESPACE + xml.encode('utf-8')
    if len(payload) + 2 > 65535:
        raise ValueError('Ultra HDR XMP exceeds one JPEG APP1 segment')
    return b'\xff\xe1' + struct.pack('>H', len(payload) + 2) + payload


def _xml(attributes, content=''):
    namespaces = ' '.join(f'xmlns:{prefix}="{uri}"' for prefix, uri in NS.items())
    return (f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="img2ultrahdr">'
            f'<rdf:RDF xmlns:rdf="{NS["rdf"]}">'
            f'<rdf:Description rdf:about="" {namespaces} {attributes}>'
            f'{content}</rdf:Description></rdf:RDF></x:xmpmeta>')


def _packets(data, start):
    return [payload[len(XMP_NAMESPACE):] for marker, _, _, payload in _segments(data, start)
            if marker == 0xe1 and payload.startswith(XMP_NAMESPACE)]


def add_google_xmp(data, metadata):
    """Add primary GContainer and secondary HDR XMP, preserving JPEG scans."""
    gain_start, gain_size, size_field, endian = _gainmap_entry(data)
    primary_xmp = _packets(data, 0)
    secondary_xmp = _packets(data, gain_start)
    if primary_xmp or secondary_xmp:
        # A reference-codec build with UHDR_WRITE_XMP=ON already supplies these.
        inspect_google_xmp(data)
        return data
    values = {'Version': '1.0', 'BaseRenditionIsHDR': 'False'}
    for field, name, logarithmic in [
        ('GainMapMin', 'min_content_boost', True),
        ('GainMapMax', 'max_content_boost', True),
        ('Gamma', 'gamma', False), ('OffsetSDR', 'offset_sdr', False),
        ('OffsetHDR', 'offset_hdr', False),
    ]:
        vector = getattr(metadata, name)
        if not vector[0] == vector[1] == vector[2]:
            raise ValueError('Prototype Google XMP writer requires scalar metadata')
        value = math.log2(vector[0]) if logarithmic else float(vector[0])
        values[field] = format(value, '.17g')
    values['HDRCapacityMin'] = format(math.log2(metadata.hdr_capacity_min), '.17g')
    values['HDRCapacityMax'] = format(math.log2(metadata.hdr_capacity_max), '.17g')
    gain_packet = _packet(_xml(' '.join(f'hdrgm:{k}="{v}"' for k, v in values.items())))
    directory = ('<Container:Directory><rdf:Seq>'
                 '<rdf:li rdf:parseType="Resource"><Container:Item '
                 'Item:Semantic="Primary" Item:Mime="image/jpeg"/></rdf:li>'
                 '<rdf:li rdf:parseType="Resource"><Container:Item '
                 'Item:Semantic="GainMap" Item:Mime="image/jpeg" '
                 f'Item:Length="{gain_size + len(gain_packet)}"/></rdf:li>'
                 '</rdf:Seq></Container:Directory>')
    primary_packet = _packet(_xml('hdrgm:Version="1.0"', directory))
    # Keep primary JFIF immediately after SOI. Inserting before MPF moves its
    # TIFF origin and the secondary JPEG equally, so the relative offset stays.
    first_marker = next(_segments(data))
    insertion = first_marker[2] if first_marker[0] == 0xe0 else 2
    output = bytearray(data[:insertion] + primary_packet + data[insertion:gain_start + 2]
                       + gain_packet + data[gain_start + 2:])
    shift = len(primary_packet)
    primary_size = struct.unpack_from(endian + 'I', data, size_field - 16)[0]
    struct.pack_into(endian + 'I', output, size_field - 16 + shift, primary_size + shift)
    struct.pack_into(endian + 'I', output, size_field + shift, gain_size + len(gain_packet))
    return bytes(output)


def inspect_google_xmp(data, metadata=None):
    """Check v1.1 fields, units and agreement between GContainer and MPF."""
    gain_start, gain_size, _, _ = _gainmap_entry(data)
    packets = [_packets(data, 0), _packets(data, gain_start)]
    if any(len(p) != 1 for p in packets):
        raise ValueError('Expected one Ultra HDR XMP packet in each JPEG')
    primary, gain = [ET.fromstring(p[0]) for p in packets]
    primary_description = primary.find('.//rdf:Description', NS)
    gain_description = gain.find('.//rdf:Description', NS)
    version = '{' + NS['hdrgm'] + '}Version'
    if (primary_description is None or gain_description is None or
            primary_description.get(version) != '1.0' or gain_description.get(version) != '1.0'):
        raise ValueError('Missing Google Ultra HDR format version')
    items = primary.findall('.//Container:Directory/rdf:Seq/rdf:li/Container:Item', NS)
    item_name = '{' + NS['Item'] + '}'
    if (len(items) != 2 or [item.get(item_name + 'Semantic') for item in items] != ['Primary', 'GainMap'] or
            any(item.get(item_name + 'Mime') != 'image/jpeg' for item in items) or
            int(items[1].get(item_name + 'Length', '-1')) != gain_size):
        raise ValueError('Google GContainer does not agree with the MPF gain map')
    gain_name = '{' + NS['hdrgm'] + '}'
    if gain_description.get(gain_name + 'BaseRenditionIsHDR') != 'False':
        raise ValueError('Google Ultra HDR requires an SDR base rendition')
    values = {key: float(gain_description.attrib[gain_name + key]) for key in
              ['GainMapMin', 'GainMapMax', 'Gamma', 'OffsetSDR', 'OffsetHDR',
               'HDRCapacityMin', 'HDRCapacityMax']}
    if not (all(math.isfinite(v) for v in values.values()) and
            values['GainMapMin'] <= 0 <= values['GainMapMax'] and values['Gamma'] > 0 and
            values['OffsetSDR'] >= 0 and values['OffsetHDR'] >= 0 and
            0 <= values['HDRCapacityMin'] < values['HDRCapacityMax']):
        raise ValueError('Google Ultra HDR metadata is outside its specified range')
    if metadata is not None:
        expected = {
            'GainMapMin': math.log2(metadata.min_content_boost[0]),
            'GainMapMax': math.log2(metadata.max_content_boost[0]),
            'Gamma': metadata.gamma[0], 'OffsetSDR': metadata.offset_sdr[0],
            'OffsetHDR': metadata.offset_hdr[0],
            'HDRCapacityMin': math.log2(metadata.hdr_capacity_min),
            'HDRCapacityMax': math.log2(metadata.hdr_capacity_max),
        }
        if any(not math.isclose(values[k], v, rel_tol=1e-5, abs_tol=1e-6)
               for k, v in expected.items()):
            raise ValueError('Google XMP parameters disagree with decoded gain map metadata')
    return {'google_ultrahdr_v1_1_xmp': True, 'gcontainer_mpf_agree': True,
            'base_rendition_is_hdr': False, 'gainmap_xmp': values}
