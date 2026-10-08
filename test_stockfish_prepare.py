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
        self.assertIn('STOCKFISH_PATH: /tmp/stockfish19-official-pgo/Stockfish/src/stockfish',source)
        self.assertIn(chess_play.STOCKFISH_SOURCE_REVISION,source)

    def test_failed_optional_engine_preparation_does_not_stop_discord(self):
        import re
        source=Path('.github/workflows/daily_puzzle_and_answer.yml').read_text()
        # Bound to the actual preparation step: a flag on a different step
        # must not satisfy this availability contract. No YAML runtime dep.
        block=re.search(r'(?ms)^      - name: Prepare and smoke-test Stockfish before Discord starts\n(.*?)(?=^      - |\Z)',source)
        self.assertIsNotNone(block)
        self.assertRegex(block.group(1),r'(?m)^        continue-on-error: true$')



class ProfileGuidedBuildTests(unittest.TestCase):
    def test_pinned_source_and_official_profile_target_are_preserved(self):
        from contextlib import ExitStack
        from types import SimpleNamespace
        with ExitStack() as stack:
            stack.enter_context(patch.dict('os.environ',{'GITHUB_ACTIONS':'true','STOCKFISH_AUTO_INSTALL':'1'}))
            for name,value in [('_STOCKFISH_INSTALL_ATTEMPTED',False),('_STOCKFISH_PATH',None)]:
                stack.enter_context(patch.object(chess_play,name,value))
            stack.enter_context(patch.object(chess_play.shutil,'which',return_value='/tool'))
            stack.enter_context(patch.object(chess_play.os.path,'isdir',return_value=False))
            stack.enter_context(patch.object(chess_play.os,'makedirs'))
            stack.enter_context(patch.object(chess_play.os,'chmod'))
            stack.enter_context(patch.object(chess_play,'_probe_stockfish_major',return_value=19))
            run=stack.enter_context(patch.object(chess_play.subprocess,'run',return_value=
                SimpleNamespace(returncode=0,stdout=chess_play.STOCKFISH_SOURCE_REVISION,stderr='')))
            chess_play._try_install_stockfish_on_github_actions(profile_build=True)
            self.assertEqual(run.call_count,3)
            self.assertIn('https://github.com/official-stockfish/Stockfish.git',run.call_args_list[0].args[0])
            self.assertIn('rev-parse',run.call_args_list[1].args[0])
            self.assertIn('profile-build',run.call_args_list[2].args[0])
            self.assertIn('ARCH=x86-64-avx2',run.call_args_list[2].args[0])
            self.assertIn('stockfish19-official-pgo',chess_play._STOCKFISH_PATH)

if __name__=='__main__':unittest.main()
