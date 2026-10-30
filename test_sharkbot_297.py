"""Regression checks for SharkBot 297 display and payout policy."""
import ast
from pathlib import Path


def test_weekly_winner_coin_scale():
    source = Path("bot.py").read_text()
    module = ast.parse(source)
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "puzzle_rush_weekly_winner_coins")
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "bot.py", "exec"), scope)
    reward = scope["puzzle_rush_weekly_winner_coins"]
    assert [reward(n) for n in (0, 1, 2, 3, 8, 9, 20)] == [0, 10, 15, 20, 45, 50, 50]


def test_alert_labels_and_privacy():
    source = Path("fairplay_ban_alerts_ui.py").read_text()
    assert "color=0x8b0000 if fair_play else 0x64748b" in source
    assert "Closed — reason unknown" in source
    assert "f' ({uid})'" not in source


def test_closed_reason_not_inferred():
    source = Path("fairplay_ban_alerts.py").read_text()
    assert "status not in (FAIRPLAY_CLOSED,'closed')" in source
    assert "'closure_status':status" in source

def test_alert_settings_and_siren():
    ui = Path("fairplay_ban_alerts_ui.py").read_text()
    logic = Path("fairplay_ban_alerts.py").read_text()
    assert "Fair Play Ban Alert 🚨" in ui
    assert "('Subscribe','add'),('Settings','settings')" in ui
    assert "class AlertSettingsView" in ui
    assert "class AlertTypeSelect" in ui
    assert "def alert_enabled(user,event):" in logic
    assert "if not alert_enabled(u,{'closure_status':status}):continue" in logic
    assert "or not alert_enabled(user,event)" in logic
