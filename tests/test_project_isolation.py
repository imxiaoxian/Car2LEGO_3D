"""Guard the repository's deployment and backend boundaries."""
import sys

import pytest

from car2lego3d.common import ROOT
from car2lego3d.project import PROJECT_NAME, DEFAULT_BACKEND, FRONTEND_PORT, API_PORT, desktop_backend


def test_desktop_backend_is_open_source_only():
    assert PROJECT_NAME == "Car2LEGO_3D"
    assert desktop_backend() == DEFAULT_BACKEND == "hunyuan-mv"
    assert desktop_backend("hunyuan-mv") == DEFAULT_BACKEND
    with pytest.raises(ValueError, match="Car2LGEO_3D_LLM"):
        desktop_backend("gpt-blender")
    assert (FRONTEND_PORT, API_PORT) == (8768, 8767)


def test_cli_default_setup_routes_to_hunyuan(monkeypatch, capsys):
    from car2lego3d import cli, multiview
    monkeypatch.setattr(multiview, "setup_source", lambda: "hunyuan-source")
    monkeypatch.setattr(sys, "argv", ["car2lego3d", "setup"])
    cli.main()
    assert capsys.readouterr().out.strip() == "hunyuan-source"


def test_frontend_proxy_matches_project_api():
    assert f"http://127.0.0.1:{API_PORT}" in (ROOT / "frontend/next.config.mjs").read_text()
