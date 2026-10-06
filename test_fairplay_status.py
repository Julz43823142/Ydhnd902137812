"""Closure labels never influence screening; all account data is synthetic."""
from dataclasses import asdict
import unittest
from unittest.mock import Mock,patch

import fairplay_analysis as analysis
import fairplay_ui as ui
from test_fairplay import TARGET,FakeEngine,sample_row
from test_fairplay_baseline import personal_game


STATUSES = ('basic','closed','closed:fair_play_violations',
            'closed:abuse','closed:inactive',None)


class UnreadableStatus(dict):
    def __getitem__(self,key):
        if key=='status':raise AssertionError('Closure status must never be read.')
        return super().__getitem__(key)
    def get(self,key,*args):
        if key=='status':raise AssertionError('Closure status must never be read.')
        return super().get(key,*args)


class AccountStatusIndependence(unittest.TestCase):
    def profile(self,status=None,include=False):
        profile=UnreadableStatus(username=TARGET,joined=1700000000,title='')
        if include:profile.update(status=status)
        return profile

    def test_explicit_neutral_context_allowlist_does_not_read_status(self):
        profile=self.profile('closed:fair_play_violations',True)
        profile.update(closed=True,ban_reason='external label',is_fair_play_banned=True)
        self.assertEqual(analysis.neutral_profile_context(profile),{'joined':1700000000,'title':''})

    def test_full_scoring_is_identical_with_hidden_open_or_closed_status(self):
        for high in (False,True):
            games=[personal_game(i,high=high) for i in range(30)]
            for game in games:game.deep=True
            def evaluate(profile):
                with patch.object(analysis.time,'time',return_value=1791273600):
                    return analysis.score_review(TARGET,games,30,{},False,'Synthetic',profile,1)
            expected=evaluate(self.profile())
            self.assertEqual(expected.priority,'HIGH' if high else 'LOW')
            for status in STATUSES:
                with self.subTest(high=high,status=status):
                    actual=evaluate(self.profile(status,True))
                    self.assertEqual(asdict(actual),asdict(expected))
                    self.assertEqual(ui.result_embed(actual).to_dict(),ui.result_embed(expected).to_dict())
                    for mode in ('Timing','Performance','Engine Analysis','Highest-Signal Games'):
                        self.assertEqual(ui.detail_embed(actual,mode).to_dict(),ui.detail_embed(expected,mode).to_dict())

    def test_api_pipeline_result_is_identical_when_status_is_hidden(self):
        def run(profile):
            analysis._game_cache.clear()
            api=Mock();api.deadline=1294
            api.get.side_effect=lambda target,suffix='',**kwargs: profile if not suffix else {'archives':[f'https://api.chess.com/pub/player/{TARGET}/games/2026/10']} if suffix.endswith('/archives') else {'games':[sample_row()]}
            engine=FakeEngine()
            with patch.object(analysis.time,'monotonic',return_value=1234),patch.object(analysis.time,'time',return_value=1791273600):
                result=analysis.review(TARGET,lambda stage:None,api_factory=lambda deadline:api,engine_factory=lambda:engine)
            api.close.assert_called_once();engine.quit.assert_called_once()
            return asdict(result)
        try:
            expected=run(self.profile())
            for status in STATUSES:
                with self.subTest(status=status):self.assertEqual(run(self.profile(status,True)),expected)
        finally:analysis._game_cache.clear()

    def test_direct_account_context_cannot_observe_closure_metadata(self):
        games=[personal_game(i) for i in range(20)]
        with patch.object(analysis.time,'time',return_value=1791273600):
            baseline=analysis.context_metrics(games,self.profile())
            for status in STATUSES:
                self.assertEqual(analysis.context_metrics(games,self.profile(status,True)),baseline)


if __name__=='__main__':unittest.main()
