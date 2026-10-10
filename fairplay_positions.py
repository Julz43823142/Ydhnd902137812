"""Position context from the same-budget engine evaluations, not opponent Elo.

Evaluation changes across adjacent subject decisions estimate the opponent's
intervening error. Missing/unequal-budget evaluations are unknown, not errors.
This is an approximate search-derived exposure measure, not a blunder verdict.
No additional engine processes/searches are needed.
"""
import statistics
import chess
from fairplay_config import CONFIG


def opponent_context(games):
    rated = [g for g in games if g.rating is not None and g.opponent_rating is not None]
    def med(values):return statistics.median(values) if values else None
    expected = [1/(1+10**(max(-4000,min(4000,g.opponent_rating-g.rating))/400)) for g in rated]
    return {'rated_games':len(rated), 'player_rating':med([g.rating for g in rated]),
            'opponent_rating':med([g.opponent_rating for g in rated]),
            'elo_difference':med([g.rating-g.opponent_rating for g in rated]),
            'expected_score':statistics.mean(expected) if expected else None,
            'actual_score':statistics.mean([g.score for g in rated]) if rated else None}


def same_verified_search_budget(left, right):
    """Compare search *contracts*, never achieved node counts at fixed depth.

    A depth-18/12 search completes after different numbers of nodes in
    different positions. Matching those node totals suppressed essentially
    every post-opponent-error observation in the deep pass. Missing, partial,
    non-exact or mismatched contracts fail closed. Legacy fixed-node fixtures
    without contracts retain their previous equal-node behavior.
    """
    a = left.get('search_contract') or {}
    b = right.get('search_contract') or {}
    if a or b:
        if not a or not b:return False
        mode=a.get('mode')
        request=a.get('requested')
        if (mode not in ('nodes','depth')
                or mode!=b.get('mode') or request!=b.get('requested')
                or not isinstance(request,int) or isinstance(request,bool) or request<=0
                or not a.get('completed') or not b.get('completed')
                or a.get('exact') is not True or b.get('exact') is not True
                or a.get('engine')!=b.get('engine')
                or a.get('multipv')!=b.get('multipv')):
            return False
        if mode=='depth':
            return (isinstance(left.get('search_depth'),int)
                    and isinstance(right.get('search_depth'),int)
                    and left['search_depth']>=request
                    and right['search_depth']>=request)
        return True
    return (left.get('nodes') is not None and
            left.get('nodes')==right.get('nodes'))


def position_context(game, config=CONFIG):
    """Annotate existing evaluations; idempotent across fast/deep re-summaries.

Large engine gaps alone do not make a quiet difficult move automatic. Easy
capture exclusions need concrete material gain plus a winning/error context.
Hard decisions remain evidence even against a very weak opponent.
"""
    previous = None
    for d in game.decisions:
        m = d.metrics
        if 'before_cp' not in m or 'actual_cp' not in m:
            previous = None
            continue
        m.setdefault('position_base_useful',m.get('useful',False))
        m.setdefault('position_base_critical',m.get('critical',False))
        m.setdefault('position_base_unique',m.get('unique',False))
        m.setdefault('position_base_weight',m.get('weight',1))
        m['useful']=m['position_base_useful'];m['critical']=m['position_base_critical']
        m['unique']=m['position_base_unique'];m['weight']=m['position_base_weight']
        before=m['before_cp'];swing=None
        if (previous is not None and previous.ply+2==d.ply
                and same_verified_search_budget(previous.metrics,m)
                and not previous.metrics.get('search_inconsistent') and not m.get('search_inconsistent')
                and max(abs(before),abs(previous.metrics['actual_cp']))<9000):
            swing=before-previous.metrics['actual_cp']
        error=swing is not None and swing>=config.opponent_error_cp
        winning=before>=config.easy_winning_cp
        board=chess.Board(d.fen);move=chess.Move.from_uci(d.move)
        obvious_gain=False
        if d.capture and move in board.legal_moves:
            values={chess.PAWN:100,chess.KNIGHT:320,chess.BISHOP:330,chess.ROOK:500,chess.QUEEN:900,chess.KING:0}
            victim=chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
            attacker=board.piece_type_at(move.from_square)
            gain=values.get(victim,0)-values.get(attacker,0)
            # Include obviously free major pieces. Pawn grabs are not assumed easy.
            undefended=(values.get(victim,0)>=320 and not board.is_attacked_by(not board.turn,move.to_square))
            obvious_gain=gain>=config.opponent_error_cp or undefended
        automatic=bool((error or winning) and obvious_gain and m.get('gap',0) is not None
                       and m.get('gap',0)>=config.critical_gap)
        easy=bool(winning and (automatic or (d.capture and m.get('gap',0) is not None
                                             and m.get('gap',0)>=config.unique_gap)))
        competitive=bool(m['useful'] and abs(before)<=config.competitive_eval_cp and not error)
        m.update(opponent_swing_cp=swing,post_opponent_error=error,
                 automatic_material_gain=automatic,
                 opponent_material_exposure=bool(error and obvious_gain),
                 equal_to_winning=bool(error and previous is not None
                    and abs(previous.metrics['actual_cp'])<=config.competitive_eval_cp and winning),
                 easy_winning=winning,easy_conversion=easy,competitive=competitive,
                 opponent_complexity_reduction=(previous.metrics.get('weight',0)-m['weight']) if error and previous else None)
        if automatic:
            m['useful']=m['critical']=m['unique']=False;m['weight']=0
        elif easy:
            m['weight']*=config.easy_conversion_weight
            m['critical']=m['unique']=False
        previous=d


def position_summary(game):
    rows=[d.metrics for d in game.decisions if 'before_cp' in d.metrics]
    competitive=[m for m in rows if m.get('competitive') and m.get('useful')]
    critical=[m for m in competitive if m.get('critical')]
    def rate(rows,key):return sum(bool(m.get(key)) for m in rows)/len(rows) if rows else None
    return {'position_context_decisions':len(rows),
            'opponent_blunder_exposure':sum(bool(m.get('post_opponent_error')) for m in rows),
            'opponent_material_exposure':sum(bool(m.get('opponent_material_exposure')) for m in rows),
            'equal_to_winning':sum(bool(m.get('equal_to_winning')) for m in rows),
            'post_opponent_error_decisions':sum(bool(m.get('post_opponent_error')) for m in rows),
            'easy_winning_position_fraction':rate(rows,'easy_winning'),
            'easy_conversion_decisions':sum(bool(m.get('easy_conversion')) for m in rows),
            'competitive_decisions':len(competitive),'competitive_top1':rate(competitive,'top1'),
            'competitive_cpl':statistics.median([m['cpl'] for m in competitive]) if competitive else None,
            'competitive_critical':len(critical),'competitive_critical_top1':rate(critical,'top1')}
