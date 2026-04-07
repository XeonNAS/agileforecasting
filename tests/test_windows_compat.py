"""Windows compatibility tests.

These tests run on all platforms (Linux CI, Windows CI, developer machines).
They use monkeypatching and mocking to simulate Windows-specific behaviour
rather than requiring a real Windows host.

Coverage:
  - secure_store.default_paths() Windows path resolution (APPDATA)
  - secure_store.default_paths() Linux/macOS path resolution (~/.config)
  - secure_store migration from old ~/.config path on Windows
  - chart_export._find_non_snap_chrome() on Windows (no crash, correct paths)
  - chart_export._is_snap_path() always False on Windows-style paths
  - chart_export BrowserNotAvailableError cross-platform message content
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# secure_store — platform path resolution
# ---------------------------------------------------------------------------


class TestSecureStoreWindowsPaths:
    def test_windows_uses_appdata(self, monkeypatch, tmp_path):
        """On Windows, default_paths() should use %APPDATA%\\agileforecasting."""
        fake_appdata = str(tmp_path / "AppData" / "Roaming")
        monkeypatch.setenv("APPDATA", fake_appdata)
        with patch.object(sys, "platform", "win32"):
            from agile_mc.secure_store import default_paths

            paths = default_paths()
        expected = Path(fake_appdata) / "agileforecasting"
        assert paths.config_dir == expected
        assert ".config" not in str(paths.config_dir)

    def test_windows_appdata_fallback_when_env_missing(self, monkeypatch, tmp_path):
        """When %APPDATA% is unset on Windows, fall back to AppData\\Roaming under home."""
        monkeypatch.delenv("APPDATA", raising=False)
        fake_home = tmp_path / "Users" / "test"
        fake_home.mkdir(parents=True)
        with patch.object(sys, "platform", "win32"):
            with patch.object(Path, "home", return_value=fake_home):
                from agile_mc.secure_store import default_paths

                paths = default_paths()
        assert paths.config_dir == fake_home / "AppData" / "Roaming" / "agileforecasting"

    def test_linux_uses_dot_config(self, monkeypatch, tmp_path):
        """On Linux, default_paths() should use ~/.config/agileforecasting."""
        monkeypatch.delenv("APPDATA", raising=False)
        with patch.object(sys, "platform", "linux"):
            with patch("os.path.expanduser", return_value=str(tmp_path)):
                from agile_mc.secure_store import default_paths

                paths = default_paths()
        assert ".config" in str(paths.config_dir)
        assert "agileforecasting" in str(paths.config_dir)
        assert "AppData" not in str(paths.config_dir)

    def test_enc_file_is_inside_config_dir(self, monkeypatch, tmp_path):
        """enc_file should always sit directly inside config_dir."""
        monkeypatch.setenv("APPDATA", str(tmp_path))
        with patch.object(sys, "platform", "win32"):
            from agile_mc.secure_store import default_paths

            paths = default_paths()
        assert paths.enc_file.parent == paths.config_dir

    def test_windows_migration_from_old_dot_config_path(self, monkeypatch, tmp_path):
        """On Windows, existing settings under ~/.config are migrated to %APPDATA%."""
        fake_home = tmp_path / "Users" / "test"
        fake_home.mkdir(parents=True)
        fake_appdata = tmp_path / "AppData" / "Roaming"
        fake_appdata.mkdir(parents=True)

        # Create an existing settings file at the old Linux-style Windows path.
        old_config_dir = fake_home / ".config" / "agileforecasting"
        old_config_dir.mkdir(parents=True)
        old_enc = old_config_dir / "ado_settings.enc.json"
        old_enc.write_text('{"v": "1", "salt_b64": "x", "token_b64": "y"}', encoding="utf-8")

        monkeypatch.setenv("APPDATA", str(fake_appdata))
        with patch.object(sys, "platform", "win32"):
            with patch("os.path.expanduser", return_value=str(fake_home)):
                from agile_mc.secure_store import default_paths

                default_paths()

        # The canonical Windows path should now contain the migrated file.
        new_enc = fake_appdata / "agileforecasting" / "ado_settings.enc.json"
        assert new_enc.exists(), "Settings were not migrated from ~/.config to %APPDATA%"
        # Old file must still exist (migration is non-destructive).
        assert old_enc.exists(), "Migration must not delete the original file"

    def test_migration_candidates_windows_order(self, monkeypatch, tmp_path):
        """_migration_candidates() returns Windows-specific sources before common ones."""
        from agile_mc.secure_store import _migration_candidates

        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        with patch.object(sys, "platform", "win32"):
            with patch("os.path.expanduser", return_value=str(tmp_path)):
                candidates = _migration_candidates("agileforecasting")

        # First candidate on Windows should reference the old ~/.config path.
        assert ".config" in str(candidates[0])

    def test_migration_candidates_linux_old_app_name(self, monkeypatch, tmp_path):
        """On Linux, _migration_candidates() checks the old app-name path."""
        from agile_mc.secure_store import _migration_candidates

        with patch.object(sys, "platform", "linux"):
            with patch("os.path.expanduser", return_value=str(tmp_path)):
                candidates = _migration_candidates("agileforecasting")

        assert any("agile-montecarlo" in str(c) for c in candidates)


# ---------------------------------------------------------------------------
# chart_export — Windows browser discovery
# ---------------------------------------------------------------------------


class TestWindowsChromeFallback:
    def test_snap_path_always_false_for_windows_style_paths(self):
        """Windows paths never contain /snap/, so _is_snap_path is always False."""
        from agile_mc.chart_export import _is_snap_path

        assert _is_snap_path(r"C:\Program Files\Google\Chrome\Application\chrome.exe") is False
        assert _is_snap_path(r"C:\Users\test\AppData\Local\Google\Chrome\Application\chrome.exe") is False
        assert _is_snap_path("C:/Program Files/Google/Chrome/Application/chrome.exe") is False

    def test_find_chrome_returns_none_gracefully_on_windows_no_browser(self, monkeypatch):
        """_find_non_snap_chrome returns None without errors when no browser is found on Windows."""
        from agile_mc.chart_export import _find_non_snap_chrome

        with patch.object(sys, "platform", "win32"):
            monkeypatch.delenv("LOCALAPPDATA", raising=False)
            with patch("shutil.which", return_value=None):
                with patch("os.path.exists", return_value=False):
                    result = _find_non_snap_chrome()
        assert result is None

    def test_find_chrome_discovers_per_user_install_via_localappdata(self, monkeypatch, tmp_path):
        """_find_non_snap_chrome finds Chrome installed per-user (%LOCALAPPDATA%)."""
        from agile_mc.chart_export import _find_non_snap_chrome

        # Create a fake per-user Chrome installation under tmp_path.
        chrome_exe = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
        chrome_exe.parent.mkdir(parents=True)
        chrome_exe.write_bytes(b"fake chrome binary")

        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        with patch.object(sys, "platform", "win32"):
            with patch("shutil.which", return_value=None):  # not on PATH
                result = _find_non_snap_chrome()

        assert result is not None
        assert str(chrome_exe) == result

    def test_find_chrome_checks_windows_paths_when_localappdata_missing(self, monkeypatch):
        """_find_non_snap_chrome checks Program Files candidates even without %LOCALAPPDATA%."""
        from agile_mc.chart_export import _find_non_snap_chrome

        checked: list[str] = []

        def tracking_exists(p: str) -> bool:
            checked.append(str(p))
            return False

        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        with patch.object(sys, "platform", "win32"):
            with patch("shutil.which", return_value=None):
                with patch("os.path.exists", side_effect=tracking_exists):
                    _find_non_snap_chrome()

        # Must have checked at least one Windows Program Files path.
        assert any("Program Files" in p for p in checked), (
            f"No Windows 'Program Files' paths checked. Paths checked: {checked}"
        )

    def test_find_chrome_linux_paths_not_checked_on_windows(self, monkeypatch):
        """On Windows, Linux fixed paths (/usr/bin/...) must not be checked."""
        from agile_mc.chart_export import _find_non_snap_chrome

        checked: list[str] = []

        def tracking_exists(p: str) -> bool:
            checked.append(str(p))
            return False

        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        with patch.object(sys, "platform", "win32"):
            with patch("shutil.which", return_value=None):
                with patch("os.path.exists", side_effect=tracking_exists):
                    _find_non_snap_chrome()

        linux_paths_checked = [p for p in checked if p.startswith("/usr/")]
        assert linux_paths_checked == [], f"Linux paths must not be checked on Windows: {linux_paths_checked}"

    def test_find_chrome_linux_paths_still_checked_on_linux(self, monkeypatch):
        """On Linux, the existing /usr/bin fixed paths are still checked."""
        from agile_mc.chart_export import _find_non_snap_chrome

        checked: list[str] = []

        def tracking_exists(p: str) -> bool:
            checked.append(str(p))
            return False

        with patch.object(sys, "platform", "linux"):
            with patch("shutil.which", return_value=None):
                with patch("os.path.exists", side_effect=tracking_exists):
                    _find_non_snap_chrome()

        linux_paths_checked = [p for p in checked if p.startswith("/usr/")]
        assert linux_paths_checked, "Expected /usr/bin Chrome paths to be checked on Linux"


# ---------------------------------------------------------------------------
# chart_export — cross-platform error messages
# ---------------------------------------------------------------------------


class TestBrowserNotAvailableErrorMessages:
    def test_first_attempt_error_includes_linux_guidance(self):
        """The 'no fallback browser' error must include Linux install guidance."""
        from agile_mc.chart_export import BrowserNotAvailableError

        # Instantiate directly to check the template message text.
        err = BrowserNotAvailableError(
            "Chart export requires Chrome or Chromium but no usable browser "
            "was found or the available browser failed to start.\n"
            "Recovery options (choose one):\n"
            "  • Set the BROWSER_PATH environment variable to point to an "
            "existing Chrome/Chromium binary and restart the app.\n"
            "  • Run  plotly_get_chrome  in the active venv to download a "
            "bundled browser, then set BROWSER_PATH to the path it prints.\n"
            "  Linux:          sudo apt install chromium  (apt package, not snap)\n"
            "  macOS/Windows:  install Chrome from https://www.google.com/chrome/"
        )
        msg = str(err)
        assert "Linux" in msg
        assert "Windows" in msg or "macOS/Windows" in msg
        assert "BROWSER_PATH" in msg

    def test_retry_error_includes_cross_platform_guidance(self):
        """The retry failure error must mention both Linux and Windows/macOS."""
        from agile_mc.chart_export import BrowserNotAvailableError

        err = BrowserNotAvailableError(
            "Chart export failed on both the initial attempt and the retry.\n"
            "Set BROWSER_PATH to a working Chrome or Chromium binary and restart.\n"
            "  Linux:          sudo apt install chromium  (apt package, not snap)\n"
            "  macOS/Windows:  install Chrome from https://www.google.com/chrome/"
        )
        msg = str(err)
        assert "Linux" in msg
        assert "Windows" in msg or "macOS/Windows" in msg
        assert "BROWSER_PATH" in msg

    def test_browser_not_available_error_is_runtime_error(self):
        """BrowserNotAvailableError must be a RuntimeError subclass."""
        from agile_mc.chart_export import BrowserNotAvailableError

        assert issubclass(BrowserNotAvailableError, RuntimeError)
