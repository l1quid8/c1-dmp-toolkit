"""Deep keypad and LX branches must remain usable on the printable sheet."""
import copy

from test_riser_scene import DMPDesign, SiteInfo, Splitter, RSP, Keypad, DevicePortRef, connect
from test_release_editor_integration import editor
from riser_presentation import layout_presentation
from riser_scene import validate_riser


def dense_school():
    design = DMPDesign(site_info=SiteInfo(school_name='DENSE SCHOOL', xr550_location='ADMIN BUILDING'),
        splitters=[Splitter(f'710-KP-{i}', 'KP', f'ADMIN BLDG 1ST FLR ROOM {i}', outputs=['Spare']*3) for i in range(1,6)] +
                  [Splitter(f'710-LX500-{i}', 'LX', f'NORTH BLDG 1ST FLR CLASSROOM {i}', outputs=['Spare']*3) for i in range(1,7)],
        rsps=[RSP(i, f'NORTH BLDG 1ST FLR CLASSROOM {i}', [501+i*8]) for i in range(1,8)],
        keypads=[Keypad(i,'MSP',f'ADMIN BLDG 1ST FLR PRINCIPAL OFFICE {i}') for i in range(1,9)])
    links=[('MSP','KP BUS','KEYPAD-1'),('MSP','PROG','710-KP-1'),('MSP','LX500','710-LX500-1'),
           ('710-KP-1','OUT1','KEYPAD-2'),('710-KP-1','OUT2','KEYPAD-3'),('710-KP-1','OUT3','710-KP-2'),
           ('710-KP-2','OUT1','KEYPAD-4'),('710-KP-2','OUT2','710-KP-3'),('710-KP-2','OUT3','710-KP-4'),
           ('710-KP-3','OUT1','KEYPAD-5'),('710-KP-3','OUT2','KEYPAD-6'),
           ('710-KP-4','OUT1','710-KP-5'),('710-KP-4','OUT2','KEYPAD-8'),('710-KP-5','OUT1','KEYPAD-7'),
           ('710-LX500-1','OUT1','RSP-1'),('710-LX500-1','OUT2','RSP-2'),('710-LX500-1','OUT3','710-LX500-2'),
           ('710-LX500-2','OUT1','710-LX500-3'),('710-LX500-2','OUT2','710-LX500-4'),
           ('710-LX500-3','OUT1','RSP-3'),('710-LX500-3','OUT2','RSP-4'),
           ('710-LX500-4','OUT1','RSP-7'),('710-LX500-4','OUT2','710-LX500-5'),
           ('710-LX500-5','OUT1','RSP-5'),('710-LX500-5','OUT2','710-LX500-6'),('710-LX500-6','OUT1','RSP-6')]
    for source,port,target in links:
        connect(design,DevicePortRef(source,port),DevicePortRef(target,'IN'))
    return design


def test_dense_preview_fits_devices_captions_and_wires_without_changing_wiring():
    design=dense_school()
    before=copy.deepcopy(design)
    doc=layout_presentation(design)
    assert not [issue for issue in validate_riser(design,doc)
                if issue.code in {'scene.off_page','scene.overlap','scene.cable_through_device','scene.caption_overlap','scene.uncovered_intersection'}]
    assert design==before
    for prefix in ('710-KP-', '710-LX500-'):
        assert len({e.x for e in doc.elements.values() if e.kind=='device' and e.ref.startswith(prefix)})==1
    assert len([e for e in doc.elements.values() if e.kind=='device'])==27


def test_preview_opens_at_readable_scale_and_leaves_project_untouched(editor, monkeypatch):
    frame,_=editor
    tab=frame.riser_tab
    before=copy.deepcopy(tab.controller.document)
    monkeypatch.setattr(tab.canvas, 'winfo_width', lambda: 980)
    monkeypatch.setattr(tab.canvas, 'winfo_height', lambda: 400)
    tab.relayout()
    assert tab.zoom >= .3
    assert not tab._fit_mode
    assert tab.controller.document==before
    tab.cancel()
    assert tab.controller.document==before
