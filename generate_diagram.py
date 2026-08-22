#!/usr/bin/env python3
"""Generate concept diagram for zigtester."""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

# Set style
plt.style.use('default')
fig, ax = plt.subplots(1, 1, figsize=(14, 10))
ax.set_xlim(0, 14)
ax.set_ylim(0, 10)
ax.axis('off')

# Color scheme
color_header = '#1a73e8'
color_core = '#34a853'
color_features = '#fbbc04'
color_output = '#ea4335'
color_bg_light = '#f8f9fa'
color_text = '#202124'

# Title
ax.text(7, 9.5, 'zigtester', fontsize=32, weight='bold', ha='center', color=color_header)
ax.text(7, 8.9, 'Unified Test Framework for fixnet Ecosystem', fontsize=13, ha='center', 
        color=color_text, style='italic')

# Horizontal divider
ax.plot([0.5, 13.5], [8.5, 8.5], 'k-', linewidth=0.8, alpha=0.2)

# Left section: Projects/Tests Input
input_y = 7
ax.text(1.2, 7.8, 'Projects & Tests', fontsize=11, weight='bold', color=color_text)

projects = [
    ('zigfoundation', 0.5, 6.8),
    ('zigoutbounds', 0.5, 6.2),
    ('zigbox', 2.5, 6.8),
    ('zigtun', 2.5, 6.2),
]

for proj_name, x, y in projects:
    box = FancyBboxPatch((x, y-0.3), 1.8, 0.5, boxstyle="round,pad=0.05", 
                         edgecolor=color_text, facecolor=color_bg_light, linewidth=1.5)
    ax.add_patch(box)
    ax.text(x+0.9, y, proj_name, fontsize=10, ha='center', va='center', weight='bold')

# Center section: zigtester Core
core_x = 6.5
core_y = 6.8
core_width = 2.5
core_height = 1.2

core_box = FancyBboxPatch((core_x-core_width/2, core_y-core_height/2), core_width, core_height,
                          boxstyle="round,pad=0.1", edgecolor=color_core, facecolor=color_core, 
                          linewidth=2.5, alpha=0.85)
ax.add_patch(core_box)
ax.text(core_x, core_y+0.2, 'zigtester', fontsize=12, ha='center', va='center', 
        weight='bold', color='white')
ax.text(core_x, core_y-0.35, 'Core Runner', fontsize=10, ha='center', va='center', color='white')

# Arrows from projects to core
for proj_name, x, y in projects:
    arrow = FancyArrowPatch((x+1.8, y+0.1), (core_x-core_width/2-0.1, core_y),
                           arrowstyle='->', mutation_scale=20, linewidth=1.5, 
                           color=color_text, alpha=0.5)
    ax.add_artist(arrow)

# Right section: Three Levels
levels = [
    ('unit', 9.5, 7.3),
    ('functional', 11, 7.3),
    ('performance', 9.5, 5.9),
]

for level_name, x, y in levels:
    box = FancyBboxPatch((x-0.6, y-0.35), 1.2, 0.6, boxstyle="round,pad=0.05",
                        edgecolor=color_features, facecolor=color_features, 
                        linewidth=1.5, alpha=0.8)
    ax.add_patch(box)
    ax.text(x, y, level_name, fontsize=10, ha='center', va='center', 
           weight='bold', color='white')

# Arrow from core to levels
arrow = FancyArrowPatch((core_x+core_width/2+0.1, core_y), (9.5-0.7, 7.3),
                       arrowstyle='->', mutation_scale=20, linewidth=2, color=color_core)
ax.add_artist(arrow)
ax.text(8, 7.1, 'Four-Level\nTest Model', fontsize=9, ha='center', color=color_core, weight='bold')

# Bottom section: Key Features
feature_y = 4.8
ax.text(1, 5.2, 'Key Features', fontsize=11, weight='bold', color=color_text)

features = [
    ('Resource Monitoring\nCPU/Memory/FD', 0.3, 4.7),
    ('Plugin Support\nEcho/Sing-box/XRay', 2.2, 4.7),
    ('Regression Detection\nHistorical Baselines', 4.1, 4.7),
    ('MCP Integration\nAI-Native', 6, 4.7),
]

for feature_text, x, y in features:
    box = FancyBboxPatch((x-0.7, y-0.35), 1.4, 0.7, boxstyle="round,pad=0.05",
                        edgecolor=color_features, facecolor=color_bg_light, 
                        linewidth=1.5, alpha=0.9)
    ax.add_patch(box)
    ax.text(x, y, feature_text, fontsize=8.5, ha='center', va='center', color=color_text)

# Arrow from levels to features
arrow = FancyArrowPatch((10.25, 5.9-0.4), (3.5, 4.7+0.4),
                       arrowstyle='->', mutation_scale=18, linewidth=1.5, 
                       color=color_text, alpha=0.4, linestyle='dashed')
ax.add_artist(arrow)

# Output formats section
output_y = 3.3
ax.text(1, 3.8, 'Output Formats', fontsize=11, weight='bold', color=color_text)

outputs = [
    ('Terminal\nANSI Colors', 1.5, 3.2),
    ('Markdown\nAI Agents', 4, 3.2),
    ('JSON\nCI/Pipelines', 6.5, 3.2),
]

for output_text, x, y in outputs:
    box = FancyBboxPatch((x-0.65, y-0.35), 1.3, 0.7, boxstyle="round,pad=0.05",
                        edgecolor=color_output, facecolor=color_output, 
                        linewidth=1.5, alpha=0.8)
    ax.add_patch(box)
    ax.text(x, y, output_text, fontsize=9, ha='center', va='center', 
           weight='bold', color='white')

# Bottom value proposition
prop_y = 1.5
ax.text(7, 2.3, 'Value Proposition', fontsize=11, weight='bold', ha='center', color=color_text)

props = [
    '60-85% Token\nReduction',
    'One Config\nZero Fragmentation',
    'Improved Tool-Use\nAccuracy',
]

for i, prop_text in enumerate(props):
    x = 2.5 + i * 3
    box = FancyBboxPatch((x-0.75, prop_y-0.4), 1.5, 0.8, boxstyle="round,pad=0.08",
                        edgecolor=color_header, facecolor='#e8f0fe', 
                        linewidth=2, alpha=0.9)
    ax.add_patch(box)
    ax.text(x, prop_y, prop_text, fontsize=9, ha='center', va='center', 
           weight='bold', color=color_header)

# Tech stack at bottom
tech_y = 0.4
ax.text(7, 0.9, 'Python 3.10+ • PyYAML • FastMCP • SQLite • UUID Project Identity', 
        fontsize=8, ha='center', color=color_text, style='italic', alpha=0.7)

plt.tight_layout()
plt.savefig('zigtester-concept.png', dpi=300, bbox_inches='tight', facecolor='white', edgecolor='none')
print("✓ Generated: zigtester-concept.png")
