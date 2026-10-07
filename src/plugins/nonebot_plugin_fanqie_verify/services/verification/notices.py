"""群公告「已读确认」校验与绑定。

依赖 LLBot **v8.3.0+** 新增的两个 OneBot 动作：

* ``_get_group_notice``       → 群公告列表（含隐藏的 ``notice_id``）
* ``_get_group_notice_acklist`` → 某条公告的**已读/未读名单**

## 为什么需要它

QQ 的群公告 id 在客户端是**隐藏的**，而「谁确认阅读了公告」只能按 id 查。
所以运维流程是：先用「获取群公告列表」把 id 打出来 → 用「设为验证公告」绑定
→ 入群验证时按绑定查该成员的阅读状态。

## 绑定存储：独立 TOML，**不动策略文件**

绑定的公告 id 落 ``fanqie_verification_notices.toml``（localstore 配置目录），
而不是写进 ``fanqie_verification_policy.toml``：

* 策略文件是**手工维护**的，群节点下是 ``[[...authors]]`` 数组表；往里塞标量键
  得注意 TOML 的「数组表之后键归属」规则，程序回写还容易把人的排版冲掉。
* 公告绑定是**运行时增删**的东西（和「发布一条新公告就要重新绑」同寿命），
  与「白名单」这种低频人工配置的维护节奏不同。

## 降级语义（用户 2026-10-07 拍板）

齿轮是**硬闸门但可降级**：未读指定公告 → 判不通过；但

* 该群**没绑定**任何公告；
* 或**调用 API 失败**（LLBot 掉线/版本不支持/权限不足）

时**不拦**，只在流程事件里记一笔 ``verify.notice_gate`` 明细 ——
否则一次 LLBot 抽风就会把**所有**新成员挡在门外。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nonebot import logger

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot

#: localstore 配置目录下的文件名。
NOTICES_FILENAME = "fanqie_verification_notices.toml"

#: localstore 使用的插件名（与 policy.py 一致）。
_PLUGIN_NAME = "nonebot_plugin_fanqie_verify"

#: 默认内容（空绑定）。
DEFAULT_NOTICES: dict[str, Any] = {"groups": {}}

#: 单条公告列表里最多展示多少条（防止一张图几十米长）。
MAX_LISTED_NOTICES = 30

#: 文本预览长度上限。
PREVIEW_LEN = 60


@dataclass(frozen=True, slots=True)
class GroupNotice:
    """一条群公告（``_get_group_notice`` 的元素）。

    Attributes:
        notice_id: 公告 id（客户端隐藏，只有接口能拿到）。
        text: 公告正文纯文本。
        publish_time: 发布时间（unix 秒）。
        confirm_required: 是否要求成员**确认阅读**（QQ 的「需要确认」开关）。
        pinned: 是否置顶。
        is_show_edit_card: 是否显示「引导修改群名片」。
        sender_id: 发布者 QQ。

    """

    notice_id: str
    text: str = ""
    publish_time: int = 0
    confirm_required: bool = False
    pinned: bool = False
    is_show_edit_card: bool = False
    sender_id: int = 0

    @property
    def preview(self) -> str:
        """单行正文预览（超长截断）。"""
        flat = " ".join(self.text.split())
        if len(flat) <= PREVIEW_LEN:
            return flat
        return flat[:PREVIEW_LEN] + "…"


@dataclass(frozen=True, slots=True)
class NoticeGate:
    """入群验证里公告闸门的判定结果。

    Attributes:
        blocked: 是否判不通过。
        reason: 判不通过的原因（``blocked`` 为 False 时为 None）。
        detail: 写进流程事件的明细。

    """

    blocked: bool
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _settings_of(raw: dict[str, Any]) -> dict[str, Any]:
    """取公告的 ``settings`` 表（结构变动时返回空表，不让它抛）。"""
    settings = raw.get("settings")
    return settings if isinstance(settings, dict) else {}


def parse_notice(raw: Any) -> GroupNotice | None:
    """把接口返回的一条原始公告解析成 :class:`GroupNotice`；无效返回 ``None``。

    ``notice_id`` 缺失或为空视作无效 —— 拿不到 id 就没法查已读名单，
    留在列表里只会让人误以为能绑定。
    """
    if not isinstance(raw, dict):
        return None
    notice_id = raw.get("notice_id")
    if notice_id is None or not str(notice_id).strip():
        return None

    message = raw.get("message")
    text = ""
    if isinstance(message, dict):
        text = str(message.get("text") or "")

    settings = _settings_of(raw)
    try:
        publish_time = int(raw.get("publish_time") or 0)
    except (TypeError, ValueError):
        publish_time = 0
    try:
        sender_id = int(raw.get("sender_id") or 0)
    except (TypeError, ValueError):
        sender_id = 0

    return GroupNotice(
        notice_id=str(notice_id).strip(),
        text=text,
        publish_time=publish_time,
        confirm_required=bool(settings.get("confirm_required")),
        pinned=bool(settings.get("pinned")),
        is_show_edit_card=bool(settings.get("is_show_edit_card")),
        sender_id=sender_id,
    )


def _notices_path() -> Path:
    """绑定文件的路径（localstore 配置目录，与策略文件同一惯例）。"""
    from nonebot_plugin_localstore import get_config_file

    path = get_config_file(_PLUGIN_NAME, NOTICES_FILENAME)
    # 自定义路径仅用于测试/迁移（与策略文件的 fanqie_verification_policy_path 对位）。
    override = _PATH_OVERRIDE["path"]
    return Path(override) if override is not None else path


#: 测试/迁移用的路径覆盖（内存态，不落配置；用 dict 持有以免 ``global``）。
_PATH_OVERRIDE: dict[str, Path | None] = {"path": None}


def set_path_override(path: str | Path | None) -> None:
    """覆盖绑定文件路径（测试用）。传 ``None`` 恢复默认。"""
    _PATH_OVERRIDE["path"] = Path(path) if path is not None else None


async def load_bindings() -> dict[int, tuple[str, ...]]:
    """读取全部「群 → 已绑定的公告 id」绑定。

    文件缺失/损坏时返回空绑定并告警（**不抛**）：绑定读不出来只应导致闸门降级，
    不该让整个插件起不来。
    """
    from ...database.toml_store import load_toml_dict_async

    try:
        data = await load_toml_dict_async(_notices_path(), default=DEFAULT_NOTICES)
    except Exception:  # noqa: BLE001 - 绑定读坏只降级，不能让插件起不来
        logger.exception("读取公告绑定失败（本次闸门将降级放行）")
        return {}

    groups = data.get("groups")
    if not isinstance(groups, dict):
        return {}

    result: dict[int, tuple[str, ...]] = {}
    for raw_group_id, raw_entry in groups.items():
        try:
            group_id = int(str(raw_group_id).strip())
        except (TypeError, ValueError):
            logger.warning("公告绑定里出现非法群号 {}，已忽略", raw_group_id)
            continue
        ids: list[str] = []
        entries = raw_entry.get("notices") if isinstance(raw_entry, dict) else raw_entry
        if isinstance(entries, (list, tuple)):
            for item in entries:
                text = str(item).strip()
                if text and text not in ids:
                    ids.append(text)
        if ids:
            result[group_id] = tuple(ids)
    return result


async def save_bindings(bindings: dict[int, tuple[str, ...]]) -> None:
    """原子写入绑定（只保留非空群）。"""
    from ...database.toml_store import write_toml_dict_file_async

    groups: dict[str, Any] = {
        str(group_id): {"notices": list(ids)}
        for group_id, ids in sorted(bindings.items())
        if ids
    }
    await write_toml_dict_file_async(_notices_path(), {"groups": groups})


async def bind_notices(
    group_id: int, notice_ids: list[str]
) -> tuple[list[str], list[str]]:
    """把公告 id 追加绑定到某个群。

    Returns:
        ``(新增的 id, 已存在而跳过的 id)``。

    """
    bindings = await load_bindings()
    current = list(bindings.get(group_id, ()))
    added: list[str] = []
    already: list[str] = []
    for notice_id in notice_ids:
        if notice_id in current:
            already.append(notice_id)
        else:
            current.append(notice_id)
            added.append(notice_id)
    if added:
        bindings[group_id] = tuple(current)
        await save_bindings(bindings)
    return added, already


async def unbind_notices(
    group_id: int, notice_ids: list[str]
) -> tuple[list[str], list[str]]:
    """从某个群解绑指定公告。

    Returns:
        ``(实际解绑的 id, 本来就没绑的 id)``。

    """
    bindings = await load_bindings()
    current = list(bindings.get(group_id, ()))
    removed = [n for n in notice_ids if n in current]
    missing = [n for n in notice_ids if n not in current]
    if removed:
        left = [n for n in current if n not in set(removed)]
        if left:
            bindings[group_id] = tuple(left)
        else:
            bindings.pop(group_id, None)
        await save_bindings(bindings)
    return removed, missing


async def fetch_group_notices(bot: Bot, group_id: int) -> list[GroupNotice] | None:
    """取群公告列表；**调用失败返回 ``None``**（与「群里真没公告」的 ``[]`` 区分）。"""
    try:
        raw = await bot.call_api("_get_group_notice", group_id=group_id)
    except Exception as exc:  # noqa: BLE001 - 接口失败按「不可用」处理并降级
        logger.warning("获取群 {} 公告列表失败: {}", group_id, exc)
        return None
    if not isinstance(raw, (list, tuple)):
        logger.warning("群 {} 公告列表返回结构异常: {!r}", group_id, type(raw).__name__)
        return None
    parsed = [parse_notice(item) for item in raw]
    return [item for item in parsed if item is not None]


async def fetch_acklist(
    bot: Bot,
    group_id: int,
    notice_id: str,
    *,
    unread: bool = False,
) -> list[dict[str, Any]] | None:
    """取某条公告的已读（``unread=False``）或未读名单；失败返回 ``None``。"""
    try:
        raw = await bot.call_api(
            "_get_group_notice_acklist",
            group_id=group_id,
            notice_id=notice_id,
            list_type="unack" if unread else "ack",
        )
    except Exception as exc:  # noqa: BLE001 - 接口失败按「不可用」处理并降级
        logger.warning(
            "获取公告 {} 的{}名单失败（群 {}）: {}",
            notice_id,
            "未读" if unread else "已读",
            group_id,
            exc,
        )
        return None
    if not isinstance(raw, (list, tuple)):
        logger.warning("公告名单返回结构异常: {!r}", type(raw).__name__)
        return None
    return [item for item in raw if isinstance(item, dict)]


def acklist_user_ids(entries: list[dict[str, Any]]) -> set[int]:
    """从名单里取出 ``user_id`` 集合（容错 int/str/脏数据）。

    非字典项（接口偶发夹带字符串等）与 ``user_id`` 缺失/非数字的项一律跳过 ——
    名单脏数据不该让整个闸门判定崩掉。
    """
    out: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = entry.get("user_id")
        try:
            out.add(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    return out


async def check_notice_read(bot: Bot, group_id: int, user_id: int) -> NoticeGate:
    """入群验证的公告闸门：该成员是否已确认阅读群内**全部**绑定公告。

    降级（都返回 ``blocked=False`` 并在 ``detail["skipped"]`` 里说明）：
    未启用 / 该群未绑定 / 接口调用失败。
    """
    from ...core.config import plugin_config

    if not plugin_config.fanqie_notice_gate_enabled:
        return NoticeGate(blocked=False, detail={"skipped": "disabled"})

    bindings = await load_bindings()
    notice_ids = bindings.get(group_id, ())
    if not notice_ids:
        return NoticeGate(blocked=False, detail={"skipped": "no_binding"})

    unread: list[str] = []
    read: list[str] = []
    for notice_id in notice_ids:
        entries = await fetch_acklist(bot, group_id, notice_id)
        if entries is None:
            # ⚠️ 调用失败**不拦**：否则 LLBot 抽风会把所有新人挡在门外。
            return NoticeGate(
                blocked=False,
                detail={"skipped": "api_error", "notice_id": notice_id},
            )
        if user_id in acklist_user_ids(entries):
            read.append(notice_id)
        else:
            unread.append(notice_id)

    if unread:
        return NoticeGate(
            blocked=True,
            reason="未确认阅读群公告",
            detail={"unread": unread, "read": read, "bound": list(notice_ids)},
        )
    return NoticeGate(blocked=False, detail={"read": read, "bound": list(notice_ids)})


def is_bindable(notice: GroupNotice) -> tuple[bool, str | None]:
    """这条公告能不能拿来当验证公告。

    用户 2026-10-07 要求：**必须校验该公告是否带确认环节**，不然「已读名单」
    根本没有意义（没开确认的公告，QQ 不收集阅读回执）。

    Returns:
        ``(可否绑定, 不可绑定的原因)``。

    """
    if not notice.confirm_required:
        return False, "该公告未开启「需要确认」（无已读名单可查）"
    return True, None


def resolve_bindable(
    notices: list[GroupNotice], requested: list[str]
) -> tuple[list[GroupNotice], list[tuple[str, str]]]:
    """把请求绑定的 id 分成「可绑定」与「被跳过（含原因）」。

    Args:
        notices: 该群当前公告列表。
        requested: 管理员请求绑定的公告 id。

    Returns:
        ``(可绑定的公告, [(id, 跳过原因), ...])``。

    """
    by_id = {notice.notice_id: notice for notice in notices}
    ok: list[GroupNotice] = []
    skipped: list[tuple[str, str]] = []
    for notice_id in requested:
        notice = by_id.get(notice_id)
        if notice is None:
            skipped.append((notice_id, "该群找不到这条公告"))
            continue
        bindable, reason = is_bindable(notice)
        if not bindable:
            skipped.append((notice_id, reason or "不可绑定"))
            continue
        ok.append(notice)
    return ok, skipped


__all__ = [
    "DEFAULT_NOTICES",
    "MAX_LISTED_NOTICES",
    "NOTICES_FILENAME",
    "PREVIEW_LEN",
    "GroupNotice",
    "NoticeGate",
    "acklist_user_ids",
    "bind_notices",
    "check_notice_read",
    "fetch_acklist",
    "fetch_group_notices",
    "is_bindable",
    "load_bindings",
    "parse_notice",
    "resolve_bindable",
    "save_bindings",
    "set_path_override",
    "unbind_notices",
]
