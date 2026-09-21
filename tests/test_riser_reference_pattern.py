"""The approved Arminta sheet defines a branching pattern, not fixed coordinates."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from parse_dmp_worksheet import DMPDesign, Keypad, RSP, SiteInfo, Splitter
from riser_model import DevicePortRef
from riser_presentation import layout_presentation
from riser_scene import validate_riser
from topology_service import connect


def reference_pattern():
    design = DMPDesign(
        site_info=SiteInfo(school_name='REFERENCE SCHOOL', xr550_location='MDF'),
        splitters=[
            Splitter('KP-710-1', 'KP', 'MDF', outputs=['Spare'] * 3),
            Splitter('710-KP-2', 'KP', 'PLANT ROOM', outputs=['Spare'] * 3),
            *[Splitter(f'710-LX500-{i}', 'LX', f'ROOM {i}',
                       outputs=['Spare'] * 3) for i in range(1, 8)],
        ],
        rsps=[RSP(i, f'ROOM {i}', [500 + i * 16]) for i in range(1, 8)],
        keypads=[Keypad(i, 'MSP', f'KEYPAD ROOM {i}') for i in range(1, 5)],
    )
    wiring = [
        ('MSP', 'KP BUS', 'KEYPAD-1'), ('MSP', 'PROG', 'KP-710-1'),
        ('MSP', 'LX500', '710-LX500-1'),
        ('KP-710-1', 'OUT1', 'KEYPAD-2'),
        ('KP-710-1', 'OUT2', 'KEYPAD-3'),
        ('KP-710-1', 'OUT3', '710-KP-2'),
        ('710-KP-2', 'OUT1', 'KEYPAD-4'),
        ('710-LX500-1', 'OUT1', 'RSP-1'),
        ('710-LX500-1', 'OUT2', 'RSP-2'),
        ('710-LX500-1', 'OUT3', '710-LX500-2'),
        ('710-LX500-2', 'OUT1', '710-LX500-3'),
        ('710-LX500-2', 'OUT2', '710-LX500-4'),
        ('710-LX500-3', 'OUT1', 'RSP-3'),
        ('710-LX500-3', 'OUT2', '710-LX500-5'),
        ('710-LX500-3', 'OUT3', '710-LX500-7'),
        ('710-LX500-4', 'OUT1', 'RSP-7'),
        ('710-LX500-5', 'OUT1', 'RSP-5'),
        ('710-LX500-5', 'OUT2', '710-LX500-6'),
        ('710-LX500-6', 'OUT1', 'RSP-4'),
        ('710-LX500-7', 'OUT1', 'RSP-6'),
    ]
    for source, port, target in wiring:
        connect(design, DevicePortRef(source, port), DevicePortRef(target, 'IN'))
    return design


def test_reference_pattern_uses_backbone_and_paired_lower_shelves():
    design = reference_pattern()
    doc = layout_presentation(design)
    at = {e.ref: e for e in doc.elements.values() if e.kind == 'device'}
    assert at['KEYPAD-1'].x < at['MSP'].x < at['710-LX500-1'].x
    assert abs(at['KEYPAD-1'].y - at['MSP'].y) < at['MSP'].height
    assert at['KP-710-1'].y > at['MSP'].y + at['MSP'].height
    assert abs(at['710-LX500-1'].y - at['710-LX500-2'].y) < 90
    assert at['RSP-1'].y > at['710-LX500-1'].y + at['710-LX500-1'].height
    assert at['RSP-2'].y > at['710-LX500-1'].y + at['710-LX500-1'].height
    assert at['710-LX500-3'].y > at['RSP-1'].y
    assert at['710-LX500-4'].y > at['RSP-2'].y
    lower = [at[f'710-LX500-{i}'] for i in (5, 6, 7)]
    assert max(e.y for e in lower) - min(e.y for e in lower) < 90
    for source, target in (('710-LX500-5', 'RSP-5'),
                           ('710-LX500-6', 'RSP-4'),
                           ('710-LX500-7', 'RSP-6')):
        parent, child = at[source], at[target]
        assert child.y > parent.y + parent.height
        assert abs((child.x + child.width / 2) -
                   (parent.x + parent.width / 2)) < child.width / 2
    assert not [issue for issue in validate_riser(design, doc)
                if issue.code in {'scene.off_page', 'scene.overlap',
                                  'scene.cable_through_device',
                                  'scene.caption_overlap',
                                  'scene.uncovered_intersection'}]


def test_expander_zones_crossing_an_lx_bus_are_reported():
    design = reference_pattern()
    design.rsps[5].zones = list(range(589, 605))
    issues = validate_riser(design, layout_presentation(design))
    overflow = [issue for issue in issues if issue.code == 'zones.cross_bus']
    assert len(overflow) == 1
    assert overflow[0].ref == 'RSP-6'
    assert '601–604' in overflow[0].message


def test_x00_zone_start_is_not_mistaken_for_the_prior_bus():
    design = reference_pattern()
    design.rsps[5].zones = list(range(500, 516))
    assert not [issue for issue in validate_riser(design, layout_presentation(design))
                if issue.code == 'zones.cross_bus']
