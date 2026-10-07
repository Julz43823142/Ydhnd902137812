"""Monotonic overall work progress; NOT time remaining or review probability."""
import re


def estimate(stage):
    if stage == 'Complete':
        return 100
    if stage.startswith('Fast engine scan:'):
        match = re.search(r'(\d+)\s*/\s*(\d+)', stage)
        return 15+int(60*min(1, int(match[1])/max(1, int(match[2])))) if match else 15
    if stage.startswith('Deep confirmation:'):
        match = re.search(r'(\d+)\s*/\s*(\d+)', stage)
        return 80+int(17*min(1, int(match[1])/max(1, int(match[2])))) if match else 80
    return {'Fetching profile…':3, 'Collecting rated games…':10,
            'Building human-move profile…':77, 'Analyzing sessions and repertoire…':79,
            'Comparing personal timing baselines…':98, 'Building report…':99}.get(stage, 0)


def bar(value):
    value = max(0, min(100, int(value)))
    filled = value//5
    return f'{"▰"*filled}{"▱"*(20-filled)} **{value}%**'


def label(stage):
    if stage.startswith('Fast engine scan:'):
        return 'Screening gameplay with Stockfish…'
    if stage.startswith('Deep confirmation:'):
        return 'Deep-confirming selected games…'
    return stage
