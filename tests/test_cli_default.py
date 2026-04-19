from typer.testing import CliRunner

from jobscraper import cli


runner = CliRunner()


def test_invoking_jobformer_without_subcommand_opens_start_menu(monkeypatch):
    called = {"value": False}

    def fake_start():
        called["value"] = True

    monkeypatch.setattr(cli, "start", fake_start)

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0
    assert called["value"] is True


def test_help_does_not_open_start_menu(monkeypatch):
    called = {"value": False}

    def fake_start():
        called["value"] = True

    monkeypatch.setattr(cli, "start", fake_start)

    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert called["value"] is False
    assert "start" in result.stdout


def test_start_menu_title_is_jobformer():
    result = runner.invoke(cli.app, [], input="5\n")

    assert result.exit_code == 0
    assert "jobformer" in result.stdout
    assert "jobformer start" not in result.stdout
