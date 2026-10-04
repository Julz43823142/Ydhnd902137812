"""Ownership provenance; never invent legacy owners or reveal hidden eggs."""
import time


def ensure(pet, uid, name=None, *, now=None, created=False):
    if not pet.get('owner_history'):
        pet['owner_history'] = [{'action': 'Created' if created else 'Tracking started',
                                 'owner_id': str(uid), 'owner_name': str(name or uid),
                                 'at': int(time.time() if now is None else now)}]


def append(pet, action, uid, name=None, *, now=None):
    pet.setdefault('owner_history', []).append({'action': action, 'owner_id': str(uid),
        'owner_name': str(name or uid), 'at': int(time.time() if now is None else now)})
