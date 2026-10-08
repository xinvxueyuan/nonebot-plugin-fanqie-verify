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
    from collections.abc import Mapping, Sequence

    from nonebot.adapters.onebot.v11 import Bot

#: localstore 配置目录下的文件名。
NOTICES_FILENAME = "fanqie_verification_notices.toml"

#: localstore 使用的插件名（与 policy.py 一致）。
_PLUGIN_NAME = "nonebot_plugin_fanqie_verify"

#: 默认内容（空绑定）。
DEFAULT_NOTICES: dict[str, Any] = {"groups": {}}

#: 单条公告列表里最多展示多少条（防止一张图几十米长）。
MAX_LISTED_NOTICES = 30

#: 每个群**最多**能绑定的验证公告条数。
#:
#: ⚠️ 这是 **QQ 的平台限制**：QQ 后来把「带『需要确认』的群公告」限制为**每群最多 1 个**
#: （2026-10-08 用户发现）。超出后第 2 条的已读名单不再收集 —— 而
#: ``confirm_required`` 字段仍返回 true，于是插件会以为它可绑。
#:
#: 不设上限的后果**很严重**：闸门判定是「绑定公告必须**全部**读齐」，第 2 条的 acklist
#: 永远拿不到人 → 所有新成员永久卡在「未确认阅读群公告」（实测：某群绑 2 条后
#: 0/4 个成员读齐过，其中一条 0 人已读）。
MAX_BOUND_NOTICES = 1

#: 文本预览长度上限。
PREVIEW_LEN = 60


class BindLimitError(ValueError):
    """绑定条数超过 QQ 平台允许的上限（见 :data:`MAX_BOUND_NOTICES`）。

    单独定类而不直接抛 ``ValueError``：调用方需要把「条数超限」与其它非法输入区分开，
    而 ``ValueError`` 说明不了是哪一种。
    """

    def __init__(self, count: int) -> None:
        """按实际收到的条数生成提示（消息留在异常类里，便于统一措辞）。"""
        super().__init__(f"最多 {MAX_BOUND_NOTICES} 条（QQ 限制），收到 {count} 条")


@dataclass(frozen=True, slots=True)
class GroupNotice:
    """一条群公告（``_get_group_notice`` 的元素）。

    Attributes:
        notice_id: 公告 id（客户端隐藏，只有接口能拿到）。
        text: 公告正文纯文本。
        publish_time: 发布时间（unix 秒）。
        confirm_required: 是否要求成员**确认阅读**（QQ 的「需要确认」开关）。
        send_new_member: 是否勾了「**发给新成员**」（``settings.send_new_member``，
            协议端实现是 ``feed.type === 20``）。
        pinned: 是否置顶。
        is_show_edit_card: 是否显示「引导修改群名片」。
        sender_id: 发布者 QQ。

    """

    notice_id: str
    text: str = ""
    publish_time: int = 0
    confirm_required: bool = False
    #: ⚠️ 与 ``confirm_required`` **同为必填条件**，见 :func:`is_bindable`。
    send_new_member: bool = False
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
        send_new_member=bool(settings.get("send_new_member")),
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
) -> tuple[list[str], list[str], list[str]]:
    """设置某个群的验证公告（**覆盖式**，最多 :data:`MAX_BOUND_NOTICES` 条）。

    语义（用户 2026-10-08 拍板）：**新绑覆盖旧的** —— QQ 只允许 1 条带「需要确认」的
    公告，所以「追加」没有意义、还会把第 2 条变成永远读不齐、把所有人拦在门外。
    管理员直接发「设为验证公告 <序号>」即可换成另一条，不必先取消。

    Args:
        group_id: 群号。
        notice_ids: 期望绑定的公告 id（≤ :data:`MAX_BOUND_NOTICES` 条；调用方**必须**
            先做数量校验，这里的超限报错是最后一道防线）。

    Returns:
        ``(新绑上的 id, 本来就在的 id, 被顶掉的旧 id)``。

    Raises:
        BindLimitError: 传入条数超过 :data:`MAX_BOUND_NOTICES`（QQ 平台限制）。

    """
    unique = list(dict.fromkeys(notice_ids))
    if len(unique) > MAX_BOUND_NOTICES:
        raise BindLimitError(len(unique))

    bindings = await load_bindings()
    current = list(bindings.get(group_id, ()))

    wanted = set(unique)
    already = [n for n in unique if n in current]
    added = [n for n in unique if n not in current]
    replaced = [n for n in current if n not in wanted]

    if not added and not replaced:
        return [], already, []

    bindings[group_id] = tuple(unique)
    await save_bindings(bindings)
    return added, already, replaced


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
    """入群验证的公告闸门：该成员是否已确认阅读群内绑定的公告。

    降级（都返回 ``blocked=False`` 并在 ``detail["skipped"]`` 里说明）：
    未启用 / 该群未绑定 / 接口调用失败 / **历史遗留的多条绑定**。

    历史遗留（``> MAX_BOUND_NOTICES`` 条，来自限制收紧之前绑的）：只读齐不了，
    因为超出上限的那些公告根本不收集回执 —— 若照旧按「全部读齐」判定，
    **所有人会被永久拦住**（生产实测 0/4 人读齐过）。故此时降级放行 + 告警，
    由管理员把绑定收敛到 1 条后再恢复强制。
    """
    from ...core.config import plugin_config

    if not plugin_config.fanqie_notice_gate_enabled:
        return NoticeGate(blocked=False, detail={"skipped": "disabled"})

    bindings = await load_bindings()
    notice_ids = bindings.get(group_id, ())
    if not notice_ids:
        return NoticeGate(blocked=False, detail={"skipped": "no_binding"})

    if len(notice_ids) > MAX_BOUND_NOTICES:
        logger.warning(
            "群 {} 绑定了 {} 条验证公告，超过 QQ 限制（{} 条）—— 本次闸门降级放行；"
            "请用「取消验证公告」把绑定收敛到 {} 条",
            group_id,
            len(notice_ids),
            MAX_BOUND_NOTICES,
            MAX_BOUND_NOTICES,
        )
        return NoticeGate(
            blocked=False,
            detail={"skipped": "legacy_multi", "bound": list(notice_ids)},
        )

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


@dataclass(frozen=True, slots=True)
class ConfirmedNotice:
    """一条**已确认阅读**的公告（用于验证通过回执）。"""

    notice_id: str
    #: 与「获取群公告列表」一致的展示序号；拿不到公告列表时为空串。
    label: str = ""
    preview: str = ""


#: 闸门「未校验」原因 → 对外说明（卡片与纯文本同口径）。
_SKIPPED_TEXT: dict[str, str] = {
    "disabled": "本群公告检查已关闭（无需确认公告）。",
    "no_binding": "本群未设置验证公告（无需确认公告）。",
    "api_error": "公告接口暂不可用，本次未校验公告阅读情况。",
    "legacy_multi": "本群绑定了多条验证公告（超出 QQ 限制），本次未校验公告阅读情况。",
}


@dataclass(frozen=True, slots=True)
class NoticeSummary:
    """闸门结果的对外摘要 —— 让「公告有没有被确认」这件事**看得见**。

    2026-10-07 用户指出：验证通过的回执里完全没提公告，成员与管理员都无从判断
    「未确认阅读公告就不能通过」这条规则到底生效没有。
    """

    bound: int = 0
    confirmed: tuple[ConfirmedNotice, ...] = ()
    #: 降级原因：``disabled`` / ``no_binding`` / ``api_error``；None 表示本次真的校了。
    skipped: str | None = None

    @property
    def enforced(self) -> bool:
        """本次验证是否**真的**执行了公告校验（非降级且有绑定）。"""
        return self.skipped is None and self.bound > 0

    def describe(self) -> str:
        """一行说明，卡片与纯文本回退共用（保证两种媒介口径一致）。"""
        if self.skipped:
            # 「未校验」的几种原因分别说清（查表而不是罗列 if，分支数才有上限）
            return _SKIPPED_TEXT.get(self.skipped, _SKIPPED_TEXT["no_binding"])
        if not self.enforced:
            return _SKIPPED_TEXT["no_binding"]
        labels = [item.label for item in self.confirmed if item.label]
        if labels:
            return f"已确认阅读公告 {'、'.join(labels)}（本群共要求 {self.bound} 条）。"
        return f"已确认阅读公告 {len(self.confirmed)}/{self.bound} 条。"


async def build_notice_summary(bot: Bot, group_id: int, user_id: int) -> NoticeSummary:
    """复查闸门并把结果整理成 :class:`NoticeSummary`（供通过回执与事件明细）。

    序号取自**与「获取群公告列表」同一套顺序**，所以卡片上的编号与管理员当时看到的一致；
    公告列表取不到时退化为「只报条数」，不猜序号。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。

    Returns:
        闸门摘要；任何异常都收敛为「未校验」而不是抛（回执路径不能因它失败）。

    """
    gate = await check_notice_read(bot, group_id, user_id)
    skipped = gate.detail.get("skipped")
    if skipped:
        return NoticeSummary(skipped=str(skipped))

    bound = tuple(str(item) for item in (gate.detail.get("bound") or ()))
    read_ids = tuple(str(item) for item in (gate.detail.get("read") or ()))

    listing = await fetch_group_notices(bot, group_id)
    index_map = notice_index_map(listing) if listing is not None else {}
    by_id = {notice.notice_id: notice for notice in listing or []}

    confirmed = tuple(
        ConfirmedNotice(
            notice_id=notice_id,
            label=notice_label(index_map, notice_id),
            preview=by_id[notice_id].preview if notice_id in by_id else "",
        )
        for notice_id in read_ids
    )
    return NoticeSummary(bound=len(bound), confirmed=confirmed)


def is_bindable(notice: GroupNotice) -> tuple[bool, str | None]:
    """这条公告能不能拿来当验证公告。

    **两个条件缺一不可**（平台语义，用户 2026-10-08 说明 + 协议端实现佐证）：

    1. 「需要确认」（``settings.confirm_required``）；
    2. 「**发给新成员**」（``settings.send_new_member``）—— **只有勾了它，QQ 才会在
       发布/更新公告时把公告发到群里、并开始收集确认数据**；没勾的话
       ``confirm_required`` 即使为 true 也**不生效**，已读名单永远为空。

    第 2 条是不设就会「静默全拦」的那种坑：公告看起来可绑（``confirm_required=true``），
    绑上后 ``acklist`` 却永远查不到任何人 → 闸门把**所有**新成员挡在门外。
    生产实测就撞到过（某群绑 2 条，其中一条对 4 个成员 0 次已读、0/4 人读齐）。

    Returns:
        ``(可否绑定, 不可绑定的原因)``。

    """
    if not notice.confirm_required:
        return False, "该公告未开启「需要确认」（无已读名单可查）"
    if not notice.send_new_member:
        return False, "该公告未勾选「发给新成员」（不勾则确认不生效、收不到回执）"
    return True, None


def bindable_badge(notice: GroupNotice) -> tuple[str, bool]:
    """列表里给这条公告的短角标 ``(文本, 是否警示色)``。

    把「为什么不能绑」说出来 —— 只说「未开确认」会把「没勾发给新成员」这条也盖进去，
    而两者的修法不同（后者要去公告设置里重新发布/更新并勾上「发给新成员」）。
    """
    if notice.confirm_required and notice.send_new_member:
        return "可作验证公告", False
    if not notice.confirm_required:
        return "未开确认", True
    return "未发给新成员", True


def notice_index_map(notices: Sequence[GroupNotice]) -> dict[str, int]:
    """公告 id → **显示序号**（从 1 开始，顺序即列表展示顺序）。

    公告 id 是长串，在群里手抄/回填都不可靠，因此对外一律用短序号；序号**只是位置的
    别名**，绑定仍存真实 ``notice_id``（顺序变化不会污染已存数据）。

    Args:
        notices: 该群当前公告列表（顺序即展示顺序）。

    Returns:
        ``{notice_id: 序号}``。

    """
    return {notice.notice_id: index for index, notice in enumerate(notices, start=1)}


def notice_label(index_map: Mapping[str, int], notice_id: str) -> str:
    """公告的**对外显示标签**：能算出序号时用 ``#N``，否则回退公告 id。

    用户 2026-10-07 实机反馈：列表里每项的**大标题**是那串长公告 id，太难读 ——
    要的是 ``#1``、``#2`` 这种「数据库映射编号」。故凡是要把公告指给用户看的地方
    （列表主文本、绑定/取消回执、闸门拦截图、通过回执）统一走这里。

    序号算不出来（拿不到公告列表 / 公告已被删除）时**回退原 id**：这时 id 是唯一
    能定位它的东西，编造一个序号反而会指错。

    Args:
        index_map: :func:`notice_index_map` 的结果。
        notice_id: 真实公告 id。

    Returns:
        ``#N`` 或原样 ``notice_id``。

    """
    number = index_map.get(notice_id)
    return f"#{number}" if number else notice_id


def resolve_notice_tokens(
    notices: Sequence[GroupNotice], tokens: Sequence[str]
) -> tuple[list[str], list[str]]:
    """把「序号 或 公告 id」的混合输入解析成真实 ``notice_id``。

    规则（用户 2026-10-07 拍板）：token 是纯数字**且落在 ``1..len(notices)``** 时按
    **序号**取；否则**原样透传**（当作公告 id）。

    透传这一条是刻意的逃生通道：已从群里删除的公告不在当前列表中，序号自然算不出来，
    但它的 id 仍必须能用于**单独解绑**（纯序号方案做不到，只能「全部」清空）。

    Args:
        notices: 该群当前公告列表（顺序即「获取群公告列表」里的展示顺序）。
        tokens: 用户输入（可混用序号与 id）。

    Returns:
        ``(按序号解析出的 notice_id 列表（去重保序）, 其余原样透传的 token 列表)``。

    """
    resolved: list[str] = []
    passthrough: list[str] = []
    for raw in tokens:
        token = raw.strip()
        if not token:
            continue
        index = _as_list_index(token, len(notices))
        if index is not None:
            notice_id = notices[index - 1].notice_id
            if notice_id not in resolved:
                resolved.append(notice_id)
            continue
        if token not in passthrough:
            passthrough.append(token)
    return resolved, passthrough


def _as_list_index(token: str, count: int) -> int | None:
    """Token 若可作**列表序号**则返回序号（1 起），否则返回 None。"""
    if not token.isdigit():
        return None
    try:
        value = int(token)
    except ValueError:  # pragma: no cover - isdigit 已挡住
        return None
    return value if 1 <= value <= count else None


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
    "MAX_BOUND_NOTICES",
    "MAX_LISTED_NOTICES",
    "NOTICES_FILENAME",
    "PREVIEW_LEN",
    "BindLimitError",
    "ConfirmedNotice",
    "GroupNotice",
    "NoticeGate",
    "NoticeSummary",
    "acklist_user_ids",
    "bind_notices",
    "bindable_badge",
    "build_notice_summary",
    "check_notice_read",
    "fetch_acklist",
    "fetch_group_notices",
    "is_bindable",
    "load_bindings",
    "notice_index_map",
    "notice_label",
    "parse_notice",
    "resolve_bindable",
    "resolve_notice_tokens",
    "save_bindings",
    "set_path_override",
    "unbind_notices",
]
