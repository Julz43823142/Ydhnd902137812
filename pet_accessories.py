"""Cosmetic-only pet accessory catalog and replaceable SVG layers."""
CATALOG = {
    'crown': {'label': 'Crown', 'price': 30},
    'glasses': {'label': 'Glasses', 'price': 15},
    'bandana': {'label': 'Bandana', 'price': 15},
    'hat': {'label': 'Hat', 'price': 25},
    'necklace': {'label': 'Necklace', 'price': 20},
    'bow': {'label': 'Bow', 'price': 15},
    'star_crown': {'label': 'Starlight Crown', 'price': None},
}


def artwork(key):
    """Layers share the existing 400px portrait coordinate space."""
    return {
        'crown': '<path d="M140 90l-8-48 38 25 30-45 30 45 38-25-8 48z" fill="#ffd166" stroke="#fff2b0" stroke-width="4"/>',
        'star_crown': '<path d="M140 90l-8-48 38 25 30-45 30 45 38-25-8 48z" fill="#bcb0ff" stroke="#fff" stroke-width="4"/><path d="M200 38l6 12 14 2-10 10 2 14-12-7-12 7 2-14-10-10 14-2z" fill="#ffe69c"/>',
        'glasses': '<g fill="#17334c" fill-opacity=".65" stroke="#f8fafc" stroke-width="5"><rect x="137" y="140" width="52" height="35" rx="12"/><rect x="211" y="140" width="52" height="35" rx="12"/><path d="M189 153h22"/></g>',
        'bandana': '<path d="M132 211q68 25 136 0l-68 70z" fill="#f06577" stroke="#ffd1d7" stroke-width="4"/>',
        'hat': '<path d="M146 83V38h108v45" fill="#263f60" stroke="#6f96c8" stroke-width="4"/><rect x="146" y="63" width="108" height="18" fill="#f06577"/><ellipse cx="200" cy="88" rx="86" ry="13" fill="#263f60" stroke="#6f96c8" stroke-width="4"/>',
        'necklace': '<path d="M134 220q66 66 132 0" fill="none" stroke="#ffd166" stroke-width="8"/><circle cx="200" cy="267" r="15" fill="#55d5ce" stroke="#ffe69c" stroke-width="4"/>',
        'bow': '<path d="M200 230l-46-26v52zM200 230l46-26v52z" fill="#d488ec" stroke="#f6d5ff" stroke-width="4"/><circle cx="200" cy="230" r="11" fill="#f6d5ff"/>',
    }.get(key, '')
