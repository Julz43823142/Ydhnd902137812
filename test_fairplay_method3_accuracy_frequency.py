"""Accuracy frequency is descriptive, opponent-aware, and never a priority override."""
import unittest
from types import SimpleNamespace
from fairplay_method3_accuracy_frequency import summarize, historical_signal


def game(kind, accuracy, own, opponent, moves=25):
    return SimpleNamespace(time_class=kind,accuracy=accuracy,rating=own,
        opponent_rating=opponent,decisions=[None]*moves)


class AccuracyFrequencyTests(unittest.TestCase):
    def test_thresholds_and_opponent_elo_buckets(self):
        games=[
            game('blitz',96,2000,2010),
            game('blitz',90,2000,1800),
            game('blitz',95,2000,1700),
            game('blitz',89.9,2000,2250),
            game('blitz',None,2000,2000),
            game('rapid',99,1800,2100)]
        result=summarize(games)
        blitz=result['by_time_class']['blitz']
        self.assertEqual((blitz['games'],blitz['accuracy_coverage']), (5,4))
        self.assertEqual((blitz['at_least_90'],blitz['at_least_95']),(3,2))
        self.assertEqual(blitz['opponent_context']['near']['at_least_90'],2)
        self.assertEqual(blitz['opponent_context']['weaker']['at_least_95'],1)
        self.assertEqual(blitz['opponent_context']['stronger']['games'],1)
        self.assertEqual(result['by_time_class']['rapid']['at_least_95'],1)
        self.assertFalse(result['calibrated'])
        self.assertFalse(result['affects_priority'])

    def test_historical_frequency_signal_needs_context_and_large_shift(self):
        rows=[]
        for i in range(100):
            g=game('blitz',95 if i>=70 else 80,1800,1800,30)
            g.ended=i
            g.identity=str(i)
            g.opponent_username='opponent'+str(i%30)
            rows.append(g)
        signals=historical_signal(rows)
        self.assertEqual(len(signals),1)
        self.assertEqual(signals[0]['time_class'],'blitz')
        self.assertEqual(historical_signal(rows[:65]),[])

    def test_missing_ratings_and_empty_coverage(self):
        row=summarize([game('bullet',90,None,1700),game('bullet',None,1500,1500)])
        bullet=row['by_time_class']['bullet']
        self.assertEqual(bullet['opponent_context']['unknown']['games'],1)
        self.assertEqual(bullet['accuracy_coverage'],1)
        self.assertEqual(row['by_time_class']['blitz']['accuracy_coverage'],0)


if __name__=='__main__':
    unittest.main()
