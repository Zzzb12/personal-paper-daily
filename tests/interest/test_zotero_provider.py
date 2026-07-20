from datetime import UTC, datetime

from zotero_arxiv_daily.interest.base import ZoteroCollection, ZoteroItem
from zotero_arxiv_daily.interest.zotero import ZoteroInterestProvider


NOW = datetime(2026, 7, 20, tzinfo=UTC)
INCLUDES = (
    "PaperDaily/00-Seeds/**",
    "PaperDaily/03-Read/**",
    "PaperDaily/04-Favorite/**",
)
EXCLUDES = ("PaperDaily/99-Exclude/**",)


class FakeGateway:
    def __init__(self, collections=(), items=()):
        self.collections = tuple(collections)
        self.items = tuple(items)

    def list_collections(self):
        return self.collections

    def list_items(self):
        return self.items


def collection(key, name, parent=None):
    return ZoteroCollection(key=key, name=name, parent_key=parent)


def item(key="item-1", collections=("topic",), abstract="abstract", title="title", added_at=NOW):
    return ZoteroItem(
        key=key,
        title=title,
        abstract=abstract,
        collection_keys=collections,
        added_at=added_at,
    )


def provider(gateway):
    return ZoteroInterestProvider(gateway, include_paths=INCLUDES, exclude_paths=EXCLUDES)


def standard_collections():
    return (
        collection("root", "PaperDaily"),
        collection("read", "03-Read", "root"),
        collection("topic", "Acceleration", "read"),
        collection("excluded", "99-Exclude", "root"),
        collection("excluded-topic", "Noise", "excluded"),
    )


def test_nested_include_path_is_resolved():
    result = provider(FakeGateway(standard_collections(), (item(),))).read()
    assert [paper.paper_id for paper in result.papers] == ["zotero:item-1"]
    assert result.papers[0].collection_paths == ("PaperDaily/03-Read/Acceleration",)
    assert result.eligible_count == 1


def test_all_approved_include_roots_are_supported():
    collections = [collection("root", "PaperDaily")]
    items = []
    for index, name in enumerate(("00-Seeds", "03-Read", "04-Favorite")):
        parent = f"parent-{index}"
        child = f"child-{index}"
        collections.extend((collection(parent, name, "root"), collection(child, "Topic", parent)))
        items.append(item(f"item-{index}", (child,)))
    result = provider(FakeGateway(collections, items)).read()
    assert [paper.paper_id for paper in result.papers] == ["zotero:item-0", "zotero:item-1", "zotero:item-2"]


def test_exclude_path_wins_over_include():
    result = provider(
        FakeGateway(standard_collections(), (item(collections=("topic", "excluded-topic")),))
    ).read()
    assert result.papers == ()
    assert result.excluded_count == 1


def test_zero_collection_item_is_not_eligible():
    result = provider(FakeGateway(standard_collections(), (item(collections=()),))).read()
    assert result.papers == ()
    assert result.excluded_count == 1


def test_blank_abstract_is_isolated_without_private_text_in_issue():
    result = provider(FakeGateway(standard_collections(), (item(abstract="  "),))).read()
    assert result.papers == ()
    assert result.invalid_count == 1
    assert result.issues[0].code == "invalid_item"
    assert "item-1" not in result.issues[0].message


def test_missing_parent_is_isolated():
    collections = (collection("topic", "Topic", "missing"),)
    result = provider(FakeGateway(collections, (item(),))).read()
    assert result.papers == ()
    assert {issue.code for issue in result.issues} == {"missing_collection"}


def test_parent_cycle_is_isolated():
    collections = (collection("a", "A", "b"), collection("b", "B", "a"))
    result = provider(FakeGateway(collections, (item(collections=("a",)),))).read()
    assert result.papers == ()
    assert {issue.code for issue in result.issues} == {"collection_cycle"}


def test_output_and_fingerprint_are_independent_of_gateway_order():
    collections = standard_collections()
    items = (item("item-b"), item("item-a"))
    first = provider(FakeGateway(collections, items)).read()
    second = provider(FakeGateway(tuple(reversed(collections)), tuple(reversed(items)))).read()
    assert first == second
    assert len(first.corpus_fingerprint) == 64
