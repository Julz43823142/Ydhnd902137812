"""Consistent landscape-oriented Method 3 PNG layout.

All nine images use the SAME native pixel width. Scale actual x coordinates
and horizontal lengths, not font sizes or y coordinates. This increases
horizontal charting room rather than adding empty sidebars or stretching text.
Original SVG producers and statistics are untouched.
"""
import re

CHART_WIDTH = 1800
_ROOT = re.compile(
    r'^<svg xmlns="http://www.w3.org/2000/svg" width="([0-9]+)" '
    r'height="([0-9]+)" viewBox="0 0 ([0-9]+) ([0-9]+)">')
_X = re.compile(r'(\s(?:x|x1|x2|cx|width)=")(-?\d+(?:\.\d+)?)(")')
_POINTS = re.compile(r'(\spoints=")([^"]+)(")')
_FIRST_TEXT = re.compile(r'(<text\b[^>]*>)([^<]+)(</text>)')


def _num(value):
    return f'{value:.3f}'.rstrip('0').rstrip('.') if value else '0'


def widen_and_number(svg, number, target_width=CHART_WIDTH):
    """Pure layout transform; retain XML escaping and graph data as rendered.

    Every numeric horizontal SVG position / box width / polygon vertex is
    widened by the same factor. Font sizes, y positions and stroke weights
    are untouched, so labels stay readable and tall images become wider.
    """
    if not isinstance(svg, str) or type(number) is not int or not 1 <= number <= 9:
        raise ValueError('Expected an SVG and a chart number from 1 to 9')
    if type(target_width) is not int or target_width < 1440:
        raise ValueError('Target width must be at least 1440 pixels')
    root = _ROOT.match(svg)
    if root is None:
        raise ValueError('Unsupported SVG root; refusing silent image distortion')
    original, height, box_width, box_height = map(int, root.groups())
    if original != box_width or height != box_height or original <= 0:
        raise ValueError('Only unscaled source charts are supported')
    factor = target_width / original
    body = svg[root.end():]
    body = _X.sub(lambda match: match[1] + _num(float(match[2]) * factor) + match[3], body)

    def scale_points(match):
        values = []
        for pair in match[2].split():
            coords = pair.split(',')
            if len(coords) != 2:
                raise ValueError('Unexpected SVG point coordinate')
            values.append(_num(float(coords[0]) * factor) + ',' + coords[1])
        return match[1] + ' '.join(values) + match[3]

    body = _POINTS.sub(scale_points, body)
    if number <= 6:
        if _FIRST_TEXT.search(body) is None:
            raise ValueError('Image has no accessible title to number')
        body = _FIRST_TEXT.sub(lambda match: (
            match[1] + str(number) + ' · ' + match[2] + match[3]), body, count=1)
    else:
        # Context graphs already carry their numbers in their first title.
        top = _FIRST_TEXT.search(body)
        if not top or not top[2].startswith(str(number) + ' · '):
            raise ValueError('Context graph number does not match display order')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{target_width}" '
            f'height="{height}" viewBox="0 0 {target_width} {height}">' + body)
