"""放行策略（verification_policy.toml）测试。

策略以群为节点：每个群下可配置多个作者，每个作者下可配置多个作品。
群未配置作者节点时放行；配置了则只校验作者。

"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.plugins.nonebot_plugin_fanqie_verify.services.verification import (
    ExtractedField,
    PolicyConfigError,
    ReadingEvidence,
    load_policy,
    policy as policy_module,
    reload_policy,
)
from src.plugins.nonebot_plugin_fanqie_verify.services.verification.policy import (
    SUPPORTED_ELEMENTS,
    AuthorEntry,
    GroupPolicy,
    VerificationPolicy,
    names_conflict,
    reviewer_author_reason,
)

_GROUP = 868258211
_OTHER_GROUP = 456


def _evidence(author: str = "阿百川大鬼") -> ReadingEvidence:
    """构造书评详情页的阅读证据。"""
    return ReadingEvidence(
        is_self_review=True,
        book_name=ExtractedField("综漫：吉他雇佣兵无法找到归宿？", "b", 1.0),
        author=ExtractedField(author, "a", 1.0),
    )


def _policy_with_groups(groups: dict[int, GroupPolicy]) -> VerificationPolicy:
    return VerificationPolicy(
        require_all=False,
        required_elements=frozenset({"book_name", "author"}),
        groups=groups,
    )


def test_supported_elements() -> None:
    """受支持的元素应覆盖书评页字段。"""
    assert "book_name" in SUPPORTED_ELEMENTS
    assert "author" in SUPPORTED_ELEMENTS
    assert "reader_name" in SUPPORTED_ELEMENTS
    assert "rating" in SUPPORTED_ELEMENTS
    assert len(SUPPORTED_ELEMENTS) == 7


def test_group_without_config_passes() -> None:
    """群未配置作者节点：放行（宽松模式）。"""
    policy = _policy_with_groups({})
    result = policy.check(_evidence(), _GROUP)
    assert result.passed is True


def test_author_hit_passes() -> None:
    """作者命中该群白名单：通过。"""
    group = GroupPolicy(
        group_id=_GROUP,
        authors=(
            AuthorEntry(
                name="阿百川大鬼", books=frozenset({"综漫：吉他雇佣兵无法找到归宿？"})
            ),
            AuthorEntry(name="刘慈欣", books=frozenset({"三体"})),
        ),
    )
    policy = _policy_with_groups({_GROUP: group})

    assert policy.check(_evidence("阿百川大鬼"), _GROUP).passed is True
    assert policy.check(_evidence("刘慈欣"), _GROUP).passed is True


def test_author_miss_rejects() -> None:
    """作者未命中该群白名单：拒绝。"""
    group = GroupPolicy(
        group_id=_GROUP,
        authors=(AuthorEntry(name="刘慈欣", books=frozenset({"三体"})),),
    )
    policy = _policy_with_groups({_GROUP: group})

    result = policy.check(_evidence("阿百川大鬼"), _GROUP)
    assert result.passed is False
    assert result.author_allowed is False
    assert result.reason == "作者不在白名单"


def test_author_without_books_still_checked() -> None:
    """作者节点未配置作品列表时，仍只校验作者名。"""
    group = GroupPolicy(
        group_id=_GROUP,
        authors=(AuthorEntry(name="阿百川大鬼"),),
    )
    policy = _policy_with_groups({_GROUP: group})

    # 作品不参与判定，作者命中即通过
    assert policy.check(_evidence("阿百川大鬼"), _GROUP).passed is True
    assert policy.check(_evidence("刘慈欣"), _GROUP).passed is False


def test_different_groups_isolated() -> None:
    """不同群的作者白名单互不影响。"""
    group_a = GroupPolicy(
        group_id=_GROUP,
        authors=(AuthorEntry(name="阿百川大鬼"),),
    )
    group_b = GroupPolicy(
        group_id=_OTHER_GROUP,
        authors=(AuthorEntry(name="刘慈欣"),),
    )
    policy = _policy_with_groups({_GROUP: group_a, _OTHER_GROUP: group_b})

    assert policy.check(_evidence("阿百川大鬼"), _GROUP).passed is True
    assert policy.check(_evidence("阿百川大鬼"), _OTHER_GROUP).passed is False
    assert policy.check(_evidence("刘慈欣"), _OTHER_GROUP).passed is True
    assert policy.check(_evidence("刘慈欣"), _GROUP).passed is False


def test_group_author_names_property() -> None:
    """GroupPolicy.author_names 应返回作者名集合。"""
    group = GroupPolicy(
        group_id=_GROUP,
        authors=(
            AuthorEntry(name="阿百川大鬼"),
            AuthorEntry(name="刘慈欣"),
        ),
    )
    assert group.author_names == frozenset({"阿百川大鬼", "刘慈欣"})
    assert group.is_configured is True


def test_should_monitor_group() -> None:
    """群节点即监控范围：配置了节点的群才监控。"""
    policy = _policy_with_groups({
        _GROUP: GroupPolicy(group_id=_GROUP),
    })
    assert policy.should_monitor_group(_GROUP) is True
    assert policy.should_monitor_group(_OTHER_GROUP) is False


def test_missing_required_element() -> None:
    """缺少必配元素应拒绝。"""
    policy = _policy_with_groups({})
    evidence = ReadingEvidence(
        is_self_review=True,
        book_name=ExtractedField("书", "b", 1.0),
        author=None,
    )
    result = policy.check(evidence, _GROUP)
    assert result.passed is False
    assert "author" in result.missing_elements


def test_load_policy_defaults(tmp_path: Path) -> None:
    """默认策略文件应生成且无群节点。"""
    path = tmp_path / "policy.toml"
    policy = load_policy(path)
    assert path.exists()
    assert policy.require_all is False
    assert policy.required_elements == frozenset({"book_name", "author"})
    assert policy.groups == {}


def test_load_policy_custom(tmp_path: Path) -> None:
    """自定义群节点策略应生效。"""
    path = tmp_path / "custom.toml"
    path.write_text(
        """[verification]
require_all = false
required_elements = ["book_name", "author"]

[verification.groups]

[[verification.groups.868258211.authors]]
name = "阿百川大鬼"
books = ["综漫：吉他雇佣兵无法找到归宿？"]

[[verification.groups.868258211.authors]]
name = "刘慈欣"
books = ["三体", "球状闪电"]

[[verification.groups.456.authors]]
name = "刘慈欣"
""",
        encoding="utf-8",
    )
    policy = load_policy(path)
    group = policy.groups[868258211]
    assert [a.name for a in group.authors] == ["阿百川大鬼", "刘慈欣"]
    assert "综漫：吉他雇佣兵无法找到归宿？" in group.authors[0].books
    assert group.authors[1].books == frozenset({"三体", "球状闪电"})
    assert policy.groups[456].authors[0].books == frozenset()


def test_reload_policy_updates_cache(tmp_path: Path) -> None:
    """reload_policy 应刷新模块级缓存。"""
    path = tmp_path / "reload.toml"
    first = load_policy(path)
    assert first.groups == {}

    path.write_text(
        """[verification]
require_all = false
required_elements = ["book_name", "author"]

[verification.groups]

[[verification.groups.868258211.authors]]
name = "阿百川大鬼"
""",
        encoding="utf-8",
    )
    second = reload_policy(path)
    assert 868258211 in second.groups
    assert reload_policy(path) == second


def test_load_policy_missing_table(tmp_path: Path) -> None:
    """缺少 [verification] 表应抛出配置错误。"""
    path = tmp_path / "bad-root.toml"
    path.write_text("[foo]\nbar = 1\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError):
        load_policy(path)


def test_load_policy_unknown_element(tmp_path: Path) -> None:
    """未知元素名应抛出配置错误。"""
    path = tmp_path / "bad-element.toml"
    path.write_text(
        '[verification]\nrequire_all = false\nrequired_elements = ["nope"]\n',
        encoding="utf-8",
    )
    with pytest.raises(PolicyConfigError):
        load_policy(path)


def test_load_policy_bad_type(tmp_path: Path) -> None:
    """类型错误应抛出配置错误。"""
    path = tmp_path / "bad-type.toml"
    path.write_text(
        '[verification]\nrequire_all = "yes"\n',
        encoding="utf-8",
    )
    with pytest.raises(PolicyConfigError):
        load_policy(path)


def test_load_policy_author_without_name(tmp_path: Path) -> None:
    """作者节点缺少 name 应抛出配置错误。"""
    path = tmp_path / "bad-author.toml"
    path.write_text(
        """[verification]

[verification.groups]

[[verification.groups.868258211.authors]]
books = ["三体"]
""",
        encoding="utf-8",
    )
    with pytest.raises(PolicyConfigError):
        load_policy(path)


def test_load_policy_group_welcome_message(tmp_path: Path) -> None:
    """群节点可配自定义 welcome_message。"""
    path = tmp_path / "welcome.toml"
    path.write_text(
        """[verification]
require_all = false
required_elements = ["book_name", "author"]

[verification.groups]

[verification.groups.868258211]
welcome_message = "本群专属欢迎语"

[[verification.groups.868258211.authors]]
name = "阿百川大鬼"

[[verification.groups.456.authors]]
name = "刘慈欣"
""",
        encoding="utf-8",
    )
    policy = load_policy(path)
    assert policy.groups[868258211].welcome_message == "本群专属欢迎语"
    # 未配置 welcome_message 的群节点回退 None
    assert policy.groups[456].welcome_message is None


def test_load_policy_welcome_message_bad_type(tmp_path: Path) -> None:
    """welcome_message 非字符串应抛出配置错误。"""
    path = tmp_path / "bad-welcome.toml"
    path.write_text(
        """[verification]
require_all = false
required_elements = ["book_name", "author"]

[verification.groups]

[verification.groups.868258211]
welcome_message = 123

[[verification.groups.868258211.authors]]
name = "阿百川大鬼"
""",
        encoding="utf-8",
    )
    with pytest.raises(PolicyConfigError):
        load_policy(path)


# ---------------------------------------------------------------------------
# 9. 边界：书评发布者不能是配置的作者（用户 2026-10-08 要求）
# ---------------------------------------------------------------------------


def _evidence_with_reader(
    author: str = "阿百川大鬼", reader: str | None = None
) -> ReadingEvidence:
    """构造带「书评发布者名」的证据（``reader=None`` 表示没识别出来）。"""
    return ReadingEvidence(
        is_self_review=True,
        book_name=ExtractedField("综漫：吉他雇佣兵无法找到归宿？", "b", 1.0),
        author=ExtractedField(author, "a", 1.0),
        reader_name=ExtractedField(reader, "r", 1.0) if reader is not None else None,
    )


def test_names_conflict_is_exact_after_strip() -> None:
    """判据是「去掉首尾空白后完全相等」——不做包含匹配。"""
    authors = frozenset({"百舸川掮客", "阿百川大鬼"})

    assert names_conflict("百舸川掮客", authors) is True
    assert names_conflict("  百舸川掮客 ", authors) is True
    assert names_conflict("百舸川掮客的小号", authors) is False  # 不做包含匹配
    assert names_conflict("百舸川", authors) is False
    assert names_conflict("", authors) is False
    assert names_conflict("   ", authors) is False
    assert names_conflict(None, authors) is False


def test_check_rejects_when_reviewer_is_configured_author() -> None:
    """发布者命中作者白名单 → 判不通过，原因里点名是哪位作者。"""
    group = GroupPolicy(group_id=_GROUP, authors=(AuthorEntry(name="百舸川掮客"),))
    policy = _policy_with_groups({_GROUP: group})

    ok = _evidence_with_reader(author="百舸川掮客", reader="路过的读者")
    assert policy.check(ok, _GROUP).passed

    result = policy.check(
        _evidence_with_reader(author="百舸川掮客", reader="百舸川掮客"), _GROUP
    )
    assert result.passed is False
    assert result.reviewer_is_author is True
    assert result.reason is not None and "百舸川掮客" in result.reason


def test_check_allows_when_reader_name_missing() -> None:
    """拿不到发布者名时**不拦**：没有可比对的对象，不制造假阴性。"""
    group = GroupPolicy(group_id=_GROUP, authors=(AuthorEntry(name="百舸川掮客"),))
    policy = _policy_with_groups({_GROUP: group})

    # reader_name 不在 required_elements 里，缺失也不算缺元素
    evidence = _evidence_with_reader(author="百舸川掮客", reader=None)
    assert policy.check(evidence, _GROUP).passed is True


def test_check_reviewer_rule_skipped_for_unconfigured_group() -> None:
    """未配置作者节点的群不做该判定（与作者白名单的宽松语义一致）。"""
    policy = _policy_with_groups({})

    assert (
        policy.check(_evidence_with_reader(reader="百舸川掮客"), _GROUP).passed is True
    )


def test_reviewer_author_reason_follows_group_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """仅视觉路径用的入口：按群策略给原因，无冲突/未配置群给 ``None``。"""
    group = GroupPolicy(group_id=_GROUP, authors=(AuthorEntry(name="百舸川掮客"),))
    policy_obj = _policy_with_groups({_GROUP: group})
    monkeypatch.setattr(policy_module, "get_policy", lambda: policy_obj)

    assert reviewer_author_reason(_GROUP, "路过的读者") is None
    assert reviewer_author_reason(_GROUP, None) is None
    assert reviewer_author_reason(_OTHER_GROUP, "百舸川掮客") is None  # 该群未配置

    reason = reviewer_author_reason(_GROUP, "百舸川掮客")
    assert reason is not None and "百舸川掮客" in reason
