"""Explicit local-only diagnostics; never imported by bot routing or log hooks."""
import copy
import json
from pathlib import Path


def export_decisions(result,path,limit=100):
    from fairplay_validation import private_input_path
    path=private_input_path(path)
    rows=[]
    for game in result.timeline:
        for d in game.decisions:
            m=d.metrics
            if not m:continue
            rows.append({'game':game.identity,'date':game.ended,'move_number':d.fullmove,'move':d.move,
                'rank':m.get('rank'),'best':m.get('best'),'cpl':m.get('cpl'),'scaled_loss':m.get('scaled_loss'),
                'gap':m.get('gap'),'spread':m.get('spread'),'difficulty':m.get('difficulty'),
                'human_information':m.get('human_information',0),'expectedness':m.get('human_expectedness'),
                'think_time':d.think,'stability':m.get('search_stability'),'book':d.opening,
                'opponent_error':m.get('post_opponent_error'),'deep':game.deep,
                'information_explanation':('Difficult competitive choice with few equivalent alternatives.' if m.get('human_opportunity') else
                    'Opening, easy/forced conversion, equivalent alternatives or insufficient rating/difficulty evidence.')})
    rows.sort(key=lambda r:r['human_information'],reverse=True)
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(rows[:max(1,min(500,limit))],indent=2));path.chmod(0o600)


def ablate(result,profile,remove,config=None):
    """Research-only sensitivity analysis; removal groups overlap by design."""
    from fairplay_config import CONFIG
    from fairplay_scoring import score_review
    from dataclasses import replace
    remove=set(remove)
    if not remove<=set(['human','timing','results','personal','opening','difficulty','recurrence']):
        raise ValueError('Unknown ablation family')
    config=replace(config or CONFIG,disabled_features=tuple(sorted(remove)))
    games=copy.deepcopy(result.timeline)
    for game in games:
        if 'timing' in remove:
            for d in game.decisions:d.clock_valid=d.clock_reliable=False;d.think=None
        if 'human' in remove or 'difficulty' in remove:
            for d in game.decisions:
                for m in [d.metrics,d.fast_engine]:m.update(human_information=0,human_opportunity=False,high_information=False)
            for m in [game.metrics,game.fast_metrics]:m['human']={}
    output=score_review('local-research',games,result.selected_games,result.skipped,result.partial,result.engine,profile,0,config)
    return {'priority':output.priority,'research_evidence_index':output.diagnostics.get('gameplay',{}).get('research_evidence_index'),
            'high_path':output.diagnostics.get('high_path'),'removed':sorted(remove),
            'limitations':'Opening removal disables contextual repertoire only; unsearched book moves cannot be reconstructed without a new engine scan.'}
