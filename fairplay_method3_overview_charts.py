"""Report page 7: published Accuracy frequency above matched-control thinking-time context.

Only presentation is combined. The two existing descriptive methods stay
separate, and neither changes Method 3's detector or its priority gates.
"""
from fairplay_charts import start
from fairplay_method3_frequency_charts import accuracy_frequency_svg
from fairplay_method3_chart_layout import _map_svg_elements


def _body(svg):
    if not svg.startswith('<svg ') or not svg.endswith('</svg>'):
        raise ValueError('Expected a complete source SVG')
    content = svg.split('>', 1)[1].rsplit('</svg>', 1)[0]
    # Each source SVG has a full-page white background; retaining the second
    # would cover the top panel when stacked.
    background = '<rect width="100%" height="100%" fill="white"/>'
    if not content.startswith(background):
        raise ValueError('Unexpected source SVG background')
    return content[len(background):]


def frequency_timing_svg(result, timing_source):
    """Stack frequency (600px) and unchanged timing observations (1040px)."""
    upper = _body(accuracy_frequency_svg(result)).replace(
        'Official Chess.com Accuracy · frequency',
        '7 · Official Chess.com Accuracy frequency + thinking-time comparison',
        1)
    lower = _body(timing_source).replace(
        '8 · Do thinking times move together?',
        'Thinking times across classes and time controls',
        1)
    lower = _map_svg_elements(lower, lambda y: (1.0, 0, 620))
    parts = start(1200, 1660)
    parts.extend((upper, lower, '</svg>'))
    return ''.join(parts)
