import os
from pathlib import Path

from aggregator import paths, store
from aggregator.sources import course_pages, quest, uw_browser


def test_tests_use_a_temp_data_dir():
    data = Path(os.environ["WATERLOO_DEADLINES_DATA"])
    assert paths.DATA_DIR == data
    for p in (store.DEFAULT_DB, uw_browser.PROFILE_DIR, quest.LAST_HTML, course_pages.SAVE_DIR):
        assert data in p.parents
    assert paths.LEGACY_DATA_DIR not in paths.DATA_DIR.parents


def test_default_is_outside_the_project(monkeypatch):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\\Users\\x\\AppData\\Local")
    d = paths.default_data_dir()
    assert paths.PROJECT_ROOT not in d.parents and d.name.lower().startswith("waterloo")


def test_migration_moves_once_and_leaves_the_old_folder(tmp_path):
    old, new = tmp_path / "data", tmp_path / "local"
    (old / "uw_profile" / "Default").mkdir(parents=True)
    (old / "uw_profile" / "Default" / "Cookies").write_bytes(b"secret")
    (old / "aggregator.db").write_bytes(b"db")
    assert sorted(paths.migrate_legacy(old, new)) == ["aggregator.db", "uw_profile"]
    assert (new / "uw_profile" / "Default" / "Cookies").read_bytes() == b"secret"
    assert old.exists() and not any(old.iterdir())  # emptied, not deleted
    assert "can be deleted" in paths.legacy_note(new)
    (old / "stray.txt").write_text("x")
    assert paths.migrate_legacy(old, new) == []  # new dir isn't empty any more: never again
    assert "couldn't be moved" in paths.legacy_note(new)
    old.joinpath("stray.txt").unlink()
    old.rmdir()
    assert paths.legacy_note(new) == ""  # old folder gone: no note


def test_no_migration_into_a_dir_in_use(tmp_path):
    old, new = tmp_path / "data", tmp_path / "local"
    old.mkdir()
    (old / "aggregator.db").write_bytes(b"old")
    new.mkdir()
    (new / "aggregator.db").write_bytes(b"current")
    assert paths.migrate_legacy(old, new) == []
    assert (new / "aggregator.db").read_bytes() == b"current" and (old / "aggregator.db").exists()


def test_migration_is_all_or_nothing_when_a_file_is_open(tmp_path):
    old, new = tmp_path / "data", tmp_path / "local"
    (old / "uw_profile").mkdir(parents=True)
    (old / "aggregator.db").write_bytes(b"db")
    with open(old / "aggregator.db", "rb"):  # a running copy of the app holds the DB open
        moved = paths.migrate_legacy(old, new)
    if moved == []:  # Windows: open files can't be renamed → nothing moves
        assert (old / "uw_profile").exists() and not new.exists()
    else:  # POSIX lets open files move; then everything moved together
        assert sorted(moved) == ["aggregator.db", "uw_profile"]


def test_store_python_gets_its_real_package_folder(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(paths, "_package_family", lambda: "PythonSoftwareFoundation.Python.3.12_abc")
    assert paths.default_data_dir() == (tmp_path / "Packages" / "PythonSoftwareFoundation.Python.3.12_abc"
                                        / "LocalCache" / "Local" / "WaterlooDeadlines")
    monkeypatch.setattr(paths, "_package_family", lambda: None)
    assert paths.default_data_dir() == tmp_path / "WaterlooDeadlines"
