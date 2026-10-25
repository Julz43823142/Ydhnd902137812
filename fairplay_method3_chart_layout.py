"""Method 3 SVG geometry and readable, truly landscape image composition.

v82 increased pixel width but retained portrait heights (opening chart 2980px):
Discord still showed those pages as thin vertical thumbnails. v83 *reflows*
panels into available width, without rescaling fonts, y within each panel,
data series, color coding, game counts, or numerical comparisons.
"""
import re

CHART_WIDTH = 1800
_ROOT = re.compile(
    r'^<svg xmlns="http://www.w3.org/2000/svg" width="([0-9]+)" '
    r'height="([0-9]+)" viewBox="0 0 ([0-9]+) ([0-9]+)">')
_X = re.compile(r'(\s(?:x|x1|x2|cx|width)=")(-?\d+(?:\.\d+)?)(")')
_Y = re.compile(r'(\s(?:y|y1|y2|cy)=")(-?\d+(?:\.\d+)?)(")')
_POINTS = re.compile(r'(\spoints=")([^"]+)(")')
_FIRST_TEXT = re.compile(r'(<text\b[^>]*>)([^<]+)(</text>)')
_TAG = re.compile(r'<(?:rect|line|circle|polyline|polygon|text)\b[^>]*>')


def _num(value):
    return f'{value:.3f}'.rstrip('0').rstrip('.') if value else '0'


def _point_pairs(raw):
    pairs = []
    for pair in raw.split():
        coords = pair.split(',')
        if len(coords) != 2:
            raise ValueError('Unexpected SVG point coordinate')
        pairs.append((float(coords[0]), float(coords[1])))
    return pairs


def _map_svg_elements(body, layout):
    """Reposition SVG *primitives*; do not transform their text/font sizes.

    Each element belongs to one source panel, determined by its first y
    coordinate. Segment plots never cross panel boundaries. This preserves
    chart slopes, intervals, annotations, and axis tick formatting.
    """
    def transform(match):
        tag = match[0]
        y_anchor = _Y.search(tag)
        point_match = _POINTS.search(tag)
        if y_anchor:
            anchor = float(y_anchor[2])
        elif point_match:
            pairs = _point_pairs(point_match[2])
            if not pairs:
                return tag
            anchor = pairs[0][1]
        else:
            return tag  # Whole-image white background (percentage width).
        x_factor, x_offset, y_offset = layout(anchor)
        if x_factor == 1. and x_offset == 0 and y_offset == 0:
            return tag
        tag = _X.sub(lambda attr: (
            attr[1] + _num(float(attr[2]) * x_factor + (
                0. if attr[1].lstrip().startswith('width=') else x_offset
            )) + attr[3]), tag)
        tag = _Y.sub(lambda attr: attr[1] + _num(float(attr[2]) + y_offset) + attr[3], tag)
        tag = _POINTS.sub(lambda attr: (
            attr[1] + ' '.join(
                _num(x * x_factor + x_offset) + ',' + _num(y + y_offset)
                for x, y in _point_pairs(attr[2])) + attr[3]), tag)
        return tag
    return _TAG.sub(transform, body)


def _landscape_layout(body, number, height, source):
    """Use 2-column chart rows and 3-column rating panels where readable."""
    if number == 7 and '7 · Favourite opening move patterns' in source:
        # Opening by colour: White left, Black right (20 history bars each);
        # all contextual notes and comparisons full width; Bullet / Blitz /
        # Rapid rating histories become three 600px-wide panels in one row.
        def layout(y):
            if y < 115: return 1., 0, 0
            if y < 900: return .5, 0, 0
            if y < 1680: return .5, 900, -780
            if y < 1900: return 1., 0, -790
            if y < 2000: return 1., 0, -875
            if y < 2290: return 1/3, 0, -895
            if y < 2580: return 1/3, 600, -1185
            if y < 2870: return 1/3, 1200, -1475
            return 1., 0, -1490
        return _map_svg_elements(body, layout), 1495
    if number == 9 and '9 · How does thinking time change?' in source:
        # Five time/phase panels: rows of 2, 2, 1, retaining every
        # middle-50% band, calendar tick, and missing-data gap.
        def layout(y):
            if y < 145: return 1., 0, 0
            if y < 1675:
                panel = min(4, int((y - 145) // 305))
                return .5, 900 * (panel % 2), (panel // 2) * 355 - panel * 305
            return 1., 0, -550
        return _map_svg_elements(body, layout), 1475
    if number == 4 and 'Official Chess.com Accuracy · frequency' in source:
        # Bullet/Blitz above, Rapid full width below; exact-control
        # before/during/after comparisons remain full-width underneath.
        def layout(y):
            if y < 140: return 1., 0, 0
            if y < 550: return .5, 0, 0
            if y < 970: return .5, 900, -420
            return 1., 0, -420
        return _map_svg_elements(body, layout), height - 420
    return body, height


def widen_and_number(svg, number, target_width=CHART_WIDTH):
    """Render numbered graphs at a uniform width with genuinely compact layouts."""
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
    body = _X.sub(lambda attr: attr[1] + _num(float(attr[2]) * factor) + attr[3], body)

    def widen_points(attr):
        return (attr[1] + ' '.join(
            _num(x * factor) + ',' + _num(y)
            for x, y in _point_pairs(attr[2])) + attr[3])

    body = _POINTS.sub(widen_points, body)
    if number <= 6:
        if _FIRST_TEXT.search(body) is None:
            raise ValueError('Image has no accessible title to number')
        body = _FIRST_TEXT.sub(lambda match: (
            match[1] + str(number) + ' · ' + match[2] + match[3]), body, count=1)
    else:
        top = _FIRST_TEXT.search(body)
        if not top or not top[2].startswith(str(number) + ' · '):
            raise ValueError('Context graph number does not match display order')
    body, height = _landscape_layout(body, number, height, svg)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{target_width}" '
            f'height="{height}" viewBox="0 0 {target_width} {height}">' + body)
