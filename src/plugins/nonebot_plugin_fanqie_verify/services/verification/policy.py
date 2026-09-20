"""放行策略的可配置加载器。

放行策略以**群为节点**组织：每个群下可配置多个作者，每个作者下可
配置多个作品（书名）。策略全部收敛到可编辑的 TOML 文件中。

判定规则（按群）：
- 群未配置任何作者节点：放行（宽松模式，仅校验必配元素存在）。
- 群配置了作者节点：提取到的作者必须命中其中一个作者名（只校验
  作者，作品列表 ``books`` 仅作配置参考与审计展示）。

群节点本身即监控范围：配置了节点的群才会执行入群验证。

策略支持通过“重载番茄OCR配置”命令运行时重载（见 :mod:`..handle`）。

策略文件位置优先级：
1. ``plugin_config.fanqie_verification_policy_path`` 显式指定路径；
2. 否则使用 ``nonebot_plugin_localstore`` 的配置目录。

"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ReadingEvidence

_PLUGIN_NAME = "nonebot_plugin_fanqie_verify"
_POLICY_FILENAME = "verification_policy.toml"

# ReadingEvidence 中可参与放行判断的字段。
SUPPORTED_ELEMENTS: frozenset[str] = frozenset({
    "reader_name",
    "publish_time",
    "rating",
    "read_duration",
    "book_name",
    "author",
    "review_text",
})

_DEFAULT_POLICY: dict[str, Any] = {
    "verification": {
        # 为 True 时要求 SUPPORTED_ELEMENTS 全部匹配；为 False 时仅按
        # required_elements 判断。
        "require_all": False,
        # require_all 为 False 时，必须匹配的元素列表。
        "required_elements": ["book_name", "author"],
        # 群节点：群号 → 作者列表。
        "groups": {},
    }
}


class PolicyConfigError(ValueError):
    """放行策略 TOML 配置无效时抛出。"""


@dataclass(frozen=True, slots=True)
class PolicyCheckResult:
    """放行策略检查结果。

    Attributes:
        passed: 是否满足放行条件。
        missing_elements: 缺失的元素名列表。
        author_allowed: 作者是否命中白名单（群未配置时为 True）。
        reason: 未通过时的原因；通过时为 ``None``。

    """

    passed: bool
    missing_elements: tuple[str, ...] = ()
    author_allowed: bool = True
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorEntry:
    """群下的一个作者节点。

    Attributes:
        name: 作者名。
        books: 该作者的作品列表（配置参考与审计展示，不参与判定）。

    """

    name: str
    books: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class GroupPolicy:
    """一个群节点的放行策略。

    Attributes:
        group_id: 群号。
        authors: 该群允许的作者白名单；为空表示未配置（放行）。
        welcome_message: 该群自定义的验证引导文案；为 ``None`` 时回退
            到全局默认 ``fanqie_welcome_message``。

    """

    group_id: int
    authors: tuple[AuthorEntry, ...] = ()
    welcome_message: str | None = None

    @property
    def author_names(self) -> frozenset[str]:
        """作者名集合。"""
        return frozenset(entry.name for entry in self.authors)

    @property
    def is_configured(self) -> bool:
        """该群是否配置了作者节点。"""
        return bool(self.authors)


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """编译后的放行策略（以群为节点）。

    Attributes:
        require_all: 是否要求全部受支持元素都匹配。
        required_elements: ``require_all`` 为 False 时要求匹配的元素集合。
        groups: 群号 → 群策略。

    """

    require_all: bool
    required_elements: frozenset[str]
    groups: dict[int, GroupPolicy] = field(default_factory=dict)

    def _required_names(self) -> frozenset[str]:
        """本次判断实际要求匹配的元素名集合。"""
        if self.require_all:
            return SUPPORTED_ELEMENTS
        return self.required_elements

    def group_policy(self, group_id: int) -> GroupPolicy | None:
        """返回指定群的策略；未配置时返回 ``None``。"""
        return self.groups.get(group_id)

    def should_monitor_group(self, group_id: int) -> bool:
        """该群是否属于监控范围。

        群节点即监控范围：只有配置了节点的群才执行入群验证。

        Args:
            group_id: 群号。

        Returns:
            群是否在监控范围内。

        """
        return group_id in self.groups

    def check(
        self,
        evidence: ReadingEvidence,
        group_id: int,
    ) -> PolicyCheckResult:
        """检查证据是否满足该群的放行策略。

        Args:
            evidence: 提取出的阅读证据。
            group_id: 群号。

        Returns:
            放行策略检查结果。

        """
        missing = tuple(
            sorted(
                element
                for element in self._required_names()
                if getattr(evidence, element) is None
            )
        )
        if missing:
            return PolicyCheckResult(
                passed=False,
                missing_elements=missing,
                author_allowed=True,
                reason=f"缺少元素：{', '.join(missing)}",
            )

        group = self.group_policy(group_id)
        if group is None or not group.is_configured:
            # 群未配置作者节点：放行（宽松模式）。
            return PolicyCheckResult(passed=True)

        author_allowed = self.is_author_allowed(evidence, group)
        if not author_allowed:
            return PolicyCheckResult(
                passed=False,
                missing_elements=(),
                author_allowed=False,
                reason="作者不在白名单",
            )

        return PolicyCheckResult(passed=True)

    def is_author_allowed(
        self,
        evidence: ReadingEvidence,
        group: GroupPolicy,
    ) -> bool:
        """作者是否命中该群的作者白名单。

        只校验作者，作品列表不参与判定。

        Args:
            evidence: 提取出的阅读证据。
            group: 群策略。

        Returns:
            作者是否在白名单内。

        """
        author = getattr(evidence, "author", None)
        if author is None:
            return False
        return author.value in group.author_names


_policy_cache: VerificationPolicy | None = None


def get_policy() -> VerificationPolicy:
    """返回缓存的放行策略；首次调用时从配置文件加载。"""
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = load_policy()
    return _policy_cache


def load_policy(path: str | Path | None = None) -> VerificationPolicy:
    """从指定（或默认）路径加载并校验放行策略。

    Args:
        path: 策略 TOML 文件路径；为 ``None`` 时使用默认位置。

    Returns:
        编译后的放行策略。

    Raises:
        PolicyConfigError: 配置内容无效时。

    """
    resolved = _resolve_policy_path(path)
    _ensure_policy_file(resolved)
    data = _load_policy_data(resolved)
    return _build_policy(data)


def reload_policy(path: str | Path | None = None) -> VerificationPolicy:
    """重新加载策略并刷新缓存，返回新策略（热更新用）。"""
    global _policy_cache
    _policy_cache = load_policy(path)
    return _policy_cache


def _resolve_policy_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    from ...core.config import plugin_config

    configured = plugin_config.fanqie_verification_policy_path
    if configured:
        return Path(configured)
    from nonebot_plugin_localstore import get_config_file

    return get_config_file(_PLUGIN_NAME, _POLICY_FILENAME)


def _ensure_policy_file(path: Path) -> None:
    from ...database.toml_store import ensure_toml_dict_file_sync

    ensure_toml_dict_file_sync(path, _DEFAULT_POLICY)


def _load_policy_data(path: Path) -> dict[str, Any]:
    from ...database.toml_store import load_toml_dict_sync

    loaded = load_toml_dict_sync(path, default=_DEFAULT_POLICY)
    if "verification" not in loaded:
        raise PolicyConfigError(f"策略配置缺失 [verification] 表，请检查 {path}")
    return _deep_merge(_DEFAULT_POLICY, loaded)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并 ``override`` 到 ``base``，嵌套字典逐层合并，其余直接覆盖。"""
    merged = base.copy()
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _build_policy(data: dict[str, Any]) -> VerificationPolicy:
    verification = _require_table(data, "verification")
    require_all = _require_bool(verification, "require_all")
    required = frozenset(_require_str_list(verification, "required_elements"))
    unknown = required - SUPPORTED_ELEMENTS
    if unknown:
        raise PolicyConfigError(
            f"策略配置出现未知元素名 {sorted(unknown)}，"
            f"支持的元素有 {sorted(SUPPORTED_ELEMENTS)}"
        )
    groups = _build_groups(verification)
    return VerificationPolicy(
        require_all=require_all,
        required_elements=required,
        groups=groups,
    )


def _build_groups(verification: dict[str, Any]) -> dict[int, GroupPolicy]:
    """解析群节点表：``groups`` 下每个键为群号。"""
    groups_raw = verification.get("groups", {})
    if not isinstance(groups_raw, dict):
        raise PolicyConfigError(
            f"策略配置 'groups' 应为表（TOML 字典），实际为 {type(groups_raw).__name__}"
        )
    groups: dict[int, GroupPolicy] = {}
    for raw_group_id, raw_group in groups_raw.items():
        group_id = _parse_group_id(raw_group_id, raw_group)
        authors = _parse_authors(raw_group, group_id)
        welcome_message = _parse_welcome_message(raw_group, group_id)
        groups[group_id] = GroupPolicy(
            group_id=group_id,
            authors=authors,
            welcome_message=welcome_message,
        )
    return groups


def _parse_group_id(raw: Any, raw_group: Any) -> int:
    """解析群号（TOML 表键为字符串）。"""
    try:
        group_id = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise PolicyConfigError(f"策略配置群号无效: {raw!r}") from exc
    if not isinstance(raw_group, dict):
        raise PolicyConfigError(
            f"策略配置群 {group_id} 应为表，实际为 {type(raw_group).__name__}"
        )
    return group_id


def _parse_welcome_message(raw_group: dict[str, Any], group_id: int) -> str | None:
    """解析群节点可选的自定义引导文案（``welcome_message``）。"""
    raw = raw_group.get("welcome_message")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise PolicyConfigError(
            f"策略配置群 {group_id} 的 'welcome_message' 应为非空字符串，"
            f"实际为 {type(raw).__name__}"
        )
    return raw.strip()


def _parse_authors(raw_group: dict[str, Any], group_id: int) -> tuple[AuthorEntry, ...]:
    """解析群下的作者数组（``authors`` 为数组表）。"""
    authors_raw = raw_group.get("authors", [])
    if not isinstance(authors_raw, list):
        raise PolicyConfigError(
            f"策略配置群 {group_id} 的 'authors' 应为数组表，"
            f"实际为 {type(authors_raw).__name__}"
        )
    entries: list[AuthorEntry] = []
    for index, raw_author in enumerate(authors_raw):
        if not isinstance(raw_author, dict):
            raise PolicyConfigError(
                f"策略配置群 {group_id} 第 {index} 个作者应为表，"
                f"实际为 {type(raw_author).__name__}"
            )
        name = raw_author.get("name")
        if not isinstance(name, str) or not name.strip():
            raise PolicyConfigError(
                f"策略配置群 {group_id} 第 {index} 个作者缺少有效 'name'"
            )
        books = _parse_author_books(raw_author.get("books"), group_id, name)
        entries.append(AuthorEntry(name=name.strip(), books=books))
    return tuple(entries)


def _parse_author_books(raw: Any, group_id: int, author_name: str) -> frozenset[str]:
    """解析作者的作品列表（可选）。"""
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise PolicyConfigError(
            f"策略配置群 {group_id} 作者 {author_name!r} 的 'books' 应为字符串数组，"
            f"实际为 {type(raw).__name__}"
        )
    books: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise PolicyConfigError(
                f"策略配置群 {group_id} 作者 {author_name!r} 的 'books' 应全为字符串，"
                f"出现 {type(item).__name__}"
            )
        books.append(item)
    return frozenset(books)


def _require_table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise PolicyConfigError(
            f"策略配置 {key!r} 应为表（TOML 字典），实际为 {type(value).__name__}"
        )
    return value


def _require_bool(table: dict[str, Any], key: str) -> bool:
    value = table.get(key)
    if not isinstance(value, bool):
        raise PolicyConfigError(
            f"策略配置 {key!r} 应为布尔值，实际为 {type(value).__name__}"
        )
    return value


def _require_str_list(table: dict[str, Any], key: str) -> list[str]:
    value = table.get(key)
    if not isinstance(value, list):
        raise PolicyConfigError(
            f"策略配置 {key!r} 应为字符串数组，实际为 {type(value).__name__}"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise PolicyConfigError(
                f"策略配置 {key!r} 应全为字符串，出现 {type(item).__name__}"
            )
        result.append(item)
    return result


__all__ = [
    "SUPPORTED_ELEMENTS",
    "AuthorEntry",
    "GroupPolicy",
    "PolicyCheckResult",
    "PolicyConfigError",
    "VerificationPolicy",
    "get_policy",
    "load_policy",
    "reload_policy",
]
