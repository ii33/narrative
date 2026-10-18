"""Check browser-enabled first launch without a writable user profile."""

import os
import subprocess
import sys


def test_browser_enabled_first_launch_skips_profile_activation(tmp_path):
    # Run in a clean process so Streamlit's global configuration does not leak
    # into the workbench tests. Exercise our CLI and Streamlit's real CLI checks.
    code = r"""
import sys
from click.testing import CliRunner
from streamlit import config
from streamlit.runtime import credentials
from streamlit.web import bootstrap, cli as streamlit_cli, skills
from narrative import cli

def forbidden(*args, **kwargs):
    raise AssertionError("First launch must not activate or write profile credentials")

credentials._check_credential_file_exists = lambda: False
credentials.Credentials.get_current = forbidden
credentials.email_prompt = forbidden
skills._nudge_dismissed_marker_path = forbidden
skills.are_skills_installed = forbidden
seen = []
def server(*args, **kwargs):
    assert config.get_option("server.headless") is False
    assert config.get_option("server.showEmailPrompt") is False
    assert config.get_option("browser.gatherUsageStats") is False
    assert config.get_option("logger.hideWelcomeMessage") is True
    # The real framework gates must suppress both terminal and browser nudges
    # before inspecting or writing user/project skill directories.
    assert skills.nudge_suppression_reason() == "welcome_hidden"
    assert skills.should_show_skills_nudge() is False
    bootstrap._maybe_print_skills_recommendation()
    seen.append(True)
bootstrap.run = server
def child(command, **kwargs):
    result = CliRunner().invoke(streamlit_cli.main, command[3:])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "Email:" not in result.output
    return result.exit_code
cli.subprocess.call = child
sys.argv = ["narrative", "--db", sys.argv[1], "ui"]
try:
    cli.main()
except SystemExit as exc:
    assert exc.code == 0
assert seen == [True]
print("Browser-enabled startup skips profile activation")
"""
    env = {**os.environ, "STREAMLIT_SERVER_HEADLESS": "false"}
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "book.sqlite3")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skips profile activation" in result.stdout
