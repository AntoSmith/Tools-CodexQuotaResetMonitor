"""Generate the project's original vector mark and its Windows icon sizes.

Pillow is only needed when regenerating assets, not when running/building the app.
"""
from pathlib import Path
import math

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'
ASSETS.mkdir(exist_ok=True)
SCALE = 4
SIZE = 512
TEAL = '#6EE7CA'
WHITE = '#F4F8FF'
points = [(256 + 164 * math.cos(math.radians(a)),
           256 + 164 * math.sin(math.radians(a))) for a in range(-65, 246, 2)]
angle = math.radians(245)
end = (256 + 164 * math.cos(angle), 256 + 164 * math.sin(angle))
tangent = (-math.sin(angle), math.cos(angle))
normal = (-tangent[1], tangent[0])
arrow = [(end[0] + tangent[0]*30, end[1] + tangent[1]*30),
         (end[0] - tangent[0]*22 + normal[0]*33, end[1] - tangent[1]*22 + normal[1]*33),
         (end[0] - tangent[0]*22 - normal[0]*33, end[1] - tangent[1]*22 - normal[1]*33)]
left = [(221, 211), (176, 256), (221, 301)]
right = [(291, 211), (336, 256), (291, 301)]

def xy(items):
    return ' '.join(f'{x:.2f},{y:.2f}' for x, y in items)

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-labelledby="title desc">
  <title id="title">Codex 额度重置监控</title>
  <desc id="desc">深蓝圆角方形，青绿色重置箭头环与白色代码括号。</desc>
  <defs><linearGradient id="navy" x2="0" y2="1"><stop stop-color="#253B60"/><stop offset="1" stop-color="#101C32"/></linearGradient></defs>
  <rect x="8" y="8" width="496" height="496" rx="112" fill="url(#navy)"/>
  <polyline points="{xy(points)}" fill="none" stroke="{TEAL}" stroke-width="36" stroke-linecap="round" stroke-linejoin="round"/>
  <polygon points="{xy(arrow)}" fill="{TEAL}"/>
  <g fill="none" stroke="{WHITE}" stroke-width="27" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="{xy(left)}"/><polyline points="{xy(right)}"/>
  </g>
</svg>
'''
(ASSETS / 'quota-monitor.svg').write_text(svg, encoding='utf-8')

image = Image.new('RGBA', (SIZE*SCALE, SIZE*SCALE))
gradient = Image.new('RGBA', image.size)
draw = ImageDraw.Draw(gradient)
for y in range(SIZE*SCALE):
    t = min(1, max(0, (y/SCALE-8)/496))
    color = tuple(round(a+(b-a)*t) for a, b in zip((37,59,96), (16,28,50))) + (255,)
    draw.line((0,y,SIZE*SCALE,y), fill=color)
mask = Image.new('L', image.size)
ImageDraw.Draw(mask).rounded_rectangle((8*SCALE,8*SCALE,504*SCALE,504*SCALE), radius=112*SCALE, fill=255)
image.paste(gradient, (0,0), mask)
draw = ImageDraw.Draw(image)

def line(items, color, width):
    scaled = [(round(x*SCALE), round(y*SCALE)) for x,y in items]
    draw.line(scaled, fill=color, width=width*SCALE, joint='curve')
    radius=width*SCALE/2
    for x,y in scaled:
        draw.ellipse((x-radius,y-radius,x+radius,y+radius), fill=color)

line(points, TEAL, 36)
draw.polygon([(round(x*SCALE),round(y*SCALE)) for x,y in arrow], fill=TEAL)
line(left, WHITE, 27)
line(right, WHITE, 27)
image = image.resize((512,512), Image.Resampling.LANCZOS)
image.save(ASSETS / 'quota-monitor.png')
image.resize((256,256), Image.Resampling.LANCZOS).save(ASSETS / 'quota-monitor-preview.png')
image.save(ASSETS / 'quota-monitor.ico', sizes=[(s,s) for s in (16,20,24,32,40,48,64,128,256)])

sheet = Image.new('RGB', (650,154), '#F2F5FA')
ink = ImageDraw.Draw(sheet)
x=20
for size in (16,24,32,48,64,96,128):
    icon=image.resize((size,size), Image.Resampling.LANCZOS)
    sheet.paste(icon,(x,12),icon)
    ink.text((x,140),str(size),fill='#192743')
    x+=size+20
sheet.save(ASSETS / 'quota-monitor-sizes.png')
print('Generated SVG, PNG, multi-size ICO and size comparison in assets/.')
