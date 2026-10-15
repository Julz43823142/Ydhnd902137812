"""Synthetic, deterministic selective-assistance and reference coverage checks."""
import copy
import math
import random
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import chess
import chess.engine
from fairplay_config import CONFIG, VERSION
from fairplay_maia import policy_evidence
from fairplay_policy import POLICY, alternatives, search_alternatives, game_summary
from fairplay_policy_episodes import integrate,game_episode
from fairplay_policy_coverage import summarize
from fairplay_ui import progress_embed
from test_fairplay_maia import decision
from test_fairplay_policy import result


def synthetic_game(index,mode='human',seed=0,count=24):
    """A specified toy human policy with exact fake engine values, not real chess evidence."""
    rng=random.Random(seed*100+index);board=chess.Board();decisions=[]
    while len(decisions)<count:
        legal=list(board.legal_moves)
        if len(legal)<8 or board.is_game_over():board=chess.Board();legal=list(board.legal_moves)
        choices=[m.uci() for m in legal];best=choices[0]
        p=.8 if mode=='elite' else .01
        distribution={m:(p if m==best else (1-p)/(len(choices)-1)) for m in choices}
        assisted=mode=='selective' and len(decisions)<3
        actual=(best if assisted or mode=='elite' else
                rng.choices(choices,weights=[distribution[m] for m in choices])[0])
        if mode=='rare_bad':actual=choices[-1]
        scores=[0]+[-150]*(len(choices)-1)
        d=SimpleNamespace(fen=board.fen(),move=actual,ply=21+len(decisions)*2,
            forced=False,trivial_kind=None,phase='middlegame',human_policy=distribution)
        d.metrics={'candidates':choices,'candidate_cp':scores,'actual_cp':0 if actual==best else -150,
            'nodes':CONFIG.deep_nodes,'competitive':True,'useful':True,
            'policy_search':{'nodes':CONFIG.deep_nodes,'scores':{}},
            'search_stability':{'objective_quality_preserved':True}}
        d.fast_policy=policy_evidence(d,distribution);decisions.append(d)
        board.push_uci(actual)
        if not board.is_game_over():board.push(rng.choice(list(board.legal_moves)))
    return SimpleNamespace(identity=f'synthetic-{index}',ended=1700000000+index*600,
        rated=True,time_class='blitz',time_control='180+0',class_index=index,deep=True,
        decisions=decisions,human_reference={},metrics={},fast_metrics={})


def episode_result(games,**kwargs):
    report=result();report.coverage['required_deep_complete']=True
    for k,v in kwargs.items():setattr(report,k,v)
    return integrate(report,games,CONFIG)


class AdaptiveReference(unittest.TestCase):
    def flat(self):
        d=decision();d.human_policy={m.uci():1/20 for m in chess.Board(d.fen).legal_moves}
        d.metrics.update(nodes=CONFIG.fast_nodes,actual_cp=0,search_contract={
            'mode':'nodes','requested':CONFIG.fast_nodes,'engine':'Synthetic','completed':True,'exact':True,'multipv':5})
        return d

    def test_flat_policy_gets_more_roots_with_a_strict_cap(self):
        d=self.flat();legacy=replace(POLICY,alternatives=3,minimum_move_mass=.025)
        self.assertEqual(len(alternatives(d,legacy)),3)
        self.assertEqual(len(alternatives(d)),12)
        self.assertFalse(set(alternatives(d))&set(d.metrics['candidates']))

    def test_peaked_policy_does_not_do_extra_work(self):
        d=self.flat();legal=list(d.human_policy)
        d.human_policy={m:(.95 if m==legal[0] else .05/19) for m in legal}
        self.assertEqual(alternatives(d),[])

    def test_ties_and_resumed_plans_have_stable_order(self):
        d=self.flat();first=alternatives(d)
        d.human_policy=dict(reversed(list(d.human_policy.items())))
        self.assertEqual(alternatives(d),first)
        self.assertEqual(first,sorted(first))

    def test_real_bound_math_gains_coverage_without_changing_thresholds(self):
        d=self.flat()
        scanner=SimpleNamespace(name='Synthetic',deadline=time.monotonic()+60,config=CONFIG,
            profile={'root_seconds':0,'root_searches':0,'fast_root_seconds':0},
            engine=SimpleNamespace(options={},analyse=lambda board,limit,root_moves:
                {'score':chess.engine.PovScore(chess.engine.Cp(-150),board.turn),'pv':root_moves}))
        original=alternatives
        old=replace(POLICY,alternatives=3,minimum_move_mass=.025)
        with patch('fairplay_policy.alternatives',side_effect=lambda row:original(row,old)):
            d.metrics['policy_search']=search_alternatives(scanner,d,CONFIG.fast_nodes)
        before=policy_evidence(d,d.human_policy)
        d.metrics['policy_search']=search_alternatives(scanner,d,CONFIG.fast_nodes)
        after=policy_evidence(d,d.human_policy)
        self.assertLess(before['known_policy_mass'],POLICY.known_mass)
        self.assertGreaterEqual(after['known_policy_mass'],POLICY.known_mass)
        self.assertGreater(after['quality_excess_lower_bound'],before['quality_excess_lower_bound'])
        self.assertLessEqual(after['expected_quality_upper_bound'],before['expected_quality_upper_bound'])
        self.assertEqual(POLICY.move_excess,.20)
        self.assertEqual(scanner.profile['root_searches'],15)

    def test_unknown_mass_remains_optimistically_bounded(self):
        d=self.flat();d.metrics['policy_search']={'nodes':CONFIG.fast_nodes,'scores':{}}
        row=policy_evidence(d,d.human_policy)
        self.assertGreater(row['unsearched_mass'],.70)
        self.assertLessEqual(row['quality_excess_lower_bound'],.20)


class SelectiveEpisodes(unittest.TestCase):
    def test_selective_help_survives_full_game_mean_dilution_but_caps_at_moderate(self):
        games=[synthetic_game(i,'selective') for i in range(10)]
        self.assertTrue(all(not game_summary(g)['contributor'] for g in games))
        report=episode_result(games)
        self.assertEqual(report.priority,'MODERATE')
        self.assertTrue(report.diagnostics['selective_policy_episodes']['best']['passed'])
        self.assertFalse(report.deep_confirmed)

    def test_human_null_and_elite_reference_controls_do_not_elevate(self):
        for mode in ('human','elite','rare_bad'):
            with self.subTest(mode=mode):
                report=episode_result([synthetic_game(i,mode) for i in range(10)])
                self.assertEqual(report.priority,'LOW')

    def test_no_single_game_or_two_great_games_can_establish_replication(self):
        for n in (1,2):
            games=[synthetic_game(i,'selective' if i<n else 'human') for i in range(10)]
            self.assertEqual(episode_result(games).priority,'LOW')

    def test_fast_selected_block_cannot_switch_to_a_different_deep_block(self):
        game=synthetic_game(0,'selective')
        for d in game.decisions[:6]:d.metrics['actual_cp']=-150
        self.assertFalse(game_episode(game)['deep_confirmed'])

    def test_missing_deep_or_quality_stability_never_confirms(self):
        for missing in ('deep','stable','policy'):
            games=[synthetic_game(i,'selective') for i in range(10)]
            for g in games:
                if missing=='deep':g.deep=False
                else:
                    for d in g.decisions:
                        if missing=='stable':d.metrics['search_stability']={}
                        else:d.human_policy={}
            self.assertEqual(episode_result(games).priority,'LOW')

    def test_missing_fast_rows_are_not_compressed_into_contiguous_evidence(self):
        game=synthetic_game(0,'selective');game.decisions[3].fast_policy=None
        self.assertFalse(game_episode(game)['contributor'])

    def test_verified_forced_moves_leave_no_engine_evidence_or_false_gaps(self):
        games=[synthetic_game(i,'selective') for i in range(10)]
        for game in games:
            expanded=[]
            for d in game.decisions:
                expanded.append(d)
                forced=copy.deepcopy(d);forced.forced=True
                expanded.append(forced)
            game.decisions=expanded
            episode=game_episode(game)
            self.assertTrue(episode['deep_confirmed'])
            self.assertEqual(episode['indices'],[0,2,4,6,8,10])
        self.assertEqual(episode_result(games).priority,'MODERATE')

    def test_deep_context_cannot_choose_different_fast_episode_indices(self):
        game=synthetic_game(0,'selective')
        first=game_episode(game)['indices']
        # Only fast context determines which blocks are selected. A changed
        # deep exclusion makes paired evidence unavailable, not a new block.
        game.decisions[0].metrics['easy_conversion']=True
        later=game_episode(game)
        self.assertEqual(later['indices'],first)
        self.assertFalse(later['deep_confirmed'])

    def test_duplicate_games_and_positions_cannot_manufacture_replication(self):
        g=synthetic_game(0,'selective')
        self.assertEqual(episode_result([copy.deepcopy(g) for _ in range(10)]).priority,'LOW')
        games=[synthetic_game(i,'selective') for i in range(10)]
        for game in games:
            for d in game.decisions:d.fen=g.decisions[0].fen;d.move=g.decisions[0].move
        self.assertEqual(episode_result(games).priority,'LOW')

    def test_unknown_missing_and_disabled_coverage_fail_closed(self):
        games=[synthetic_game(i,'selective') for i in range(10)]
        self.assertEqual(episode_result(games,partial=True).priority,'LOW')
        r=result();r.coverage['required_deep_complete']=False
        self.assertEqual(integrate(r,games,CONFIG).priority,'LOW')
        self.assertEqual(integrate(result(),games,replace(CONFIG,disabled_features=('neural',))).priority,'LOW')
        self.assertEqual(integrate(result(),games,CONFIG).priority,'LOW')

    def test_history_holes_and_mixed_classes_cannot_invent_a_period(self):
        games=[synthetic_game(i,'selective') for i in range(10)]
        for i,g in enumerate(games):g.class_index=i*2
        self.assertEqual(episode_result(games).priority,'LOW')
        for i,g in enumerate(games):g.class_index=i;g.time_class='rapid' if i%2 else 'blitz'
        self.assertEqual(episode_result(games).priority,'LOW')

    def test_daily_unrated_and_bullet_not_compared_with_live_policy(self):
        for mode in ('daily','bullet','unrated'):
            games=[synthetic_game(i,'selective') for i in range(10)]
            for g in games:
                if mode=='unrated':g.rated=False
                else:g.time_class=mode
            self.assertEqual(episode_result(games).priority,'LOW')

    def test_existing_high_and_reasons_are_preserved(self):
        games=[synthetic_game(i,'selective') for i in range(10)]
        report=episode_result(games,priority='HIGH',reasons=['Existing confirmed route.'])
        self.assertEqual(report.priority,'HIGH');self.assertEqual(report.reasons,['Existing confirmed route.'])

    def test_no_nonfinite_fast_evidence_counts(self):
        for key in ('signed_quality_excess','known_policy_mass','reference_weight'):
            game=synthetic_game(0,'selective')
            for bad in (float('nan'),None,True):
                for d in game.decisions[:6]:d.fast_policy[key]=bad
                self.assertFalse(game_episode(game)['contributor'])


class ProgressAndCoverage(unittest.TestCase):
    def test_version_is_visible_in_initial_running_and_stopped_cards(self):
        for stage in ('Fetching profile…','Fast engine scan 10 / 100','Stopped by moderator'):
            self.assertIn(VERSION,progress_embed('synthetic',stage).footer.text)

    def test_unresolved_reference_is_visible_not_normalized_away(self):
        g=synthetic_game(0,'selective');g.decisions[0].metrics.pop('policy_search')
        coverage=summarize([g])
        self.assertGreater(coverage['incomplete'],0)
        self.assertEqual(coverage['eligible'],24)
        self.assertFalse(coverage['scoring_influence'])


if __name__=='__main__':unittest.main()
