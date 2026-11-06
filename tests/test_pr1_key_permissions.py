"""POSIX key-file checks use generated temporary keys, never enrolled credentials."""
import os
import stat
from types import SimpleNamespace

import pytest

from benchlink_sidecar.config import ConfigError
from benchlink_sidecar.pairing import generate_key, load_key, public_key_b64url


@pytest.mark.parametrize("mode", [0o601, 0o640, 0o644, 0o660, 0o666, 0o700])
def test_key_load_refuses_permissions_broader_than_0600(tmp_path, mode):
    path = tmp_path / "fixture.pem"
    generate_key(str(path))
    path.chmod(mode)
    with pytest.raises(ConfigError, match="0600"):
        load_key(str(path))


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_owner_only_keys_are_usable(tmp_path, mode):
    path = tmp_path / "fixture.pem"
    original = generate_key(str(path))
    path.chmod(mode)
    assert public_key_b64url(load_key(str(path))) == public_key_b64url(original)


def test_key_load_refuses_symlink_even_to_private_file(tmp_path):
    path = tmp_path / "fixture.pem"
    generate_key(str(path))
    link = tmp_path / "link.pem"
    link.symlink_to(path)
    with pytest.raises(ConfigError):
        load_key(str(link))


def test_permissions_checked_on_the_same_descriptor_that_is_read(tmp_path, monkeypatch):
    path = tmp_path / "fixture.pem"
    original = generate_key(str(path))
    replacement = tmp_path / "replacement.pem"
    generate_key(str(replacement))
    replacement.chmod(0o644)
    real_fstat, checked = os.fstat, []

    def replace_after_check(fd):
        info = real_fstat(fd)
        assert stat.S_ISREG(info.st_mode)
        checked.append(fd)
        os.replace(replacement, path)
        return info

    from benchlink_sidecar import pairing
    isolated_os = SimpleNamespace(**vars(os))
    isolated_os.fstat = replace_after_check
    monkeypatch.setattr(pairing, "os", isolated_os)
    actual = load_key(str(path))
    assert len(checked) == 1
    assert public_key_b64url(actual) == public_key_b64url(original)


def test_key_load_rejects_non_regular_file_without_blocking(tmp_path):
    path = tmp_path / "fixture.fifo"
    os.mkfifo(path, 0o600)
    with pytest.raises(ConfigError):
        load_key(str(path))
