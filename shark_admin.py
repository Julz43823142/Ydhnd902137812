"""ID-only admin corrections. All mutations validate the actor again here.

Shared/puzzle/Guess corrections use immutable audit events and optimistic Git
commits; Daily-owned records are edited by the Daily process in memory.
"""
import asyncio
import copy
import hashlib
import json
import math
import re
import time
from pathlib import Path

import discord
import shared_leaderboard as shared
import guess_leaderboard as guess
import puzzle_stats as puzzles
from shop_catalog import BOARD_THEMES, PIECE_SETS, ARROW_COLORS, NAME_COLORS, canonical_piece_set

ADMIN_ID = 362606514764251137
ADMIN_LIST = """🛠️ **Admin Commands**
**Points & coins — exact totals**
`!edit <name> <points>` — Shared / Puzzle points.
`!editcoins <name> <coins>` — Shared coins.
`!editguesspoints <name> <points>` — Guess points.

**Leaderboard / streak corrections — exact totals**
`!editchesselo <name> <elo>` — Chess Elo.
`!editpuzzleelo <name> <elo>` — Puzzle Elo.
`!editstreak <name> <best>` — Best Puzzle streak (legacy alias).
`!editpuzzlestreak <name> <best>` — Best Puzzle streak.
`!editpuzzlecurrentstreak <name> <streak>` — Current Puzzle streak.
`!editguessstreak <name> <streak>` — Current Guess streak.
`!editguesscurrentstreak <name> <streak>` — Current Guess streak.
`!editguessbeststreak <name> <best>` — Best Guess streak.
`!editguesstotal <name> <total>` — Total Guess answers.
`!editguesscorrect <name> <correct>` — Correct Guess answers.
`!editguesswrong <name> <wrong>` — Wrong Guess answers.
`!editrushweekly <name> <score>` — Current week's Rush best.
`!editrushalltime <name> <score>` — Highest stored all-time Rush run.

**Advanced stats**
`!editstat <name> <puzzle|guess|chess> <field> <value>`
`!adminfields <puzzle|guess|chess>` — Show every editable field.

**Ownership — give / remove**
`!givebadge` / `!removebadge` `<name> <badge> [amount]`
`!giveboard` / `!removeboard` `<name> <board>`
`!givepieces` / `!removepieces` `<name> <pieces>`
`!givearrow` / `!removearrow` `<name> <arrow>`
`!givecolor` / `!removecolor` `<name> <color>`
`!giveachievement` / `!removeachievement` `<name> <achievement>`
`!adminitems <type>` — Available item names.
`!admininventory <name>` — View owned items.

**Existing controls — Puzzle / Survival / Minigames**
`!editcolor <name> <color|default>` — Active name color.
`!addheart <team>` — Restore one heart; can revive dead runs.
`!delete <team>` — Delete team and saved runs.

All values above are SET to the exact number you enter, not added. Shared, Puzzle and Guess corrections can be used in their normal bot channels. Chess and Rush corrections must be handled by the Puzzle controller. Names may contain spaces; use a mention if ambiguous. Grants do not equip items. Corrections do not mint coins."""

SCALAR_COMMANDS = {
    '!edit':'points', '!editpoints':'points', '!editpuzzlepoints':'points',
    '!editcoins':'coins', '!editguesspoints':'guess_points',
    '!editchesselo':'chess_elo', '!editpuzzleelo':'puzzle_elo',
    '!editstreak':'puzzle_best_streak',
    '!editpuzzlestreak':'puzzle_best_streak',
    '!editpuzzlebeststreak':'puzzle_best_streak',
    '!editpuzzlecurrentstreak':'puzzle_current_streak',
    '!editguessstreak':'guess_current_streak',
    '!editguesscurrentstreak':'guess_current_streak',
    '!editguessbeststreak':'guess_best_streak',
    '!editguesstotal':'guess_total',
    '!editguesscorrect':'guess_correct',
    '!editguesswrong':'guess_wrong',
    '!editrushweekly':'rush_weekly',
    '!editrushalltime':'rush_alltime',
}
OWNERSHIP = {'badge':'badges','board':'boards','pieces':'pieces','arrow':'arrows','color':'colors','achievement':'achievements'}
OWN_COMMANDS = {f'!{action}{kind}':(action,kind) for action in ('give','remove') for kind in OWNERSHIP}
COMMANDS = set(SCALAR_COMMANDS) | set(OWN_COMMANDS) | {'!editstat','!adminfields','!adminitems','!admininventory'}
GUESS_FIELDS = {'total','correct','wrong','current_streak','best_streak'}
CHESS_FIELDS = {'elo','peak_elo','games','wins','draws','losses'}
PUZZLE_FIELDS = set(puzzles._default_user()) - {'name','achievements','updated_at','admin_rated'}


def require_admin(actor_id):
    if str(actor_id) != str(ADMIN_ID):
        raise PermissionError('Only Sharkmeister can use admin commands.')


def number(value, *, integer=False, elo=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('Enter a valid number.') from None
    if not math.isfinite(value) or value < 0 or value > 1_000_000_000:
        raise ValueError('Use a finite number between 0 and 1,000,000,000.')
    if integer and not value.is_integer():
        raise ValueError('This value must be a whole number.')
    if elo and not 100 <= value <= 4000:
        raise ValueError('Elo must be between 100 and 4000.')
    return int(value) if integer else round(value, 3)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def _read(path, default):
    text = shared._origin_file(path)
    if text is None:
        return copy.deepcopy(default)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f'Invalid state file: {path}')
    return value


def _transaction(actor_id, tx, operation, mutate):
    require_admin(actor_id)
    if not tx:
        raise ValueError('Missing transaction identifier.')
    audit_path = 'admin_events/' + hashlib.sha256(str(tx).encode()).hexdigest() + '.json'
    # Match Guess's existing lock order: Guess first, shared repository second.
    with guess._LOCK, shared.REPOSITORY_LOCK:
        for attempt in range(8):
            if not shared._fetch_retry():
                continue
            base = shared._run(['git','rev-parse',shared._origin_ref()])
            if base.returncode:
                continue
            existing = shared._origin_file(audit_path)
            if existing is not None:
                return json.loads(existing)['result']
            files, result = mutate()
            files[audit_path] = _json({'transaction_id':str(tx),'actor_id':str(actor_id),
                'operation':operation,'result':result,'created_at':time.time()})
            commit = shared._commit_snapshot(base.stdout.strip(), files, 'Apply admin correction')
            push = shared._run(['git','push','origin',f'{commit}:refs/heads/{shared._branch()}'])
            if push.returncode == 0:
                # Never reset the worktree. The next regular mutation reads origin.
                for filename, content in files.items():
                    try:
                        path=Path(filename); path.parent.mkdir(parents=True,exist_ok=True)
                        temporary=path.with_suffix(path.suffix+'.admin.tmp')
                        temporary.write_text(content,encoding='utf-8'); temporary.replace(path)
                    except OSError as error:
                        print(f'Admin correction saved remotely; local mirror failed: {type(error).__name__}',flush=True)
                if shared.LEGACY_FILE in files:
                    shared._CACHE_SNAPSHOT = json.loads(files[shared.LEGACY_FILE])
                return result
            time.sleep(min(.5, .1*(attempt+1)))
    raise RuntimeError('Could not save the admin correction to GitHub. Retry the command.')


def _shared_files(snapshot, migrated):
    files={shared.LEGACY_FILE:shared._snapshot_json(snapshot)}
    if not migrated:
        files[shared._event_filename(shared.MIGRATION_TRANSACTION_ID)] = shared._event_json(shared._migration_event())
    return files


def canonical_item(kind, item):
    key=item.casefold()
    if kind=='badge':
        try:
            return shared.resolve_badge(item)
        except ValueError:
            # An admin may also grant a custom Discord emoji or a Unicode badge.
            custom = re.fullmatch(r'<a?:[A-Za-z0-9_]+:[0-9]+>',item)
            unicode_emoji = len(item)<=32 and any(ord(c)>=0x2300 for c in item) and not any(c.isspace() or c in '<>"`' for c in item)
            if custom or unicode_emoji:
                return item
            raise ValueError('Use a badge from the catalogue or an emoji.') from None
    if kind=='pieces': key=canonical_piece_set(key)
    catalogs={'board':BOARD_THEMES,'pieces':PIECE_SETS,'arrow':ARROW_COLORS,'color':NAME_COLORS,'achievement':puzzles.ACHIEVEMENT_BY_ID}
    if kind not in catalogs or key not in catalogs[kind]:
        raise ValueError('Unknown item. Use !adminitems '+kind+'.')
    if (kind in {'board','pieces'} and key=='classic') or (kind=='arrow' and key=='green'):
        raise ValueError('The free default item is always owned and cannot be granted or removed.')
    return key


def set_shared(actor_id, uid, name, field, value, tx):
    require_admin(actor_id)
    if field not in {'points','coins'}:raise ValueError('Unsupported wallet field.')
    value=number(value)
    def mutate():
        snapshot,migrated=shared._origin_state()
        entry=shared._normalize_entry(snapshot.get(str(uid),{'name':name}))
        before=entry[field];entry[field]=value;entry['name']=name;snapshot[str(uid)]=entry
        return _shared_files(snapshot,migrated),{'name':name,'field':field,'before':before,'value':value}
    return _transaction(actor_id,tx,'set-'+field,mutate)


def change_inventory(actor_id, uid, name, action, kind, item, count, tx):
    require_admin(actor_id)
    if action not in {'give','remove'}:raise ValueError('Unknown inventory action.')
    item=canonical_item(kind,item)
    count=number(count,integer=True)
    if not 1<=count<=1000:raise ValueError('Amount must be between 1 and 1000.')
    if kind!='badge' and count!=1:raise ValueError('Only badges can have multiple copies.')
    if kind=='achievement':
        def achievement_mutation():
            snapshot=puzzles._normalize_snapshot(_read(puzzles.STATS_FILE,puzzles._empty_snapshot()))
            entry=puzzles._normalize_user(snapshot['users'].get(str(uid),puzzles._default_user(name)))
            owned=entry['achievements']
            if action=='give':
                if item not in owned:owned.append(item)
            elif item in owned:owned.remove(item)
            else:raise ValueError('That player does not own this achievement.')
            entry['name']=name;snapshot['users'][str(uid)]=entry
            return {puzzles.STATS_FILE:_json(snapshot)},{'name':name,'action':action,'kind':kind,'item':item,'amount':1}
        return _transaction(actor_id,tx,action+'-'+kind,achievement_mutation)
    def mutate():
        snapshot,migrated=shared._origin_state()
        entry=shared._normalize_entry(snapshot.get(str(uid),{'name':name}));entry['name']=name
        owned=entry[OWNERSHIP[kind]]
        if action=='give':
            if kind=='badge':owned.extend([item]*count)
            elif item not in owned:owned.append(item)
        else:
            if owned.count(item)<count:raise ValueError('That player does not own enough copies of this item.')
            for _ in range(count):owned.remove(item)
        active_key={'badge':'active_badge','board':'active_board','pieces':'active_piece','arrow':'active_arrow','color':'active_color'}[kind]
        old_active=entry[active_key]
        entry=shared._normalize_entry(entry);snapshot[str(uid)]=entry
        return _shared_files(snapshot,migrated),{'name':name,'action':action,'kind':kind,'item':item,'amount':count,
            'active_reset':old_active!=entry[active_key]}
    return _transaction(actor_id,tx,action+'-'+kind,mutate)


def set_guess_points(actor_id,uid,name,value,tx):
    require_admin(actor_id);value=number(value)
    def mutate():
        events=guess._origin_events();legacy=guess._origin_legacy_scores();files={}
        if not events:
            for old_uid,entry in legacy.items():
                points=float(entry.get('points',0))
                baseline_tx=f'baseline:{old_uid}:{points:.3f}'
                event=guess._event_payload(baseline_tx,old_uid,entry.get('name','Unknown'),points,'legacy-baseline')
                events[baseline_tx]=event;files[guess._event_filename(baseline_tx)]=_json(event)
        before=float(guess._snapshot(events,legacy).get(str(uid),{}).get('points',0))
        event=guess._event_payload(tx,uid,name,round(value-before,3),'admin-guess-correction')
        event['before']=before;event['after']=value
        files[guess._event_filename(tx)]=_json(event)
        return files,{'name':name,'field':'guess_points','before':before,'value':value}
    return _transaction(actor_id,tx,'set-guess-points',mutate)


def _set_stat_entry(entry,field,value):
    entry[field]=value
    if field=='wrong':entry['total']=entry.get('correct',0)+value
    elif field=='correct':entry['total']=value+entry.get('wrong',0)
    elif field=='total':entry['correct']=min(entry.get('correct',0),value)
    if field in {'wrong','correct','total'}:entry['wrong']=entry['total']-entry.get('correct',0)
    for current,best in [('current_streak','best_streak'),('current_wrong_streak','best_wrong_streak'),('current_hard_streak_2600','best_hard_streak_2600')]:
        if field==best:entry[current]=min(entry.get(current,0),value)
        if field==current:entry[best]=max(entry.get(best,0),value)
    if field=='elo':entry['peak_elo']=max(entry.get('peak_elo',value),value)
    if field=='peak_elo' and value<entry.get('elo',0):raise ValueError('Peak Elo cannot be below current Elo.')


def set_stat(actor_id,uid,name,domain,field,value,tx):
    require_admin(actor_id)
    fields=PUZZLE_FIELDS if domain=='puzzle' else GUESS_FIELDS if domain=='guess' else set()
    if field not in fields:raise ValueError('Unknown field. Use !adminfields '+domain+'.')
    value=number(value,integer=field not in {'elo','peak_elo'},elo=field in {'elo','peak_elo'})
    def mutate():
        if domain=='puzzle':
            snapshot=puzzles._normalize_snapshot(_read(puzzles.STATS_FILE,puzzles._empty_snapshot()))
            entry=puzzles._normalize_user(snapshot['users'].get(str(uid),puzzles._default_user(name)))
            path=puzzles.STATS_FILE
        else:
            snapshot=guess._normalize_stats_snapshot(_read(guess.STATS_FILE,guess._empty_stats_snapshot()))
            entry=snapshot['users'].get(str(uid),{'name':name,'total':0,'correct':0,'wrong':0,'best_streak':0,'current_streak':0,'targets':{'chatter':{},'chess':{}}})
            path=guess.STATS_FILE
        before=entry.get(field,0);_set_stat_entry(entry,field,value)
        entry['name']=name;entry['updated_at']=int(time.time())
        if domain=='puzzle' and field=='elo':entry['admin_rated']=True
        snapshot['users'][str(uid)]=entry
        return {path:_json(snapshot)},{'name':name,'field':domain+'.'+field,'before':before,'value':value}
    return _transaction(actor_id,tx,'set-stat',mutate)


async def resolve_target(message,text):
    text=text.strip().strip('"')
    if text.casefold() in {'me','myself'}:return str(message.author.id),message.author.display_name
    match=re.fullmatch(r'<@!?(\d+)>|([0-9]{15,22})',text)
    if match:
        uid=int(match.group(1) or match.group(2))
        member=message.guild.get_member(uid)
        if member is None:member=await message.guild.fetch_member(uid)
        return str(uid),member.display_name
    matches={str(m.id):m.display_name for m in message.guild.members if text.casefold() in {m.name.casefold(),m.display_name.casefold()}}
    snapshot=await asyncio.to_thread(shared._current_snapshot)
    for uid,entry in snapshot.items():
        if str(entry.get('name','')).casefold()==text.casefold():matches.setdefault(str(uid),entry['name'])
    if len(matches)!=1:
        raise ValueError('Player not found or name is ambiguous. Use a Discord mention to select the player.')
    return next(iter(matches.items()))


def _owned_text(profile):
    from collections import Counter
    lines=['🎒 **Owned items — '+discord.utils.escape_markdown(profile.get('name','Player'))+'**']
    for kind in ('badges','boards','pieces','arrows','colors'):
        text=', '.join(f'{k} ×{v}' if v>1 else str(k) for k,v in Counter(profile.get(kind,[])).items()) or 'None'
        lines.append(f'**{kind.title()}:** {text}')
    lines.append('Classic board/pieces and the green arrow are always available.')
    return '\n'.join(lines)


async def send_text(message,text):
    # Split long inventories/catalogues without mentions or oversized messages.
    for start in range(0,len(text),1900):
        await message.channel.send(text[start:start+1900],allowed_mentions=discord.AllowedMentions.none())


async def handle_message(message, *, daily_editor=None, color_editor=None):
    parts=message.content.strip().split()
    if not parts or parts[0].casefold() not in COMMANDS:return False
    command=parts[0].casefold()
    if message.author.id!=ADMIN_ID:
        await send_text(message,'🔒 Only Sharkmeister can use this command.')
        return True
    try:
        if command=='!adminfields':
            if len(parts)!=2 or parts[1].casefold() not in {'puzzle','guess','chess'}:raise ValueError('Usage: !adminfields <puzzle|guess|chess>')
            fields={'puzzle':PUZZLE_FIELDS,'guess':GUESS_FIELDS,'chess':CHESS_FIELDS}[parts[1].casefold()]
            await send_text(message,'**Editable fields:**\n'+', '.join('`'+x+'`' for x in sorted(fields)))
            return True
        if command=='!adminitems':
            if len(parts)!=2:raise ValueError('Usage: !adminitems <badge|board|pieces|arrow|color|achievement>')
            kind=parts[1].casefold()
            items={'badge':shared._ALL_BADGES,'board':BOARD_THEMES,'pieces':PIECE_SETS,'arrow':ARROW_COLORS,'color':NAME_COLORS,'achievement':puzzles.ACHIEVEMENT_BY_ID}.get(kind)
            if items is None:raise ValueError('Unknown item type.')
            await send_text(message,'**'+kind.title()+' catalogue:**\n'+', '.join(str(x) for x in items))
            return True
        if command=='!admininventory':
            if len(parts)<2:raise ValueError('Usage: !admininventory <name>')
            uid,name=await resolve_target(message,' '.join(parts[1:]))
            profile=await asyncio.to_thread(shared.get_cosmetic_profile,uid,name)
            await send_text(message,_owned_text(profile));return True
        tx=f'admin-command:{message.id}'
        if command in OWN_COMMANDS:
            action,kind=OWN_COMMANDS[command];count=1
            if len(parts)<3:raise ValueError('Usage: '+command+' <name> <item>')
            if kind=='badge' and len(parts)>=4 and re.fullmatch(r'\d+',parts[-1]):count=number(parts.pop(),integer=True)
            item=parts[-1];target=' '.join(parts[1:-1])
            uid,name=await resolve_target(message,target)
            # Role changes must be coordinated by the Puzzle process.
            if kind=='color' and color_editor is None:raise ValueError('Use color ownership commands in the Puzzle channel.')
            old_color=None
            if kind=='color' and action=='remove':
                profile=await asyncio.to_thread(shared.get_cosmetic_profile,uid,name)
                if profile.get('active_color')==canonical_item(kind,item):
                    old_color=profile['active_color'];await color_editor(uid,'')
            try:
                result=await asyncio.to_thread(change_inventory,message.author.id,uid,name,action,kind,item,count,tx)
            except Exception:
                if old_color is not None:await color_editor(uid,old_color)
                raise
            result_text=f"✅ **{name}:** {action} {count} × {result['item']} ({kind})."
            if result.get('active_reset'):result_text+=' The removed active item was reset to the default.'
        else:
            if command=='!editstat':
                if len(parts)<5:raise ValueError('Usage: !editstat <name> <puzzle|guess|chess> <field> <value>')
                domain,field,value=parts[-3:];domain=domain.casefold();field=field.casefold()
                target=' '.join(parts[1:-3]);operation='stat'
            else:
                if len(parts)<3:raise ValueError('Usage: '+command+' <name> <value>')
                operation=SCALAR_COMMANDS[command];value=parts[-1];target=' '.join(parts[1:-1])
            uid,name=await resolve_target(message,target)
            if operation in {'points','coins'}:
                result=await asyncio.to_thread(set_shared,message.author.id,uid,name,operation,value,tx)
            elif operation=='guess_points':
                result=await asyncio.to_thread(set_guess_points,message.author.id,uid,name,value,tx)
            elif operation in {
                'puzzle_elo', 'puzzle_best_streak', 'puzzle_current_streak'
            }:
                puzzle_field = {
                    'puzzle_elo': 'elo',
                    'puzzle_best_streak': 'best_streak',
                    'puzzle_current_streak': 'current_streak',
                }[operation]
                result=await asyncio.to_thread(
                    set_stat,message.author.id,uid,name,'puzzle',puzzle_field,value,tx
                )
            elif operation in {
                'guess_current_streak', 'guess_best_streak',
                'guess_total', 'guess_correct', 'guess_wrong'
            }:
                guess_field = {
                    'guess_current_streak': 'current_streak',
                    'guess_best_streak': 'best_streak',
                    'guess_total': 'total',
                    'guess_correct': 'correct',
                    'guess_wrong': 'wrong',
                }[operation]
                result=await asyncio.to_thread(
                    set_stat,message.author.id,uid,name,'guess',guess_field,value,tx
                )
            elif operation=='stat' and domain in {'puzzle','guess'}:
                result=await asyncio.to_thread(set_stat,message.author.id,uid,name,domain,field,value,tx)
            else:
                if operation=='stat' and domain!='chess':raise ValueError('Domain must be puzzle, guess or chess.')
                if daily_editor is None:raise ValueError('Use Chess and Rush corrections in the Puzzle channel.')
                daily_field=field if operation=='stat' else 'elo'
                result=await daily_editor(message.author.id,uid,name,operation,daily_field,value,tx)
            result_text=f"✅ **{name}:** {result['field']} set to **{result['value']}**."
        await send_text(message,result_text)
    except (ValueError,PermissionError) as error:
        await send_text(message,'❌ '+str(error))
    except discord.Forbidden:
        await send_text(message,'❌ Discord denied member/role access. Check the bot permissions and role order.')
    except Exception as error:
        print(f'Admin command failed: {type(error).__name__}: {error}',flush=True)
        await send_text(message,'❌ Could not complete/save the correction. Check the Actions log before retrying.')
    return True
