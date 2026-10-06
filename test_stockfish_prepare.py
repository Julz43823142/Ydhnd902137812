"""Offline provisioning contract; a separate real-engine smoke test is opt-in."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import chess
import chess.engine
import chess_play
from scripts.prepare_stockfish import prepare


class EnginePreparation(unittest.TestCase):
    def test_exports_path_only_after_successful_real_search_and_closes(self):
        engine=Mock(id={'name':'Stockfish 19'},analyse=Mock(return_value={
            'pv':[chess.Move.from_uci('e2e4')],
            'score':chess.engine.PovScore(chess.engine.Cp(20),chess.WHITE)}))
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'github-env'
            with patch.object(chess_play,'_create_stockfish_engine',return_value=engine),patch.object(chess_play,'_resolve_stockfish_binary',return_value='/synthetic/stockfish'):
                self.assertEqual(prepare(destination),'/synthetic/stockfish')
            self.assertEqual(destination.read_text(),'STOCKFISH_PATH=/synthetic/stockfish\n')
        engine.quit.assert_called_once()

    def test_incomplete_engine_data_fails_without_export_and_closes(self):
        engine=Mock(id={'name':'Stockfish 19'},analyse=Mock(return_value={}))
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'github-env'
            with patch.object(chess_play,'_create_stockfish_engine',return_value=engine):
                with self.assertRaisesRegex(RuntimeError,'incomplete'):prepare(destination)
            self.assertFalse(destination.exists())
        engine.quit.assert_called_once()

    def test_runtime_scan_does_not_install_or_use_shared_game_engine(self):
        from fairplay_analysis import EngineScanner
        import time
        engine=Mock(options={},id={'name':'Synthetic'},transport=SimpleNamespace(get_pid=lambda:-1))
        with patch.object(chess_play,'_create_stockfish_engine',return_value=engine) as create:
            scanner=EngineScanner(time.monotonic()+10)
            scanner.close()
        self.assertFalse(create.call_args.kwargs['allow_install'])

    def test_workflow_installs_and_smoke_tests_before_discord(self):
        source=Path('.github/workflows/daily_puzzle_and_answer.yml').read_text()
        self.assertLess(source.index('python scripts/prepare_stockfish.py --github-env'),source.index('run: python bot.py'))
        self.assertIn('STOCKFISH_PATH: /tmp/stockfish19-official/Stockfish/src/stockfish',source)
        self.assertIn(chess_play.STOCKFISH_SOURCE_REVISION,source)

    def test_failed_optional_engine_preparation_does_not_stop_discord(self):
        import yaml
        workflow=yaml.safe_load(Path('.github/workflows/daily_puzzle_and_answer.yml').read_text())
        setup=next(step for step in workflow['jobs']['run']['steps'] if step.get('run')=='python scripts/prepare_stockfish.py --github-env')
        self.assertIs(setup['continue-on-error'],True)


if __name__=='__main__':unittest.main()
