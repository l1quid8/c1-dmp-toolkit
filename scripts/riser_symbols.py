"""Approved schematic symbols. Pure geometry shared by Canvas and vector export."""
from dataclasses import dataclass

from riser_drawing import TextRun, wrap_text


@dataclass(frozen=True)
class SymbolPart:
    kind: str
    coords: tuple
    fill: bool = False
    width: float = 1.5


def equipment_location(design, ref):
    from riser_scene import _location
    return _location(design, ref)


def detailed_size(design, ref):
    if ref == 'MSP':
        w,h,size,text_width,baseline = 440.,220.,23.,290.,125.
    elif ref.startswith('RSP-'):
        w,h,size,text_width,baseline = 400.,200.,21.,360.,130.
    elif any(s.id == ref for s in design.splitters):
        w,h,size,text_width,baseline = 210.,135.,16.,186.,71.
    else:
        return 180.,144.
    lines=wrap_text(equipment_location(design,ref),text_width,size)
    return w, max(h,baseline+(len(lines)-1)*size*1.3+size*.25+44)


def symbol_parts(design, element):
    x,y,w,h=element.x,element.y,element.width,element.height
    if element.symbol_style != 'detailed':
        shape='ellipse' if element.ref.startswith('KEYPAD-') else 'rectangle'
        return [SymbolPart(shape,(x,y,x+w,y+h),True,2)]
    if any(s.id==element.ref for s in design.splitters):
        b=min(10.5,w/10,h/10)
        return [SymbolPart('polygon',(x+b,y,x+w-b,y,x+w,y+b,x+w,y+h-b,
                                      x+w-b,y+h,x+b,y+h,x,y+h-b,x,y+b),True,2)]
    if not element.ref.startswith('KEYPAD-'):
        return [SymbolPart('rectangle',(x,y,x+w,y+h),True,2)]
    # One logical device, not independent drawing objects. Screen is an ID,
    # not a guessed hardware model number. Coordinates scale with the body.
    def p(kind,coords,fill=False,width=1.3):
        return SymbolPart(kind,tuple((x+c*w/180 if i%2==0 else y+c*h/144)
                                     for i,c in enumerate(coords)),fill,width)
    parts=[p('polygon',(10,0,170,0,180,10,180,134,170,144,10,144,0,134,0,10),True,2),
           p('line',(28,8,28,136)),p('rectangle',(43,15,164,49))]
    for row in range(4):
        for col in range(3):
            cx,cy=68+col*36,69+row*19
            parts.append(p('ellipse',(cx-5,cy-5,cx+5,cy+5)))
    parts += [p('ellipse',(10,99,16,105)),p('ellipse',(10,116,16,122))]
    return parts


def detailed_text(design, element):
    x,y,w,h=element.x,element.y,element.width,element.height
    cx=x+w/2
    result=[]
    def wrapped(text,baseline,size,width=None,center=None):
        for i,line in enumerate(wrap_text(text,width or w-24,size)):
            result.append(TextRun(cx if center is None else center,baseline+i*size*1.3,line,size))
    location=equipment_location(design,element.ref)
    if element.ref=='MSP':
        result.append(TextRun(cx,y+77,'MSP',52,True))
        wrapped(location,y+125,23,w-150,cx+35)
        result.append(TextRun(x+12,y+h/2-7,'KP BUS',16,True,'start'))
        from riser_scene import port_point
        for name in sorted({e.source.port_id for e in design.connections
                            if e.source.device_id=='MSP'}-{'KP BUS'}):
            px,py=port_point(element,name,output=True)
            result.append(TextRun(px,py-15,name,16,True))
    elif element.ref.startswith('KEYPAD-'):
        result.append(TextRun(x+w*104/180,y+h*39/144,element.ref,19,True))
        wrapped(location,y+h+28,16,max(w,300))
    elif any(s.id==element.ref for s in design.splitters):
        result += [TextRun(cx,y+20,'IN',16),TextRun(cx,y+46,element.ref,22,True)]
        wrapped(location,y+71,16)
        result.extend(TextRun(x+w*i/4,y+h-17,str(i),16,True) for i in range(1,4))
    else:
        rsp=next((r for r in design.rsps if f'RSP-{r.number}'==element.ref),None)
        result.append(TextRun(cx,y+85,element.ref,42,True))
        if rsp:
            result.append(TextRun(cx,y+29,rsp.model,17,True))
            if rsp.zones:
                result.append(TextRun(cx,y+h-21,f'Z{min(rsp.zones)}-Z{max(rsp.zones)}',18))
        wrapped(location,y+130,21,w-40)
    return result


def text_bounds(run):
    import fitz
    width=fitz.get_text_length(run.text,fontname='hebo' if run.bold else 'helv',fontsize=run.size)
    left=run.x-width/2 if run.anchor=='middle' else run.x-width if run.anchor=='end' else run.x
    return left,run.y-run.size,left+width,run.y+run.size*.25


def terminal_parts(design, element):
    if element.symbol_style != 'detailed':
        return []
    from riser_scene import port_point
    if element.ref=='MSP':
        ports=[(name,True) for name in sorted({e.source.port_id for e in design.connections
                                             if e.source.device_id=='MSP'})]
    elif any(s.id==element.ref for s in design.splitters):
        ports=[('IN',False)]+[(f'OUT{i}',True) for i in range(1,4)]
    else:
        ports=[('IN',False)]
    result=[]
    for name,output in ports:
        x,y=port_point(element,name,output=output)
        result.append(SymbolPart('ellipse',(x-4,y-4,x+4,y+4)))
    return result


def caption_boxes(design, element):
    if element.symbol_style!='detailed':
        return []
    return [text_bounds(r) for r in detailed_text(design,element)
            if r.y>element.y+element.height]


def modern_title(document):
    """Content-led title cells; long values wrap instead of overwriting the next cell."""
    tb=document.title_block
    x=document.page_width-348
    w=312
    rows=[]; rules=[]
    y=220.
    sections=[('PROJECT',tb.school_name,26,True),('LOCAL CODE',tb.local_code,22,True),
              ('ADDRESS',tb.address,17,False)]
    if tb.project_title and tb.project_title!=tb.school_name:
        sections.append(('PROJECT TITLE',tb.project_title,18,True))
    sections += [('DRAWING',tb.drawing_title,26,True),('SYSTEM',tb.system,21,True),
                 ('ISSUE DATE',tb.issue_date,18,False),('DRAWN BY',tb.drawn_by,18,False),
                 ('CHECKED BY',tb.checked_by,18,False),
                 ('REVISION RECORD','\n'.join(tb.revisions) or 'No revisions recorded',16,False)]
    for label,value,size,bold in sections:
        rows.append(TextRun(x+22,y,label,15,True,'start')); y+=34
        for line in wrap_text(value,w-44,size,bold):
            rows.append(TextRun(x+22,y,line,size,bold,'start')); y+=size*1.3
        y+=18; rules.append(((x,y),(x+w,y))); y+=34
    # Keep the sheet identifier at the foot; validation warns if exceptionally
    # verbose metadata needs editing to fit a single sheet.
    foot=max(y,document.page_height-160)
    rules.append(((x,foot-28),(x+w,foot-28)))
    rows += [TextRun(x+22,foot,'SHEET',15,True,'start'),
             TextRun(x+w/2,foot+82,tb.sheet_number,46,True)]
    return rows,rules
