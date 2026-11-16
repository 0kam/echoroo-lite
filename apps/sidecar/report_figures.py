"""Portable report figures drawn from saved cells, without a browser or Office."""
import io
import json
import math
from datetime import date,datetime,timezone
from functools import lru_cache
from html import escape
from pathlib import Path

from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

DATA=Path(__file__).parent/'data'/'report'


@lru_cache(maxsize=12)
def font(size):
    return ImageFont.truetype(str(DATA/'fonts'/'BIZUDGothic-Regular.ttf'),size)


def png_bytes(image):
    buffer=io.BytesIO();image.save(buffer,format='PNG');return buffer.getvalue()


def common_name(species,locale):
    """Locale name, then English, then the user's own name; empty when only the scientific name is known."""
    names=species['common_names'];custom=species.get('custom_names') or {}
    common=names.get(locale) or names.get('en') or next(iter(custom.values()),'')
    return common if common!=species['scientific_name'] else ''


def custom_method(report,t):
    """'AI made for this species (created ...)', dated in the site's time zone; SQLite stores UTC without an offset."""
    created=None
    if report.get('created_at'):
        moment=datetime.fromisoformat(report['created_at'])
        created=(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(ZoneInfo(report['site']['timezone'])).date()
    return t['custom_model_date'].format(month=created.month if created else '—',day=created.day if created else '—',
                                         mon=t['months'][created.month-1] if created else '—')


def cell_color(cell,report):
    if cell['status']=='no_recording':return '#f1f0f2'
    if cell['status'] in ('unanalysed','not_evaluated'):return '#fafafa'
    value=cell['candidate_rate']
    if value is None:return '#f1f0f2'
    if value==0:return '#ffffff'
    fraction=.25+.75*max(0,min(1,value))
    return '#'+''.join(f'{round(a+(b-a)*fraction):02x}' for a,b in zip((248,224,222),(160,80,76)))


def spiral(report,locale,counting_status_text):
    """Same scales as the app. SVG remains vector; PNG is rendered at 2x for print."""
    days=(date.fromisoformat(report['end_date'])-date.fromisoformat(report['start_date'])).days+1
    t=json.loads((DATA/'messages.json').read_text(encoding='utf-8'))[locale]
    scale=2;image=Image.new('RGB',(960*scale,1250*scale),'white');draw=ImageDraw.Draw(image)
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="1250" viewBox="0 0 960 1250">',
         '<rect width="960" height="1250" fill="white"/>']
    def polygon(points,color):
        draw.polygon([(round(x*scale),round(y*scale)) for x,y in points],fill=color)
        svg.append('<polygon points="'+' '.join(f'{x:.2f},{y:.2f}' for x,y in points)+f'" fill="{color}"/>')
    def label(x,y,value,size=26):
        while draw.textlength(value,font=font(size*scale))>900*scale and size>12:size-=1
        draw.text((round(x*scale),round(y*scale)),value,font=font(size*scale),anchor='mm',fill='#575279')
        svg.append(f'<text x="{x}" y="{y}" text-anchor="middle" dominant-baseline="central" font-family="BIZ UDGothic,sans-serif" font-size="{size}" fill="#575279">{escape(value)}</text>')
    cy=580
    def point(radius,hour):
        angle=hour*math.pi/12-math.pi/2
        return (480+radius*math.cos(angle),cy+radius*math.sin(angle))
    taxon=report['species'];common=common_name(taxon,locale)
    label(480,30,(common+' · ' if common else '')+taxon['scientific_name'],32)
    engine=report.get('engine')
    if engine=='custom':
        method=custom_method(report,t)
    else:
        from models import REGISTRY
        method=REGISTRY[engine]['name']
    rule=t['figure_rule'].format(score=f"{report['threshold']:.2f}") if report['threshold'] is not None else ''
    label(480,72,method+(' · '+rule if rule else ''),30)
    label(480,110,counting_status_text,30)
    label(480,146,report['site']['name']+' · '+report['start_date']+' — '+report['end_date']+' · '+report['site']['timezone'],26)
    if report['threshold'] is None:
        # Nothing is counted, so there is no chart: grey cells would read as "no recording".
        label(480,cy,t['figure_undecided'],26);svg.append('</svg>')
        return png_bytes(image),''.join(svg)
    inner,outer=60,340;ring=(outer-inner)/days
    for cell in report['cells']:
        day=(date.fromisoformat(cell['date'])-date.fromisoformat(report['start_date'])).days
        r0,r1=inner+day*ring,inner+(day+1)*ring
        hour=cell['hour'];steps=8
        points=[point(r1,hour+i/steps) for i in range(steps+1)]+[point(r0,hour+i/steps) for i in range(steps,-1,-1)]
        polygon(points,cell_color(cell,report))
        if cell['status'] in ('unanalysed','not_evaluated','partial'):
            x,y=point((r0+r1)/2,hour+.5)
            radius=max(.65,min(2,ring*.25))
            draw.ellipse(((x-radius)*scale,(y-radius)*scale,(x+radius)*scale,(y+radius)*scale),fill='#575279')
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="#575279"/>')
    for hour in (0,6,12,18):
        x,y=point(outer+48,hour);label(x,y,f'{hour:02d}:00',30)
    label(480,cy-9,str(days),30);label(480,cy+27,t['day_unit'],23)
    for day in (sorted({0,(days-1)//2,days-1}) if days>1 else []):
        x,y=point(inner+(day+.5)*ring,15)
        value=str(date.fromordinal(date.fromisoformat(report['start_date']).toordinal()+day))[5:]
        label(round(x),round(y),value,22)
    for i in range(200):
        c={'status':'analysed','candidate_rate':max(i/199,.00001)}
        polygon([(310+i*1.7,cy+430),(312+i*1.7,cy+430),(312+i*1.7,cy+460),(310+i*1.7,cy+460)],cell_color(c,report))
    label(230,cy+445,t['figure_less'],30)
    label(730,cy+445,t['figure_more'],30)
    for offset,key in ((500,'figure_detection'),(545,'figure_missing'),(590,'figure_partial'),(635,'figure_caution')):
        label(480,cy+offset,t[key],30)
    svg.append('</svg>')
    return png_bytes(image),''.join(svg)

