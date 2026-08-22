#!/usr/bin/env python3
"""Generate zigtester concept diagram using PIL."""

from PIL import Image, ImageDraw, ImageFont
import io

def create_diagram():
    # Create image
    width, height = 1400, 1000
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    
    # Colors
    COLOR_PRIMARY = '#1a73e8'
    COLOR_SUCCESS = '#34a853'
    COLOR_WARNING = '#fbbc04'
    COLOR_ERROR = '#ea4335'
    COLOR_BG = '#f8f9fa'
    COLOR_TEXT = '#202124'
    
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_subtitle = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        font_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_text = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    except:
        font_title = ImageFont.load_default()
        font_subtitle = ImageFont.load_default()
        font_label = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Title
    draw.text((width//2, 60), "zigtester", fill=COLOR_PRIMARY, font=font_title, anchor="mm")
    draw.text((width//2, 110), "Unified Test Framework for fixnet Ecosystem", 
              fill=COLOR_TEXT, font=font_subtitle, anchor="mm")
    
    # Horizontal divider
    draw.line([(50, 140), (width-50, 140)], fill='#e0e0e0', width=2)
    
    # --- LEFT SECTION: PROJECTS ---
    draw.text((120, 180), "Projects & Tests", fill=COLOR_TEXT, font=font_label, anchor="lm")
    
    projects = [
        ("zigfoundation", 50, 220),
        ("zigoutbounds", 50, 290),
        ("zigbox", 250, 220),
        ("zigtun", 250, 290),
    ]
    
    for name, x, y in projects:
        # Draw rounded box
        draw.rectangle([x, y-20, x+180, y+20], outline=COLOR_TEXT, fill=COLOR_BG, width=2)
        draw.text((x+90, y), name, fill=COLOR_TEXT, font=font_label, anchor="mm")
    
    # --- CENTER: CORE ---
    core_x, core_y = width // 2, 280
    draw.rectangle([core_x-120, core_y-50, core_x+120, core_y+50], 
                   outline=COLOR_SUCCESS, fill=COLOR_SUCCESS, width=3)
    draw.text((core_x, core_y-15), "zigtester", fill='white', font=font_label, anchor="mm")
    draw.text((core_x, core_y+15), "Core Runner", fill='white', font=font_text, anchor="mm")
    
    # --- RIGHT SECTION: LEVELS ---
    levels = [
        ("unit", 950, 220),
        ("functional", 1100, 220),
        ("performance", 950, 290),
    ]
    
    for name, x, y in levels:
        draw.rectangle([x-60, y-20, x+60, y+20], outline=COLOR_WARNING, fill=COLOR_WARNING, width=2)
        draw.text((x, y), name, fill='white', font=font_label, anchor="mm")
    
    # --- ARROWS ---
    # Projects to core
    draw.line([(230, 235), (core_x-120, 280)], fill=COLOR_TEXT, width=2)
    draw.line([(230, 305), (core_x-120, 280)], fill=COLOR_TEXT, width=2)
    
    # Core to levels
    draw.line([(core_x+120, 280), (890, 235)], fill=COLOR_SUCCESS, width=2)
    draw.line([(core_x+120, 280), (890, 305)], fill=COLOR_SUCCESS, width=2)
    
    # --- FEATURES ---
    draw.text((100, 400), "Key Features", fill=COLOR_TEXT, font=font_label, anchor="lm")
    
    features = [
        ("Resource Monitoring\nCPU/Memory/FD", 100, 460),
        ("Plugin Support\nEcho/Sing-box", 350, 460),
        ("Regression Detection\nHistorical Baselines", 600, 460),
        ("MCP Integration\nAI-Native", 850, 460),
    ]
    
    for text, x, y in features:
        draw.rectangle([x-90, y-35, x+90, y+35], outline=COLOR_WARNING, fill=COLOR_BG, width=2)
        for i, line in enumerate(text.split('\n')):
            draw.text((x, y-15+i*20), line, fill=COLOR_TEXT, font=font_small, anchor="mm")
    
    # --- OUTPUT FORMATS ---
    draw.text((100, 580), "Output Formats", fill=COLOR_TEXT, font=font_label, anchor="lm")
    
    outputs = [
        ("Terminal\nANSI", 200, 640),
        ("Markdown\nAI Agents", 500, 640),
        ("JSON\nCI/Pipelines", 800, 640),
    ]
    
    for text, x, y in outputs:
        draw.rectangle([x-90, y-35, x+90, y+35], outline=COLOR_ERROR, fill=COLOR_ERROR, width=2)
        for i, line in enumerate(text.split('\n')):
            draw.text((x, y-15+i*20), line, fill='white', font=font_text, anchor="mm")
    
    # --- VALUE PROPOSITION ---
    draw.text((width//2, 750), "Value Proposition", fill=COLOR_TEXT, font=font_label, anchor="mm")
    
    props = [
        "60-85% Token\nReduction",
        "One Config\nZero Fragmentation",
        "Improved Tool-Use\nAccuracy",
    ]
    
    for i, text in enumerate(props):
        x = 300 + i * 350
        draw.rectangle([x-100, 800, x+100, 900], outline=COLOR_PRIMARY, fill='#e8f0fe', width=2)
        for j, line in enumerate(text.split('\n')):
            draw.text((x, 830+j*30), line, fill=COLOR_PRIMARY, font=font_text, anchor="mm")
    
    # Tech stack
    draw.text((width//2, 960), 
              "Python 3.10+ • PyYAML • FastMCP • SQLite • UUID Project Identity",
              fill=COLOR_TEXT, font=font_small, anchor="mm")
    
    return img

if __name__ == '__main__':
    img = create_diagram()
    img.save('zigtester-concept.png')
    print("✓ Generated: zigtester-concept.png (1400x1000px)")
