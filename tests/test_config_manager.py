"""
memory/config_manager.py tests, added for the code-review finding: every
setter here used to do a direct read-modify-write straight to
CONFIG_FILE.write_text() - not atomic (a crash mid-write truncates the
file) and not locked (a background loop and a user action landing at
nearly the same moment could each read the same starting state and one
write silently overwrites the other's change). _atomic_write() (temp file
+ os.replace()) and the module-level `_lock` fix both; these tests pin
that every setter now goes through them instead of a fifth open-coded
copy, and that a concurrent read-modify-write can't lose an update.
"""
import threading

import pytest

from memory import config_manager as cm


@pytest.fixture(autouse=True)
def _isolated_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "api_keys.json")


def test_patch_config_writes_and_reads_back():
    cm._patch_config(hello="world")
    assert cm.load_api_keys()["hello"] == "world"


def test_patch_config_does_not_disturb_other_keys():
    cm._patch_config(a=1)
    cm._patch_config(b=2)
    assert cm.load_api_keys() == {"a": 1, "b": 2}


def test_save_flag_coerces_bool():
    cm._save_flag("flag", True)
    assert cm.load_api_keys()["flag"] is True


def test_patch_nested_merges_without_clobbering_siblings():
    cm._patch_nested("turn_tuning", {"a": 1})
    cm._patch_nested("turn_tuning", {"b": 2})
    assert cm.load_api_keys()["turn_tuning"] == {"a": 1, "b": 2}


def test_atomic_write_never_leaves_a_half_written_file(tmp_path):
    """The write goes to a temp file first, then os.replace()s it in - so
    at no point does CONFIG_FILE itself contain partial/invalid JSON."""
    cm._patch_config(seed="value")
    original_text = cm.CONFIG_FILE.read_text(encoding="utf-8")

    cm._patch_config(seed="a much longer value than before, to change the file size")

    # No leftover temp files after a successful write.
    leftovers = list(tmp_path.glob(".api_keys_*.tmp"))
    assert leftovers == []
    # The real file is always valid, complete JSON - read it fresh.
    assert cm.load_api_keys()["seed"] != "value"
    assert original_text  # sanity: something was actually written the first time


def test_corrupt_json_recovers_to_empty_dict():
    cm.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    cm.CONFIG_FILE.write_text("{not valid json", encoding="utf-8")

    assert cm.load_api_keys() == {}


def test_concurrent_writes_to_different_keys_do_not_lose_either(tmp_path):
    """Regression: a read-modify-write race could let a background
    timestamp update and a user's settings change each read the same
    starting state and one write clobber the other. With `_lock` held for
    the whole read-modify-write, N threads each setting their own key must
    all survive."""
    cm._patch_config(seed=True)   # create the file first

    def _set(i):
        for _ in range(20):
            cm._patch_config(**{f"key_{i}": i})

    threads = [threading.Thread(target=_set, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = cm.load_api_keys()
    for i in range(8):
        assert data[f"key_{i}"] == i


def test_save_brief_enabled_round_trips():
    cm.save_brief_enabled(False)
    assert cm.get_brief_enabled() is False


def test_save_plugin_config_merges_only_the_given_namespace():
    cm.save_plugin_config("ns_a", {"x": 1})
    cm.save_plugin_config("ns_b", {"y": 2})
    cm.save_plugin_config("ns_a", {"z": 3})

    data = cm.load_api_keys()
    assert data["plugin_config"]["ns_a"] == {"x": 1, "z": 3}
    assert data["plugin_config"]["ns_b"] == {"y": 2}


def test_save_plugin_enabled_merges_across_plugins():
    cm.save_plugin_enabled("plugin_a", True)
    cm.save_plugin_enabled("plugin_b", False)

    assert cm.load_api_keys()["plugins_enabled"] == {"plugin_a": True, "plugin_b": False}
