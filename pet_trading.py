"""Pet assets for the existing direct/open trade ledger; no separate market."""
import copy
import json
import time

import pets


def label(pet):
    if pets.level(pet) == 0:
        return 'Mysterious Egg · Level 0'
    name = pet.get('name') or pet['species']
    return f"{name} · {pet['species']} · {pet['rarity'].title()} · Level {pets.level(pet)}"


def asset_from_text(text, uid=None):
    """Resolve `pet:<id>` or a unique `pet <name/species>` owned by uid."""
    text = str(text or '').strip()
    if text.casefold().startswith('pet:'):
        query = text[4:].strip()
    elif text.casefold().startswith('pet '):
        query = text[4:].strip()
    else:
        return None
    if not query:
        raise ValueError('Add a pet ID or name after pet:. Find IDs in the Pet Card.')
    if uid is None:
        # An open request cannot choose a buyer's inventory before acceptance.
        if not text.casefold().startswith('pet:') or not query.isalnum() or len(query) > 64:
            raise ValueError('Use pet:<exact pet ID> for an open trade request.')
        return {'type': 'pet', 'pet_id': query}
    owner = pets.get_owner(uid)
    candidates = [p for p in owner['pets'] if not p.get('died_at')]
    exact = [p for p in candidates if p['id'] == query]
    matches = exact or [p for p in candidates if query.casefold() in {
        str(p.get('name') or '').casefold(),
        (p['species'] if pets.level(p) else 'Mysterious Egg').casefold(),
    }]
    if not matches:
        raise ValueError('You do not own that living pet. Use its exact Pet Card ID.')
    if len(matches) != 1:
        raise ValueError('More than one pet matches. Use its exact Pet Card ID.')
    pet = matches[0]
    ensure_tradable(owner, pet['id'])
    return {'type': 'pet', 'pet_id': pet['id'], 'label': label(pet)}


def ensure_tradable(owner, pet_id):
    pet = pets.current_pet(owner, pet_id)
    if pet is None or pet.get('died_at'):
        raise ValueError('That pet is no longer alive or owned by this player.')
    expedition = owner.get('expedition', {})
    if expedition.get('status') == 'running' and expedition.get('pet_id') == pet_id:
        raise ValueError('Claim this pet\'s expedition before trading it.')
    return pet


def available(data, uid, asset):
    owner = pets._owner(data, uid, time.time())
    try:
        ensure_tradable(owner, asset['pet_id'])
        return True
    except ValueError:
        return False


def settle(data, first_uid, second_uid, offer, request):
    """Validate the final capacities before moving either pet or any payment."""
    now = time.time()
    owners = {str(uid): pets._owner(data, uid, now) for uid in (first_uid, second_uid)}
    outgoing = {}
    for uid, asset in ((str(first_uid), offer), (str(second_uid), request)):
        if asset['type'] == 'pet':
            outgoing[uid] = ensure_tradable(owners[uid], asset['pet_id'])
    if len(outgoing) == 2 and offer['pet_id'] == request['pet_id']:
        raise ValueError('The same pet cannot appear on both sides of a trade.')
    for uid, other in ((str(first_uid), str(second_uid)), (str(second_uid), str(first_uid))):
        count = sum(not p.get('died_at') for p in owners[uid]['pets'])
        final = count - int(uid in outgoing) + int(other in outgoing)
        if other in outgoing and final > pets.MAX_LIVING:
            raise ValueError(f'Trade would exceed the limit of {pets.MAX_LIVING} living pets for player {uid}.')
        if other in outgoing and any(p['id'] == outgoing[other]['id'] for p in owners[uid]['pets']):
            raise ValueError('This pet ID already exists in the receiving collection.')
    # Remove both sides first: a full collection may swap a pet for another pet.
    for uid, pet in outgoing.items():
        owners[uid]['pets'].remove(pet)
    for uid, other in ((str(first_uid), str(second_uid)), (str(second_uid), str(first_uid))):
        if uid in outgoing:
            pet = copy.deepcopy(outgoing[uid])
            # Accessory unlocks belong to their owner and are not part of a pet trade.
            pet.pop('accessory', None)
            owners[other]['pets'].append(pet)
    for owner in owners.values():
        pets.expire(owner, now)
    return {pets.FILE: json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n'}
