"""Rebuild the NSIS sidebar from the eMotionCut mark; Pillow is build-time only."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageFilter

root=Path(__file__).resolve().parent.parent
scale=4
W,H=164,314
BG='#14161B'
ACCENT='#2EE6C5'

im=Image.new('RGB',(W*scale,H*scale),BG)

# soft accent bloom behind the mark, so the panel is not a flat slab
glow=Image.new('RGB',(W*scale,H*scale),BG)
gd=ImageDraw.Draw(glow)
gd.ellipse([-40*scale,-70*scale,150*scale,120*scale],fill='#1b3f3b')
glow=glow.filter(ImageFilter.GaussianBlur(28*scale))
im=Image.blend(im,glow,.55)
draw=ImageDraw.Draw(im)

logo=Image.open(root/'public/emotioncut-icon-512.png').convert('RGBA').resize((72*scale,72*scale),Image.Resampling.LANCZOS)
im.paste(logo,(24*scale,30*scale),logo)

def text(x,y,value,size,color='#EAF2F0'):
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',size*scale)
    draw.text((x*scale,y*scale),value,font=font,fill=color)

text(24,122,'eMotionCut',19)
text(24,152,'智能剪辑工作台',11,ACCENT)
draw.line((24*scale,182*scale,140*scale,182*scale),fill='#33453f',width=scale)
text(24,200,'录制 · 剪辑 · 出片',11)
text(24,228,'完整离线组件',11,'#9fb4ac')
text(24,250,'准备就绪，即刻创作',10,'#9fb4ac')

out=root/'desktop/installer-assets/sidebar.bmp'
out.parent.mkdir(parents=True,exist_ok=True)
im.resize((W,H),Image.Resampling.LANCZOS).save(out)
print(out)
