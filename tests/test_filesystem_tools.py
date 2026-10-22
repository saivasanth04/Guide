"""Real file effects stay inside temporary, explicitly bound workspaces."""

import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ntg_llm.errors import RoutingError
from ntg_llm.tools.filesystem import FilesystemTools, create_filesystem_registry
from ntg_llm.tools.models import ToolError


def call(registry, name, **arguments):
    return registry.execute(name, arguments, call_id="tool_001")


def write(registry, path, content, overwrite=False):
    return call(registry, "write_file", path=path, content=content, overwrite=overwrite)


def test_utf8_nested_create_read_and_deterministic_listing(tmp_path):
    registry = create_filesystem_registry(tmp_path)
    result = write(registry, "nested/café.py", "print('café')\n")
    assert result.ok and result.data.created
    assert result.data.bytes_written == len("print('café')\n".encode())
    read = call(registry, "read_file", path="nested/café.py")
    assert read.ok and read.data.content == "print('café')\n"
    assert read.data.path == "nested/café.py"
    write(registry, "B.txt", "b")
    write(registry, "a.txt", "a")
    listing = call(registry, "list_files", path=".")
    assert [entry.name for entry in listing.data.entries] == ["a.txt", "B.txt", "nested"]
    assert not listing.data.truncated
    assert listing.data.entries[-1].type == "directory"


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "nested/../../escape",
        "/absolute",
        "C:\\absolute",
        "C:relative",
        "\\\\server\\share\\file",
        "\\\\?\\C:\\file",
        "a.txt:secret",
        "NUL",
        "con.py",
        "LPT1.log",
        "trailing.",
        "trailing ",
        "wild*card",
        "a\x00b",
        "a\nb",
        ".env",
        ".ENV.example",
        "x/.env.local",
        "beatapi.txt",
        ".git/config",
        ".ssh/id_rsa",
        "router.sqlite3-wal",
        "state.db",
        "._ntg_tmp_secret",
    ],
)
def test_denied_paths_cannot_be_read_or_written(tmp_path, path):
    registry = create_filesystem_registry(tmp_path)
    for result in (call(registry, "read_file", path=path), write(registry, path, "private")):
        assert not result.ok and result.error.code == "path_denied"
        assert str(tmp_path) not in result.model_dump_json()
    assert list(tmp_path.iterdir()) == []


def test_protected_entries_are_omitted_from_listing(tmp_path):
    for name in (".env", "state.sqlite3", "beatapi.txt", "visible.txt"):
        (tmp_path / name).write_text("secret", encoding="utf-8")
    listing = call(create_filesystem_registry(tmp_path), "list_files", path=".")
    assert [entry.name for entry in listing.data.entries] == ["visible.txt"]


@pytest.mark.parametrize("host,requested", [(False, False), (False, True), (True, False)])
def test_replacement_needs_host_and_request_permission(tmp_path, host, requested):
    registry = create_filesystem_registry(tmp_path, allow_overwrite=host)
    assert write(registry, "file.txt", "original").ok
    result = write(registry, "file.txt", "replacement", overwrite=requested)
    assert not result.ok and result.error.code == "already_exists"
    assert (tmp_path / "file.txt").read_text() == "original"


def test_explicit_replacement_and_payload_bounds(tmp_path):
    registry = create_filesystem_registry(
        tmp_path, allow_overwrite=True, max_read_bytes=4, max_write_bytes=6, max_list_entries=1
    )
    assert write(registry, "one", "12345").ok
    assert call(registry, "read_file", path="one").error.code == "too_large"
    assert write(registry, "two", "éééé").error.code == "too_large"
    result = write(registry, "one", "new", overwrite=True)
    assert result.ok and not result.data.created
    assert (tmp_path / "one").read_text() == "new"
    assert write(registry, "two", "2").ok
    listing = call(registry, "list_files", path=".")
    assert len(listing.data.entries) == 1 and listing.data.truncated


def test_missing_binary_and_wrong_kind_are_safe_errors(tmp_path):
    registry = create_filesystem_registry(tmp_path)
    assert call(registry, "read_file", path="missing").error.code == "not_found"
    (tmp_path / "binary").write_bytes(b"\xff\xfe")
    assert call(registry, "read_file", path="binary").error.code == "unsupported_encoding"
    (tmp_path / "directory").mkdir()
    assert call(registry, "read_file", path="directory").error.code == "path_denied"
    assert write(registry, "directory", "data").error.code == "path_denied"
    assert call(registry, "list_files", path="binary").error.code == "path_denied"


def test_hard_links_are_denied(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("private", encoding="utf-8")
    root = tmp_path / "workspace"
    root.mkdir()
    os.link(outside, root / "linked")
    registry = create_filesystem_registry(root, allow_overwrite=True)
    assert call(registry, "read_file", path="linked").error.code == "path_denied"
    assert write(registry, "linked", "new", True).error.code == "path_denied"
    assert call(registry, "list_files", path=".").data.entries == []
    assert outside.read_text() == "private"


def test_symbolic_links_are_denied_when_host_can_create_them(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("private", encoding="utf-8")
    root = tmp_path / "workspace"
    root.mkdir()
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit creating a native symlink")
    registry = create_filesystem_registry(root)
    assert call(registry, "read_file", path="linked/secret").error.code == "path_denied"
    assert write(registry, "linked/new", "data").error.code == "path_denied"
    assert call(registry, "list_files", path=".").data.entries == []
    assert not (outside / "new").exists()


def test_reparse_attribute_is_denied_even_without_native_link_privileges():
    with pytest.raises(ToolError) as error:
        FilesystemTools._check_info(
            SimpleNamespace(st_file_attributes=0x400, st_mode=0, st_nlink=1)
        )
    assert error.value.code == "path_denied"


def test_publication_failure_preserves_existing_file_and_cleans_staging(tmp_path, monkeypatch):
    registry = create_filesystem_registry(tmp_path, allow_overwrite=True)
    write(registry, "file", "original")

    def fail(*args):
        raise OSError("private-host-path")

    monkeypatch.setattr(os, "replace", fail)
    result = write(registry, "file", "new", True)
    assert result.error.code == "io_error"
    assert (tmp_path / "file").read_text() == "original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file"]
    assert "private-host-path" not in result.model_dump_json()


def test_concurrent_creates_publish_once_without_corruption(tmp_path):
    registry = create_filesystem_registry(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda text: write(registry, "shared", text), ["A" * 2000, "B" * 2000])
        )
    assert sum(r.ok for r in results) == 1
    assert [r.error.code for r in results if not r.ok] == ["already_exists"]
    assert (tmp_path / "shared").read_text() in {"A" * 2000, "B" * 2000}
    assert [p.name for p in tmp_path.iterdir()] == ["shared"]


@pytest.mark.parametrize("operation", ["link", "fsync"])
def test_failed_create_does_not_publish_or_leave_staging(tmp_path, monkeypatch, operation):
    registry = create_filesystem_registry(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("private failure detail")

    monkeypatch.setattr(os, operation, fail)
    result = write(registry, "file", "new content")
    assert result.error.code == "io_error"
    assert list(tmp_path.iterdir()) == []


def test_concurrent_authorized_replacements_never_mix_source(tmp_path):
    registry = create_filesystem_registry(tmp_path, allow_overwrite=True)
    assert write(registry, "shared", "original").ok
    values = ["A" * 2000, "B" * 2000]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda text: write(registry, "shared", text, True), values))
    assert all(result.ok and not result.data.created for result in results)
    assert (tmp_path / "shared").read_text() in values
    assert [path.name for path in tmp_path.iterdir()] == ["shared"]


@pytest.mark.parametrize(
    "option,value",
    [
        ("max_read_bytes", 0),
        ("max_write_bytes", True),
        ("max_list_entries", -1),
        ("allow_overwrite", "true"),
    ],
)
def test_invalid_policy_is_configuration_error(tmp_path, option, value):
    with pytest.raises(RoutingError) as error:
        create_filesystem_registry(tmp_path, **{option: value})
    assert error.value.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_workspace_cannot_be_a_protected_directory(tmp_path):
    root = tmp_path / ".ssh"
    root.mkdir()
    with pytest.raises(RoutingError) as error:
        create_filesystem_registry(root)
    assert error.value.status_code == 400


@pytest.mark.parametrize("root", [r"\\server\share", r"\\?\C:\workspace", "C:relative"])
def test_network_or_ambiguous_roots_are_rejected_before_resolution(root):
    from unittest.mock import patch

    with patch(
        "pathlib.Path.resolve", side_effect=AssertionError("Must not resolve a remote root")
    ):
        with pytest.raises(RoutingError) as error:
            create_filesystem_registry(root)
    assert error.value.status_code == 400
