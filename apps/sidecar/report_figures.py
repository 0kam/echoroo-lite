"""Portable report figures drawn from saved cells, without a browser or Office."""
import io
import json
import math
from datetime import date
from functools import lru_cache
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DATA=Path(__file__).parent/'data'/'report'


@lru_cache(maxsize=12)
def font(size):
    return ImageFont.truetype(str(DATA/'fonts'/'BIZUDGothic-Regular.ttf'),size)


def png_bytes(image):
    buffer=io.BytesIO();image.save(buffer,format='PNG');return buffer.getvalue()


def cell_color(cell,report):
    if cell['status']=='no_recording':return '#f1f0f2'
    if cell['status'] in ('unanalysed','not_evaluated'):return '#fafafa'
    value=cell['candidate_rate']
    if value is None:return '#f1f0f2'
    if value==0:return '#ffffff'
    fraction=.25+.75*max(0,min(1,value))
    return '#'+''.join(f'{round(a+(b-a)*fraction):02x}' for a,b in zip((248,224,222),(160,80,76)))


def spiral(report,locale,criterion_text):
    """Same scales as the app. SVG remains vector; PNG is rendered at 2x for print."""
    days=(date.fromisoformat(report['end_date'])-date.fromisoformat(report['start_date'])).days+1
    t=json.loads((DATA/'messages.json').read_text(encoding='utf-8'))[locale]
    scale=2;image=Image.new('RGB',(960*scale,1120*scale),'white');draw=ImageDraw.Draw(image)
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="1120" viewBox="0 0 960 1120">',
         '<rect width="960" height="1120" fill="white"/>']
    def polygon(points,color):
        draw.polygon([(round(x*scale),round(y*scale)) for x,y in points],fill=color)
        svg.append('<polygon points="'+' '.join(f'{x:.2f},{y:.2f}' for x,y in points)+f'" fill="{color}"/>')
    def label(x,y,value,size=26):
        while draw.textlength(value,font=font(size*scale))>900*scale and size>12:size-=1
        draw.text((round(x*scale),round(y*scale)),value,font=font(size*scale),anchor='mm',fill='#575279')
        svg.append(f'<text x="{x}" y="{y}" text-anchor="middle" dominant-baseline="central" font-family="BIZ UDGothic,sans-serif" font-size="{size}" fill="#575279">{escape(value)}</text>')
    def point(radius,hour):
        angle=hour*math.pi/12-math.pi/2
        return (480+radius*math.cos(angle),530+radius*math.sin(angle))
    taxon=report['species'];common=taxon['common_names'].get(locale) or taxon['common_names'].get('en','')
    label(480,28,common+' · '+taxon['scientific_name'],26)
    engine=report.get('engine')
    method=f"{t['custom_model']} #{report['model_id']}" if engine=='custom' else 'BirdNET' if engine=='birdnet' else 'Perch'
    label(480,64,method+' · '+t['criterion']+': '+criterion_text,22)
    label(480,99,report['site']['name']+' · '+report['start_date']+' — '+report['end_date']+' · '+report['site']['timezone'],19)
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
    label(480,521,str(days),30);label(480,557,t['day_unit'],23)
    for day in (sorted({0,(days-1)//2,days-1}) if days>1 else []):
        x,y=point(inner+(day+.5)*ring,15)
        value=str(date.fromordinal(date.fromisoformat(report['start_date']).toordinal()+day))[5:]
        label(round(x),round(y),value,18)
    for i in range(200):
        c={'status':'analysed','candidate_rate':max(i/199,.00001)}
        polygon([(310+i*1.7,941),(312+i*1.7,941),(312+i*1.7,961),(310+i*1.7,961)],cell_color(c,report))
    label(238,951,'>0%',26)
    label(710,951,'100%',26)
    label(480,992,t['figure_detection' if report['threshold'] is not None else 'figure_undecided'],23)
    label(480,1030,t['figure_missing'],21)
    label(480,1068,t['figure_caution'],21)
    svg.append('</svg>')
    return png_bytes(image),''.join(svg)


def recording_schedule(scope):
    schedule=scope.get('schedule') or [{'date':row['date'],'hours':[row['seconds'] if hour==0 else 0 for hour in range(24)]} for row in scope['daily']]
    row_height=16;height=max(450,120+len(schedule)*row_height)
    image=Image.new('RGB',(1600,height),'white');draw=ImageDraw.Draw(image)
    left,right,top=190,1540,60
    for hour in range(0,25,3):
        x=left+(right-left)*hour/24
        draw.line((x,top-10,x,top+len(schedule)*row_height),fill='#dfdad9',width=1)
        draw.text((x,25),str(hour),font=font(24),anchor='mm',fill='#373450')
    for index,row in enumerate(schedule):
        y=top+index*row_height
        draw.text((left-12,y+row_height/2),row['date'],font=font(13),anchor='rm',fill='#373450')
        for hour,seconds in enumerate(row['hours']):
            if seconds>0:
                x0=left+(right-left)*hour/24;x1=left+(right-left)*(hour+1)/24
                draw.rectangle((x0,y,x1,y+row_height-1),fill='#a0504c')
    draw.text(((left+right)/2,height-28),'0–24 h',font=font(24),anchor='mm',fill='#373450')
    return png_bytes(image)
