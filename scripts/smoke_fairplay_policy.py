"""Real Maia + real Stockfish, synthetic game only; no account/network fixtures."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fairplay_analysis import review,close_shared_engine_pool
from fairplay_config import CONFIG
from fairplay_maia import policy_evidence
from scripts.smoke_stockfish_reviews import FixtureAPI,TARGET

def main():
    try:
        r=review(TARGET,lambda _:None,api_factory=FixtureAPI)
        ref=r.diagnostics['human_reference']
        assert ref['available'] and ref['positions']>0
        assert ref['fast_counterfactual']['complete'] and ref['deep_counterfactual']['complete']
        paired=0
        for game in r.games:
            for d in game.decisions:
                if not d.human_policy:continue
                assert d.fast_policy is not None
                extra=d.metrics.get('policy_search',{})
                assert extra.get('nodes')==CONFIG.deep_nodes
                row=policy_evidence(d,d.human_policy)
                if row and d.fast_policy:
                    assert 0<=row['known_policy_mass']<=1.00001
                    assert row['counterfactual_complete']
                    paired+=1
        assert paired>0
        assert r.priority not in ('HIGH','VERY HIGH'), 'One game cannot establish HIGH'
        print('Real human-alternative integration passed:',paired,'paired decisions.',
              'Runtime:',round(r.elapsed,2),'seconds.')
    finally:close_shared_engine_pool()

if __name__=='__main__':main()
