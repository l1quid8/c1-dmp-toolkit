"""Physical equipment locations, independent of drawing geometry."""
from dataclasses import dataclass
import re


@dataclass
class EquipmentLocation:
    id: str
    full_label: str
    building: str = ''
    floor: str = ''
    room: str = ''
    confirmed: bool = False


def location_key(value):
    return ' '.join((value or '').upper().split())


def is_unresolved(value):
    return location_key(value) in {'', 'UNKNOWN', 'UNSPECIFIED', 'TBD', 'TBC', 'N/A', '?'}


def legacy_normal_location(value):
    text = (value or 'UNSPECIFIED').upper()
    text = re.sub(r'\(\s*SERVICE\s+KEYPAD\s*\)', '', text)
    text = re.sub(r'\bBLDG\b', 'BUILDING', text)
    text = re.sub(r'\bFLR\b', 'FLOOR', text)
    text = re.sub(r'[()_,./-]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()
