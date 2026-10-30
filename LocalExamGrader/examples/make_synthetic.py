"""Create fake, rasterized 2-page exam for local, non-sensitive testing."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

folder = Path(__file__).resolve().parent
fontfile = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
try:
    f = ImageFont.truetype(fontfile, 28)
    strong = ImageFont.truetype(fontfile, 34)
except OSError:
    f = ImageFont.load_default()
    strong = f

images = []
for number in (1, 2):
    im = Image.new('RGB', (1120, 1500), (252, 252, 249))
    draw = ImageDraw.Draw(im)
    draw.text((80, 55), 'SYNTHETIC EXAM — TEST DATA ONLY', fill=(20, 24, 31), font=strong)
    draw.line((80, 125, 1040, 125), fill=(90, 90, 90), width=3)
    if number == 1:
        lines = [
            'Q1. A 12 V source is connected across a 4 ohm resistor.',
            'What is the current? Show your calculation.',
            '',
            'Student work:',
            'V = I R',
            'I = V / R',
            'I = 12 / 4 = 4 A',
            '',
            '(The result above intentionally contains an arithmetic mistake.)'
        ]
    else:
        lines = [
            'Q2. Calculate the impedance of a series R-L circuit.',
            'Assume R = 4 ohm and inductive reactance is 3 ohm.',
            '',
            'Student work:',
            'Z = R + j XL',
            'Z = 4 + j3 ohm',
            '|Z| = sqrt(4^2 + 3^2) = 5 ohm',
            'Angle = atan(3/4) = 36.9 degrees'
        ]
    y = 200
    for s in lines:
        draw.text((100, y), s, font=f, fill=(33, 40, 45))
        y += 88
    # save as rasterized document so there is no embedded PDF text
    im = im.filter(ImageFilter.GaussianBlur(0.25))
    images.append(im)
images[0].save(folder / 'synthetic_exam.pdf', 'PDF', resolution=150.0, save_all=True, append_images=images[1:])
print('Created', folder / 'synthetic_exam.pdf')
