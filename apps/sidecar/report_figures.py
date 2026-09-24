"""Portable report figures drawn from saved cells, without a browser or Office."""
import base64
import io
import json
import math
from datetime import date
from functools import lru_cache
from html import escape
from pathlib import Path

import numpy as np
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
    value=cell['max_similarity'] if report['kind']=='search' else cell['candidate_rate']
    if value is None:return '#f1f0f2'
    if report['kind']=='detection' and value==0:return '#ffffff'
    low=(report['threshold'] if report['threshold'] is not None else -1) if report['kind']=='search' else 0
    if value<low:return '#e0dfe4'
    fraction=1 if low==1 else max(0,min(1,(value-low)/(1-low)))
    if report['kind']=='detection':fraction=.25+.75*fraction
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
    method=('BirdNET' if engine=='birdnet' else 'Perch') if report['kind']=='detection' else f"{t['search']} #{report['run_id']}"
    if engine=='custom':method=f"{t['custom_model']} #{report['model_id']}"
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
    low=report['threshold'] if report['kind']=='search' else 0
    start=(low if low is not None else -1)
    for i in range(200):
        value=start+(1-start)*i/199
        c={'status':'analysed','candidate_rate':max(value,.00001),'max_similarity':value}
        polygon([(310+i*1.7,941),(312+i*1.7,941),(312+i*1.7,961),(310+i*1.7,961)],cell_color(c,report))
    label(238,951,f'{start:.2f}' if report['kind']=='search' else '>0%',26)
    label(710,951,'1.00' if report['kind']=='search' else '100%',26)
    metric='figure_detection' if report['kind']=='detection' else 'figure_search_unset' if report['threshold'] is None else 'figure_search'
    label(480,992,t[metric],23)
    label(480,1030,t['figure_missing'],21)
    label(480,1068,t['figure_caution'],21)
    svg.append('</svg>')
    return png_bytes(image),''.join(svg)


def spectrum(source,start,end,marks=()):
    from audio import spectrogram
    result=spectrogram(source,start,end)
    palette=np.asarray(json.loads((DATA/'roseus.json').read_text()),dtype=np.uint8).reshape(256,3)
    pixels=np.frombuffer(base64.b64decode(result['pixels']),dtype=np.uint8).reshape(result['height'],result['width'])
    plot=Image.fromarray(palette[pixels]).resize((880,270))
    image=Image.new('RGB',(1040,380),'white');image.paste(plot,(110,45));draw=ImageDraw.Draw(image)
    for mark in marks:
        a=110+(mark['start_s']-start)/(end-start)*880
        b=110+(mark['end_s']-start)/(end-start)*880
        draw.line([(a,315),(a,57),(b,57),(b,315)],fill='#fff5bd',width=2)
    for y,frequency in [(45,result['frequency_max_hz']/1000),(180,result['frequency_max_hz']/2000),(315,0)]:
        draw.text((94,y),f'{frequency:.1f}',font=font(22),anchor='rm',fill='#373450')
    draw.text((63,15),'kHz',font=font(22),anchor='mm',fill='#373450')
    for x,value in [(125,start),(550,(start+end)/2),(975,end)]:
        draw.text((x,348),f'{value:.2f} s',font=font(22),anchor='mm',fill='#373450')
    return png_bytes(image),{k:v for k,v in result.items() if k!='pixels'}


def recording_effort(scope):
    daily=scope['daily'];values=[v['seconds']/3600 for v in daily];peak=max(values or [1]) or 1
    image=Image.new('RGB',(1600,450),'white');draw=ImageDraw.Draw(image)
    for index,value in enumerate(values):
        x=120+index*1400/max(1,len(values));width=max(1,1400/max(1,len(values))-.5)
        draw.rectangle((x,350-value/peak*290,x+width,350),fill='#a0504c')
    for fraction in (0,.5,1):
        y=350-fraction*290
        draw.text((100,y),f'{peak*fraction:.1f} h',font=font(25),anchor='rm',fill='#373450')
    if daily:
        for index in sorted({0,(len(daily)-1)//2,len(daily)-1}):
            x=120+(index+.5)*1400/len(daily)
            x=max(175,min(1420,x))
            draw.text((x,393),daily[index]['date'],font=font(26),anchor='mm',fill='#373450')
    return png_bytes(image)
