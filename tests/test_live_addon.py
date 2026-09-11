"""App packaging and startup checks; no paid API calls or Home Assistant actions."""

import hashlib
from io import BytesIO
import json
from pathlib import Path
import tarfile

from aiohttp.test_utils import TestClient, TestServer
import pytest

from special_agent_live import install_source, run


def valid_options(**changes):
    return {"api_key": "test-openai-key", "device_token": "test_device_token_" * 3, **changes}


def test_home_assistant_options_use_supervisor_proxy_and_preserve_agent_identity():
    settings = run.settings_from_options(valid_options(
        backend="home-assistant", agent_id="conversation.special_agent_2", room="Office",
        idle_timeout=30, max_duration=300,
    ), {"SUPERVISOR_TOKEN": "supervisor-test-token", "HA_TOKEN": "must-not-use", "HA_URL": "https://wrong.invalid"})
    assert settings.ha_url + "/api/conversation/process" == "http://supervisor/core/api/conversation/process"
    assert settings.ha_token == "supervisor-test-token"
    assert settings.ha_agent_id == "conversation.special_agent_2"
    assert (settings.room, settings.bind, settings.port) == ("Office", "0.0.0.0", 8099)
    assert (settings.idle_timeout, settings.max_duration) == (30, 300)
    assert "supervisor-test-token" not in repr(settings)
    assert "test-openai-key" not in repr(settings)
    assert settings.device_token not in repr(settings)


def test_demo_defaults_do_not_use_supervisor_access():
    settings = run.settings_from_options(valid_options(), {"SUPERVISOR_TOKEN": "unused"})
    assert (settings.backend, settings.room, settings.ha_token) == ("demo", "Office", "")
    assert (settings.idle_timeout, settings.max_duration) == (90, 600)


@pytest.mark.parametrize("options, field", [
    (valid_options(api_key=None), "api_key"),
    (valid_options(api_key="test-secret\nheader"), "api_key"),
    (valid_options(api_key=""), "api_key"),
    (valid_options(device_token="short-secret"), "device_token"),
    (valid_options(device_token="x" * 32 + "&other=value"), "device_token"),
    (valid_options(device_token="x" * 257), "device_token"),
    (valid_options(backend="unknown-secret-backend"), "backend"),
    (valid_options(agent_id=""), "agent_id"),
    (valid_options(room="x" * 121), "room"),
    (valid_options(idle_timeout=True), "idle_timeout"),
    (valid_options(idle_timeout=9), "idle_timeout"),
    (valid_options(max_duration=1801), "max_duration"),
    (valid_options(max_duration="600"), "max_duration"),
])
def test_invalid_options_fail_before_server_start_without_echoing_values(options, field):
    with pytest.raises(ValueError, match=field) as error:
        run.settings_from_options(options, {})
    for secret in (options["api_key"], options["device_token"]):
        if secret:
            assert secret not in str(error.value)


def test_home_assistant_backend_requires_supervisor_provided_token():
    with pytest.raises(ValueError, match="SUPERVISOR_TOKEN"):
        run.settings_from_options(valid_options(backend="home-assistant"), {"HA_TOKEN": "not-supervisor"})


def test_startup_reads_options_and_disables_token_bearing_access_logs(monkeypatch, tmp_path):
    options_file = tmp_path / "options.json"
    options_file.write_text(json.dumps(valid_options()))
    monkeypatch.setattr(run, "OPTIONS_FILE", options_file)
    monkeypatch.setattr(run, "create_app", lambda settings: settings)
    calls = []
    monkeypatch.setattr(run.web, "run_app", lambda app, **kwargs: calls.append((app, kwargs)))
    assert run.main() == 0
    assert len(calls) == 1
    assert calls[0][1] == {"host": "0.0.0.0", "port": 8099, "access_log": None}
    assert calls[0][0].api_key == valid_options()["api_key"]


@pytest.mark.parametrize("content", ["{ malformed and secret", "[]", '{"api_key": "test-secret-key"}'])
def test_startup_fails_cleanly_for_bad_configuration(monkeypatch, tmp_path, caplog, content):
    options_file = tmp_path / "options.json"
    options_file.write_text(content)
    monkeypatch.setattr(run, "OPTIONS_FILE", options_file)
    monkeypatch.setattr(run.web, "run_app", lambda *args, **kwargs: pytest.fail("Server must not start"))
    assert run.main() == 1
    assert "secret" not in caplog.text


async def test_addon_settings_start_shared_app_with_health_endpoint_without_cloud_session():
    settings = run.settings_from_options(valid_options(), {})
    async with TestClient(TestServer(run.create_app(settings))) as client:
        response = await client.get("/health")
        assert response.status == 200
        assert await response.json() == {
            "model": "gpt-live-1", "backend": "demo", "device_connected": False, "audio_active": False,
            "last_stop_reason": None,
        }


def source_archive(*, linked_file=None):
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as source:
        for name in (*install_source.RUNTIME_FILES, "../../unexpected-file", "secrets.env"):
            member = tarfile.TarInfo(f"special_agent-{install_source.REVISION}/{name}")
            content = ("content of " + name).encode()
            if name == linked_file:
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                source.addfile(member)
            else:
                member.size = len(content)
                source.addfile(member, BytesIO(content))
    return buffer.getvalue()


def test_source_integrity_is_checked_before_any_files_are_written(tmp_path):
    assert len(install_source.REVISION) == 40
    assert len(install_source.ARCHIVE_SHA256) == 64
    with pytest.raises(ValueError, match="SHA-256"):
        install_source.install_archive(source_archive(), tmp_path)
    assert not list(tmp_path.rglob("*"))


def test_verified_source_installs_only_allowlisted_runtime_files(monkeypatch, tmp_path):
    archive = source_archive()
    monkeypatch.setattr(install_source, "ARCHIVE_SHA256", hashlib.sha256(archive).hexdigest())
    install_source.install_archive(archive, tmp_path)
    installed = {str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*") if path.is_file()}
    assert installed == set(install_source.RUNTIME_FILES)
    for name in installed:
        assert (tmp_path / name).read_text() == "content of " + name


def test_source_links_cannot_be_followed_even_in_verified_archive(monkeypatch, tmp_path):
    archive = source_archive(linked_file=install_source.RUNTIME_FILES[-1])
    monkeypatch.setattr(install_source, "ARCHIVE_SHA256", hashlib.sha256(archive).hexdigest())
    with pytest.raises(ValueError, match="archive member"):
        install_source.install_archive(archive, tmp_path)
    assert not list(tmp_path.rglob("*"))
