"""Tests for the OS/2 IPF (INF) container parser."""


def test_header_and_counts(inf_general):
    assert inf_general.h["ID"] == b"HSP"
    assert len(inf_general.topics) == 3224
    assert len(inf_general.dictionary) == 6434


def test_topic_titles_decoded(inf_general):
    # Every topic record yields a (possibly empty) string title.
    assert all(isinstance(t.title, str) for t in inf_general.topics)
    assert any("SERVICE SPECIFICATION" in t.title.upper() for t in inf_general.topics)


def test_slots_lazy_and_cached(inf_general):
    t = inf_general.topics[39]
    s1 = inf_general.get_slots(t)
    s2 = inf_general.get_slots(t)
    assert s1 is s2                      # cached on the topic
    assert s1 and all(hasattr(s, "localdict") and hasattr(s, "text") for s in s1)


def test_local_dict_indices_map_into_global(inf_general):
    """Every local-dict entry indexes a real global dictionary word."""
    t = inf_general.topics[39]
    for slot in inf_general.get_slots(t):
        for gi in slot.localdict:
            assert 0 <= gi < len(inf_general.dictionary)
