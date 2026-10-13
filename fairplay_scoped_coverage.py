"""Positive gameplay evidence from a fully retrieved suffix of a partial history.

Missing archives never supply evidence or a clean baseline. A supported recent
period can survive unavailable *earlier* data; an unsupported incomplete scan
cannot become a confident LOW. All underlying gameplay thresholds stay intact.
"""
from datetime import datetime, timezone
from fairplay_config import CONFIG


def complete_suffix_boundary(coverage):
    months=coverage.get('incomplete_archive_months')
    if not isinstance(months,list) or not months:return None
    ends=[]
    for month in months:
        if not isinstance(month,str):return None
        try:
            parts=month.split('-')
            if len(parts)!=2 or len(parts[0])!=4 or len(parts[1])!=2:return None
            year,value=map(int,parts)
            if not 2007<=year<=2100 or not 1<=value<=12:return None
            ends.append(datetime(year+int(value==12),1 if value==12 else value+1,1,tzinfo=timezone.utc).timestamp())
        except (TypeError,ValueError):return None
    return max(ends)


def integrate(result,games,history,collection,coverage_state,engine,profile,config=CONFIG):
    boundary=complete_suffix_boundary(collection)
    audit={'evaluated':False,'passed':False,'boundary':boundary,'blockers':[]}
    result.diagnostics['retrieved_period_review']=audit
    if not result.partial or collection.get('primary_archive_partial') is not True:return result
    # Missing CPU/engine work, unlike missing older archives, is required
    # evidence. Do not bypass that contract with a different scoring scope.
    if (coverage_state.get('observed_primary_engine_complete') is not True
            or coverage_state.get('required_deep_complete') is not True):
        audit['blockers']=['Required engine work is incomplete.']
    elif boundary is None:
        audit['blockers']=['Archive gaps are not localized; a complete recent period cannot be established.']
    else:
        context=[g for g in history if g.rated is True and g.time_class!='daily' and g.ended>=boundary]
        selected=[g for g in games if g.rated is True and g.time_class!='daily' and g.ended>=boundary]
        audit.update(context_games=len(context),reviewed_games=len(selected),deep_games=sum(g.deep is True for g in selected))
        if len(selected)<config.min_games:
            audit['blockers']=['Too few reviewed games after the most recent data gap.']
        else:
            # Selection comes from the unchanged pre-engine plan, never hits.
            # Keep original chronological indices: missing intermediate games
            # in a control/class still invalidate an existing period gate.
            from fairplay_scoring import score_review
            from fairplay_policy import integrate as integrate_policy
            scoped=score_review(result.username,selected,len(context),{},False,
                engine,profile,0,config,context_games=context,
                coverage_state={'primary_engine_complete':True,'required_deep_complete':True,
                                'context_history_complete':True,'optional_context_partial':False})
            scoped=integrate_policy(scoped,selected,config,strict_original_sequence=True)
            audit.update(evaluated=True,priority=scoped.priority,confidence=scoped.confidence,
                         high_paths=scoped.diagnostics.get('high_paths',{}))
            audit['passed']=scoped.priority in ('HIGH','VERY HIGH') and scoped.deep_confirmed
            if audit['passed'] and result.priority in ('LOW','MODERATE','INSUFFICIENT DATA'):
                result.priority='HIGH'  # Never create VERY HIGH from this partial-history route.
                result.deep_confirmed=True
                result.confidence='MEDIUM' if scoped.confidence=='HIGH' else scoped.confidence
                result.reasons=[
                    'A fully retrieved recent period met the existing deep-confirmed gameplay HIGH criteria.',
                    'Earlier archive data is unavailable. Missing data is not evidence of misconduct or a clean baseline.',
                    *scoped.reasons[:2]]
                result.diagnostics.update(high_path='Deep-confirmed gameplay in a fully retrieved recent period',high_blocked=[])
    result.diagnostics.setdefault('high_paths',{})['Fully retrieved recent-period HIGH']={
        'passed':audit['passed'],'blockers':audit['blockers'] if audit['blockers'] else
        ([] if audit['passed'] else ['The complete suffix did not meet existing gameplay HIGH criteria.'])}
    if result.priority=='LOW':
        result.priority='INSUFFICIENT DATA'
        result.confidence='LOW'
        result.reasons=['Archive data is missing and no supported elevated gameplay period was established. This incomplete history cannot justify a LOW conclusion.']
    return result
