"""Explainable qualitative indicators; no opaque precision or price forecasts."""
def assess(report,previous=None):
    if report.get('historical'):
        return {'label':'Not enough tracked data','reasons':['Legacy audits do not establish complete mint/burn and activity coverage.']}
    a=report.get('activity',{});opening=a.get('opening_supply');closing=a.get('closing_supply')
    if opening is None or closing is None or opening<=0:
        return {'label':'Not enough tracked data','reasons':['A positive opening supply and closing supply are required.']}
    if a.get('unattributed_wallet_change'):
        return {'label':'Not enough tracked data','reasons':[f"Unclassified wallet changes: {a['unattributed_wallet_change']:+g} coins.",'Complete source/sink coverage is required before assigning an inflation label.']}
    change=100*(closing-opening)/opening
    net_creation=100*(a.get('minted',0)-a.get('burned',0))/opening
    active=len(a.get('active',[]));trades=a.get('trades',0)
    label=('Low Activity' if active<3 and trades==0 else 'High Inflation' if net_creation>15 else
           'Mild Inflation' if net_creation>5 else 'Deflation' if net_creation < -5 else 'Healthy')
    reasons=[f'Saved weekly wallet supply: {change:+.1f}%',f'Net recorded coin creation / opening supply: {net_creation:+.1f}%',
             f"Recorded creation/removal: +{a.get('minted',0):g} / −{a.get('burned',0):g} coins",
             f'{active} active wallets · {trades} trades · {a.get("traded_coins",0):g} coins exchanged']
    if report.get('supply',0)>0:
        share=sum(float(e.get('coins',0)) for _,e in report.get('top',[]))/report['supply']
        reasons.append(f'Top-three wallet share at snapshot: {share:.0%} ({"High" if share>.75 else "Moderate" if share>.4 else "Low"})')
        reasons.append(f"Tracked trade turnover / snapshot wallet supply: {a.get('traded_coins',0)/report['supply']:.1%}")
    if previous and previous.get('activity'):
        old=previous['activity'].get('traded_coins',0);new=a.get('traded_coins',0)
        if old>0:reasons.append('Trade volume: '+('Rising' if new>old*1.2 else 'Falling' if new<old*.8 else 'Stable'))
    spending=a.get('burned',0)/opening
    reasons.append('Spending activity (recorded coin sinks / opening supply): '+('High' if spending>=.1 else 'Moderate' if spending>=.02 else 'Low'))
    reasons.append('Spending thresholds: >=10% high, >=2% moderate. Only audited sinks count.')
    reasons.append('Net creation thresholds: >5% mild, >15% high, <−5% deflation. Low activity: <3 active wallets and no trades. Wallet supply excludes escrow; first week can be partial.')
    return {'label':label,'reasons':reasons}
