"""入群验证流程编排（FR1/FR2/FR5/FR6/FR7/FR8）。

把 OCR 服务层、FR3 信息提取、FR4 综合判断、会话管理与群管理动作
串成一个完整流程。本模块只接收规范化参数，不直接依赖 NoneBot
matcher，便于测试。

"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from nonebot import logger, require

require("nonebot_plugin_orm")
from nonebot.adapters.onebot.v11 import Bot
from nonebot_plugin_orm import get_session

from ...core.config import plugin_config
from ...repositories import message_store as repository
from ...services.ocr import (
    OCRError,
    recognize_image_url_multi,
)
from . import actions, extractor, fusion, judgment, policy, vision
from .session import SessionRecord, get_session_store

if TYPE_CHECKING:
    from .models import ReadingEvidence


def _to_dict(evidence: ReadingEvidence) -> dict:
    """把提取结果转换为可入库的 last_extracted 字典。"""
    return {
        "is_self_review": evidence.is_self_review,
        "reader_name": evidence.reader_name.value if evidence.reader_name else None,
        "book_name": evidence.book_name.value if evidence.book_name else None,
        "author": evidence.author.value if evidence.author else None,
        "rating": evidence.rating.value if evidence.rating else None,
        "publish_time": (
            evidence.publish_time.value if evidence.publish_time else None
        ),
        "read_duration": (
            evidence.read_duration.value if evidence.read_duration else None
        ),
        "review_text": evidence.review_text.value if evidence.review_text else None,
    }


def _group_known_books(group_id: int) -> frozenset[str]:
    """返回该群放行策略中配置的全部白名单书名（无节点时为空集合）。

    供提取器的无冒号书名回退匹配做双向确认：识别出的书名候选必须与
    白名单精确一致才接受，避免误把界面元素当书名。

    """
    group = policy.get_policy().group_policy(group_id)
    if group is None:
        return frozenset()
    return frozenset(book for author in group.authors for book in author.books)


async def start_verification(
    bot: Bot,
    *,
    group_id: int,
    user_id: int,
) -> SessionRecord | None:
    """FR1：为新成员开启验证会话并发送引导消息。

    若策略配置了监控群聊白名单且该群不在其中，则跳过本次验证。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。

    Returns:
        创建的会话记录；未启用验证时返回 ``None``。

    """
    from ...hooks.adapters import resolve_platform_context

    if not policy.get_policy().should_monitor_group(group_id):
        logger.info(
            "群 {} 不在监控范围，跳过新成员 {} 的验证",
            group_id,
            user_id,
        )
        return None

    platform_context = resolve_platform_context(bot)
    record = get_session_store().start(
        group_id=str(group_id),
        user_id=str(user_id),
        bot_id=str(getattr(bot, "self_id", "")),
        platform_id=platform_context.platform_id if platform_context else "qq",
        adapter_id=platform_context.adapter_id if platform_context else "~onebot.v11",
        protocol_id=platform_context.protocol_id if platform_context else None,
    )

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info(
            "引导前检查：成员 {} 已不在群 {} 中，结束会话 trace={}",
            user_id,
            group_id,
            record.trace_id,
        )
        get_session_store().remove(str(group_id), str(user_id))
        return None
    if member.is_muted:
        record = (
            get_session_store().set_muted(str(group_id), str(user_id), is_muted=True)
            or record
        )

    await _persist_session(record)
    await _record_event(record, event_type="verify.start")
    await actions.send_guide(bot, group_id, user_id)
    logger.info(
        "新成员 {} 进入群 {} 验证流程，截止 {} trace={}",
        user_id,
        group_id,
        record.expires_at.isoformat(),
        record.trace_id,
    )
    return record


async def handle_submission(
    bot: Bot,
    *,
    group_id: int,
    user_id: int,
    image_url: str | None,
    reply_message_id: int | None = None,
) -> str:
    """FR2~FR8：处理新成员提交的阅读截图。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。
        image_url: 图片 URL（无可用 URL 时视为下载失败）。
        reply_message_id: 触发本次提交的消息 id，记录后供超时通报等
            后续群消息引用原消息。

    Returns:
        面向用户的反馈消息。

    """
    store = get_session_store()
    if not store.try_claim(str(group_id), str(user_id)):
        return "当前没有待处理的验证请求。"
    if reply_message_id:
        store.set_last_image_message(str(group_id), str(user_id), reply_message_id)
    try:
        record = store.get(str(group_id), str(user_id))
        if record is None or record.status != "waiting":
            return "当前没有待处理的验证请求。"

        member = await actions.get_member_info(bot, group_id, user_id)
        if member is None:
            logger.info(
                "提交处理：成员 {} 已不在群 {} 中，结束会话 trace={}",
                user_id,
                group_id,
                record.trace_id,
            )
            store.remove(str(group_id), str(user_id))
            return "你已不在群聊中，无需验证。"
        if member.is_muted and not record.is_muted:
            record = (
                store.set_muted(str(group_id), str(user_id), is_muted=True) or record
            )
            await _persist_session(record)

        if not image_url:
            return await _handle_download_failure(group_id, user_id)

        evidence = (
            await _recognize_and_extract(image_url, group_id, record)
            if plugin_config.fanqie_ocr_enabled
            else None
        )
        if evidence is None:
            return await _handle_vision_fallback(
                bot, group_id, user_id, image_url, record
            )
        await _persist_last_extracted(record, evidence)

        if not evidence.is_sufficient:
            logger.info(
                "信息不足 trace={} group={} user={}", record.trace_id, group_id, user_id
            )
            return await _handle_insufficient(bot, group_id, user_id)

        policy_check = policy.get_policy().check(evidence, group_id)
        verdict = judgment.judge_evidence(evidence)
        reject_reason = (
            policy_check.reason if not policy_check.passed else verdict.reason
        )

        if reject_reason is not None:
            logger.info(
                "验证拒绝 group={} user={} reason={} trace={}",
                group_id,
                user_id,
                reject_reason,
                record.trace_id,
            )
            return await _handle_reject(bot, group_id, user_id, evidence, reject_reason)

        return await _vision_review(bot, group_id, user_id, image_url, record)
    finally:
        store.release(str(group_id), str(user_id))


async def _handle_vision_fallback(
    bot: Bot,
    group_id: int,
    user_id: int,
    image_url: str,
    record: SessionRecord,
) -> str:
    """OCR 识别不出（或 OCR 已停用）时改用视觉模型看图判定。"""
    verdict = await vision.vision_fallback(image_url, group_id)
    if verdict is not None:
        fallback_evidence = vision.verdict_to_evidence(verdict)
        await _persist_last_extracted(record, fallback_evidence)
        await _record_event(
            record,
            event_type="verify.ocr",
            success=True,
            detail={
                "fallback": "vision",
                "ocr_enabled": plugin_config.fanqie_ocr_enabled,
                "model": verdict.model,
                "prompt": verdict.prompt,
                "image_url": image_url,
                "raw": verdict.raw,
                "passed": verdict.passed,
                "reason": verdict.reason,
            },
        )
        if verdict.passed:
            logger.info(
                "视觉判定通过 group={} user={} trace={}",
                group_id,
                user_id,
                record.trace_id,
            )
            return await _handle_pass(bot, group_id, user_id)
        logger.info(
            "视觉判定拒绝 group={} user={} reason={} trace={}",
            group_id,
            user_id,
            verdict.reason,
            record.trace_id,
        )
        return await _handle_reject(
            bot,
            group_id,
            user_id,
            fallback_evidence,
            verdict.reason,
        )
    return await _handle_ocr_failure(bot, group_id, user_id)


async def _vision_review(
    bot: Bot,
    group_id: int,
    user_id: int,
    image_url: str,
    record: SessionRecord,
) -> str:
    """OCR 通过后调用视觉模型复核（第二道防线，持否决权）。

    视觉模型否决则拒绝；通过或不可用则放行。

    """
    vision_verdict = await vision.vision_fallback(image_url, group_id)
    if vision_verdict is not None:
        await _record_event(
            record,
            event_type="verify.vision_review",
            success=vision_verdict.passed,
            detail={
                "model": vision_verdict.model,
                "prompt": vision_verdict.prompt,
                "image_url": image_url,
                "raw": vision_verdict.raw,
                "passed": vision_verdict.passed,
                "reason": vision_verdict.reason,
                "has_review_detail_title": vision_verdict.has_review_detail_title,
                "has_rating_stars": vision_verdict.has_rating_stars,
            },
        )
        if not vision_verdict.passed:
            logger.info(
                "视觉复核否决 group={} user={} reason={} trace={}",
                group_id,
                user_id,
                vision_verdict.reason,
                record.trace_id,
            )
            fallback_evidence = vision.verdict_to_evidence(vision_verdict)
            return await _handle_reject(
                bot,
                group_id,
                user_id,
                fallback_evidence,
                vision_verdict.reason,
            )
        logger.info(
            "视觉复核通过 group={} user={} trace={}",
            group_id,
            user_id,
            record.trace_id,
        )
    else:
        logger.info(
            "视觉复核不可用（OCR 已通过，放行） group={} user={} trace={}",
            group_id,
            user_id,
            record.trace_id,
        )

    logger.info(
        "验证通过 group={} user={} trace={}", group_id, user_id, record.trace_id
    )
    return await _handle_pass(bot, group_id, user_id)


async def _recognize_and_extract(
    image_url: str,
    group_id: int,
    record: SessionRecord,
) -> ReadingEvidence | None:
    """用多模型并行识别图片，融合各模型提取结果。

    所有模型都失败时返回 ``None``。识别与各模型提取结果会连同事务追踪
    （``record.trace_id``）写入事件表「verify.ocr」，确保链路可完整回溯。

    Args:
        image_url: 图片 URL。
        group_id: 群号（用于取作者白名单书名）。
        record: 当前验证会话（含 trace_id、群号、用户）。

    Returns:
        融合后的证据；全部模型失败返回 ``None``。

    """
    trace_id = record.trace_id
    models = list(plugin_config.fanqie_ocr_models) or [_default_ocr_model()]
    recognize_start = datetime.now(UTC)
    try:
        results = await recognize_image_url_multi(image_url, models=models)
    except OCRError as exc:
        logger.warning("OCR 识别失败 trace={}: {}", trace_id, exc)
        return None
    recognize_elapsed = (datetime.now(UTC) - recognize_start).total_seconds()
    if not results:
        logger.warning("全部 OCR 模型识别失败 trace={}", trace_id)
        return None

    known_books = _group_known_books(group_id)
    evidences: list[ReadingEvidence] = []
    per_model: list[dict[str, Any]] = []
    for model, result in results.items():
        ev = extractor.extract_reading_evidence(
            result,
            known_books=known_books,
        )
        evidences.append(ev)
        per_model.append(_summarize_model_extraction(model, ev, result))
        logger.debug(
            "模型 {} trace={} 提取: self={} book={} author={}",
            model,
            trace_id,
            ev.is_self_review,
            ev.book_name.value if ev.book_name else None,
            ev.author.value if ev.author else None,
        )
    merged, provenance = fusion.merge_evidences_with_trace(
        evidences,
        models,
        threshold=plugin_config.fanqie_similarity_threshold,
    )
    # 逐步判定链路：为什么 is_sufficient / 为什么缺某字段。
    decision_steps: list[str] = []
    if not merged.is_self_review:
        decision_steps.append("缺「我」徽章(is_self_review=False)")
    if merged.book_name is None:
        decision_steps.append("缺书名字段(book_name=None)")
    if merged.author is None:
        decision_steps.append("缺作者字段(author=None)")
    if merged.is_sufficient:
        decision_steps.append("信息充分")
    else:
        decision_steps.append("信息不足")
    # 将完整识别/提取链路落库，trace_id 串联。
    await _record_event(
        record,
        event_type="verify.ocr",
        success=True,
        detail={
            "per_model": per_model,
            "provenance": provenance,
            "merged": _summarize_model_extraction("merged", merged),
            "recognize_elapsed_s": round(recognize_elapsed, 3),
            "decision_steps": decision_steps,
            "is_sufficient": merged.is_sufficient,
        },
    )
    return merged


def _default_ocr_model() -> str:
    """返回配置的主 OCR 模型名。"""
    return plugin_config.fanqie_ocr_model or "PaddleOCR-VL-1.6"


_FIELD_KEYS: tuple[str, ...] = (
    "review_detail_page",
    "reader_name",
    "publish_time",
    "rating",
    "read_duration",
    "book_name",
    "author",
    "review_text",
)


def _summarize_model_extraction(
    model: str,
    ev: ReadingEvidence,
    result: Any | None = None,
) -> dict[str, Any]:
    """把一个模型的提取证据序列化为可审计的字典。

    含字段值/置信度/来源行，并附带该模型**全部 OCR 文本行**（text /
    confidence / box），便于完整回溯「模型到底识别出了哪些内容、坐标在哪」。

    """
    fields: dict[str, Any] = {}
    for key in _FIELD_KEYS:
        f = getattr(ev, key, None)
        if f is None:
            fields[key] = None
        else:
            fields[key] = {
                "value": f.value,
                "confidence": f.confidence,
                "source": f.source_text,
            }
    lines_out: list[dict[str, Any]] = []
    if result is not None:
        lines_out = [
            {
                "text": getattr(line, "text", None),
                "confidence": getattr(line, "confidence", None),
                "box": getattr(line, "box", None),
            }
            for page in getattr(result, "pages", None) or []
            for line in getattr(page, "lines", None) or []
        ]
    return {
        "model": model,
        "is_self_review": ev.is_self_review,
        "publish_days_ago": ev.publish_days_ago,
        "fields": fields,
        "lines": lines_out,
    }


async def handle_private_submission(
    bot: Bot,
    *,
    user_id: int,
    image_url: str | None,
) -> str:
    """私聊验证入口：按账号处理新成员提交的阅读截图。

    私聊消息不携带群号，因此先按账号查出该用户的全部待验证等待群：

    - 无 waiting 会话：提示当前没有待验证请求；
    - 恰好一个：直接对该群验证；
    - 多个：若已通过私聊选定目标群则验证该群，否则返回群列表供选择。

    群选择状态由会话存储维护（``set_private_target`` / ``get_private_target``）。

    Args:
        bot: 当前 Bot 实例。
        user_id: 成员 QQ 号。
        image_url: 图片 URL（无可用 URL 时视为下载失败）。

    Returns:
        面向用户的反馈消息。

    """
    store = get_session_store()
    waiting = store.list_waiting_by_user(str(user_id))
    if not waiting:
        return "你当前没有待验证的群聊请求。"
    if len(waiting) == 1:
        record = waiting[0]
        return await handle_submission(
            bot,
            group_id=int(record.group_id),
            user_id=user_id,
            image_url=image_url,
        )
    # 多个待验证群：先看用户是否已选定目标群。
    target = store.get_private_target(str(user_id))
    if target is not None and any(r.group_id == target for r in waiting):
        return await handle_submission(
            bot,
            group_id=int(target),
            user_id=user_id,
            image_url=image_url,
        )
    groups = "、".join(sorted(r.group_id for r in waiting))
    return (
        f"你当前在 {len(waiting)} 个群等待验证：{groups}。"
        "请回复「验证 群号」指定要在哪个群验证（如：验证 123456）。"
    )


async def review_verification(
    bot: Bot,
    *,
    group_id: int,
    user_id: int,
    triggered_by_admin: bool = False,
) -> str:
    """管理员或群成员发起“重审”：重新开启目标成员的验证流程。

    普通群成员（非群管理/群主）通过“重审”命令重审**自己**：
    - 要求当前存在待处理会话（``waiting`` / ``awaiting_admin``）；
    - 消耗一次重审机会，上限 ``fanqie_review_max_times``（默认 2）；
    - 重审复用现有验证流程：重置 OCR 重试次数、重新发送引导消息、
      重新计算超时窗口。
    管理员（FANQIE_ADMIN_IDS 或群内 admin/owner）可对任意普通成员发起
    重审，不消耗次数、不受上限限制。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 被重审的目标成员 QQ 号。
        triggered_by_admin: 是否由管理员发起（``True`` 时不消耗次数）。

    Returns:
        面向命令发起者的反馈消息。

    """
    store = get_session_store()
    group_key, user_key = str(group_id), str(user_id)

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        return "该成员不在群聊中，无法重审。"
    if member.is_admin:
        return "该成员是群管理或群主，无需参与入群验证。"

    old_record = store.get(group_key, user_key)
    if not triggered_by_admin:
        if old_record is None or old_record.status not in (
            "waiting",
            "awaiting_admin",
        ):
            return "你当前没有待处理的验证请求，无需重审。"
        if old_record.review_count >= plugin_config.fanqie_review_max_times:
            return (
                f"重审次数已达上限（{plugin_config.fanqie_review_max_times} 次），"
                "请联系管理员处理。"
            )

    started = await start_verification(bot, group_id=group_id, user_id=user_id)
    if started is None:
        return "该群未启用入群验证或重审未完成，请稍后再试。"

    # start 创建了新会话（retry 重置、review_count 归零）；普通成员自审
    # 需要把旧会话的重审计数延续下来（+1），管理员重审视为全新流程。
    if not triggered_by_admin and old_record is not None:
        updated_review = old_record.review_count + 1
        updated = store.set_review_count(group_key, user_key, updated_review)
        if updated is not None:
            await _persist_session(updated)
    await _record_event(
        store.get(group_key, user_key),
        event_type="verify.review",
        success=True,
        detail={"by_admin": triggered_by_admin},
    )
    return "已重新发起验证，请查看新的引导消息并尽快提交截图。"


async def handle_timeout(group_id: str, user_id: str) -> None:
    """FR7：超时未收到截图，按验证失败处理并通知管理员决策。"""
    store = get_session_store()
    record = store.get(group_id, user_id)
    if record is None:
        return
    bot_id = record.bot_id
    if not bot_id:
        logger.warning(
            "会话缺少 bot_id，跳过超时处理: {} trace={}",
            (group_id, user_id),
            record.trace_id,
        )
        await _persist_session(store.end(group_id, user_id, status="expired"))
        return
    bot = await _get_bot(bot_id)
    if bot is None:
        logger.warning("找不到 Bot {}，跳过超时处理 trace={}", bot_id, record.trace_id)
        await _persist_session(store.end(group_id, user_id, status="expired"))
        return

    member = await actions.get_member_info(bot, int(group_id), int(user_id))
    if member is None:
        logger.info(
            "超时处理：成员 {} 已不在群 {} 中，直接结束 trace={}",
            user_id,
            group_id,
            record.trace_id,
        )
        ended = store.end(group_id, user_id, status="expired")
        await _persist_session(ended)
        await _record_event(
            ended,
            event_type="verify.timeout",
            success=False,
            detail={"left_group": True},
        )
        await actions.notify_admins(
            bot,
            group_id=int(group_id),
            user_id=int(user_id),
            reply_message_id=None,
            message=f"用户 {user_id} 在群 {group_id} 超时未提供截图，且已不在群聊中。",
        )
        return

    # 超时后在群内 @ 成员提示已超时、可发送「重审」重试（引用其最后提交的图片）。
    await actions.announce_member_timeout(
        bot,
        int(group_id),
        int(user_id),
        reply_message_id=store.get_last_image_message(group_id, user_id),
    )
    await _record_event(
        store.get(group_id, user_id),
        event_type="verify.timeout",
        success=True,
    )
    await _await_admin_decision(
        bot,
        group_id=group_id,
        user_id=user_id,
        reason=f"用户 {user_id} 在群 {group_id} 超时未提供截图",
    )


async def handle_reminder(group_id: str, user_id: str, remaining_seconds: int) -> None:
    """待管理员决策成员被移出前提醒（由会话存储的提醒任务触发）。"""
    store = get_session_store()
    record = store.get(group_id, user_id)
    if record is None or record.status != "awaiting_admin":
        return
    bot_id = record.bot_id
    if not bot_id:
        return
    bot = await _get_bot(bot_id)
    if bot is None:
        return
    member = await actions.get_member_info(bot, int(group_id), int(user_id))
    if member is None:
        return
    await actions.announce_kick_reminder(
        bot,
        int(group_id),
        int(user_id),
        remaining_seconds,
        reply_message_id=store.get_last_image_message(group_id, user_id),
    )
    await _record_event(
        record,
        event_type="verify.reminder",
        success=True,
        detail={"remaining_seconds": remaining_seconds},
    )


async def admin_decision(
    bot: Bot,
    *,
    group_id: int,
    user_id: int,
    keep: bool,
    reply_message_id: int | None = None,
) -> str:
    """FR9：管理员决定踢出或保留。

    admin_decision 附带“插入直接批准”能力：``keep=True`` 时，只要目标成员
    正处于验证中（``waiting`` / ``awaiting_admin``），管理员即可直接放行并
    欢迎，无需等待成员提交截图或超时。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 目标成员 QQ 号。
        keep: ``True`` 表示保留（/keep /通过），``False`` 表示踢出（/kick）。
        reply_message_id: 触发本次决策的消息 id（欢迎消息引用原消息）。

    Returns:
        面向管理员的反馈消息。

    """
    store = get_session_store()
    if keep:
        active = store.get(str(group_id), str(user_id))
        if active is None or active.status not in ("waiting", "awaiting_admin"):
            return "该成员当前不在验证流程中，无法直接批准。"
        record = store.end(str(group_id), str(user_id), status="approved")
        await _persist_session(record)
        await _record_event(record, event_type="verify.admin_keep", success=True)
        await actions.send_welcome(
            bot,
            group_id,
            user_id,
            reply_message_id
            or store.get_last_image_message(str(group_id), str(user_id)),
        )
        return "已直接批准该成员并通过验证。"
    pre_record = store.get(str(group_id), str(user_id))
    member = await actions.get_member_info(bot, group_id, user_id)
    kicked = await actions.kick_member(bot, group_id, user_id, member)
    record = store.end(str(group_id), str(user_id), status="kicked")
    await _persist_session(record)
    await _record_event(
        pre_record or record,
        event_type="verify.admin_kick",
        success=kicked,
    )
    if member is None:
        return "该成员已不在群聊中。"
    return "已将该成员移出群聊。" if kicked else "踢出失败，请检查机器人权限。"


async def _handle_ocr_failure(bot: Bot, group_id: int, user_id: int) -> str:
    """FR8：OCR 调用失败，重试计数 +1。"""
    return await _increment_retry(bot, group_id, user_id, kind="识别失败")


async def _handle_insufficient(
    bot: Bot,
    group_id: int,
    user_id: int,
) -> str:
    """FR8：信息不足，重试计数 +1。"""
    return await _increment_retry(bot, group_id, user_id, kind="信息不足")


async def _handle_download_failure(group_id: int, user_id: int) -> str:
    """PRD 10：图片下载失败不计入识别失败，提示重新发送。"""
    store = get_session_store()
    record = store.get(str(group_id), str(user_id))
    await _persist_session(record)
    await _record_event(
        record,
        event_type="verify.download_fail",
        success=False,
    )
    return "图片获取失败，请重新发送清晰的截图。"


async def _increment_retry(
    bot: Bot,
    group_id: int,
    user_id: int,
    *,
    kind: str,
    evidence: ReadingEvidence | None = None,
) -> str:
    """识别/判定失败：重试计数 +1；达上限则转入管理员决策（FR8）。

    Args:
        bot: 当前 Bot 实例。
        group_id: 群号。
        user_id: 成员 QQ 号。
        kind: 失败类别，用于拼装面向成员的提示文案。
        evidence: 本次截图的识别结果，达上限转管理员时附带（用于增强
            管理员通知中的书名/作者/评分等信息）。

    """
    store = get_session_store()
    updated = store.mark_retry(str(group_id), str(user_id))
    if updated is None:
        return "当前没有待处理的验证请求。"
    await _persist_session(updated)
    await _record_event(
        updated,
        event_type="verify.retry",
        success=False,
        detail={
            "kind": kind,
            "retry_count": updated.retry_count,
            "max_attempts": plugin_config.fanqie_max_attempts,
        },
    )

    remaining = plugin_config.fanqie_max_attempts - updated.retry_count
    trace = updated.trace_id
    if remaining > 0:
        base = f"{kind}，请重新发送清晰的截图（剩余尝试次数：{remaining}）。"
        if kind == "信息不足":
            base += (
                "请确认是在**点开自己书评的详情页面**后截图（书评详情页顶部有"
                "「书评详情」标题，并带「我」徽章），而不是列表页或他人书评页。"
            )
        return f"{base}{_trace_suffix(trace)}"

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info(
            "重试耗尽 trace={} 成员 {} 已不在群 {} 中", trace, user_id, group_id
        )
        await _persist_session(store.end(str(group_id), str(user_id), status="failed"))
        await actions.notify_admins(
            bot,
            group_id=group_id,
            user_id=user_id,
            reply_message_id=None,
            message=(
                f"用户 {user_id} 在群 {group_id} 连续 "
                f"{plugin_config.fanqie_max_attempts} 次识别失败，但已不在群聊中。"
                f"{_trace_suffix(trace)}"
            ),
        )
        return (
            f"连续 {plugin_config.fanqie_max_attempts} 次识别失败，已通知管理员处理。"
            f"{_trace_suffix(trace)}"
        )

    await _await_admin_decision(
        bot,
        group_id=str(group_id),
        user_id=str(user_id),
        reason=(
            f"用户 {user_id} 在群 {group_id} 连续 "
            f"{plugin_config.fanqie_max_attempts} 次未通过验证"
            f"{_trace_suffix(trace)}"
        ),
        evidence=evidence,
    )
    return (
        f"连续 {plugin_config.fanqie_max_attempts} 次识别失败，已通知管理员处理。"
        f"{_trace_suffix(trace)}"
    )


async def _handle_pass(
    bot: Bot,
    group_id: int,
    user_id: int,
) -> str:
    """FR5：通过验证，发送欢迎消息。"""
    store = get_session_store()
    record = store.get(str(group_id), str(user_id))

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info(
            "放行处理：成员 {} 已不在群 {} 中，仅结束会话 trace={}",
            user_id,
            group_id,
            record.trace_id if record else None,
        )
        ended = store.end(str(group_id), str(user_id), status="approved")
        await _persist_session(ended)
        await _record_event(ended, event_type="verify.pass", success=True)
        return "验证通过。"

    ended = store.end(str(group_id), str(user_id), status="approved")
    await _persist_session(ended)
    await _record_event(ended, event_type="verify.pass", success=True)
    return "验证通过，欢迎加入本群！"


async def _handle_reject(
    bot: Bot,
    group_id: int,
    user_id: int,
    evidence: ReadingEvidence,
    reason: str | None,
) -> str:
    """FR6：验证未通过 —— 计入重试；连续失败达上限才转管理员决策。

    成员仍有重试机会时只提示重发截图（与 OCR 路径一致），避免仅视觉
    模式下首判失败即把人送进待管理员处理阶段。

    """
    store = get_session_store()

    cur = store.get(str(group_id), str(user_id))
    trace = cur.trace_id if cur else None

    member = await actions.get_member_info(bot, group_id, user_id)
    if member is None:
        logger.info(
            "拒绝处理 trace={} 成员 {} 已不在群 {} 中",
            trace,
            user_id,
            group_id,
        )
        ended = store.end(str(group_id), str(user_id), status="rejected")
        await _persist_session(ended)
        await _record_event(
            ended,
            event_type="verify.reject",
            success=False,
            detail={"left_group": True, "reason": reason},
        )
        await actions.notify_admins(
            bot,
            group_id=group_id,
            user_id=user_id,
            reply_message_id=None,
            message=(
                f"用户 {user_id} 在群 {group_id} 未通过验证（{reason}），"
                "但已不在群聊中。"
                f"{_trace_suffix(trace)}"
            ),
        )
        return f"验证未通过。{_trace_suffix(trace)}"

    await _record_event(
        store.get(str(group_id), str(user_id)),
        event_type="verify.reject",
        success=False,
        detail={"left_group": False, "reason": reason},
    )
    # 与 OCR 路径（_handle_insufficient）保持一致：**先给成员重试机会**，
    # 只有在 `FANQIE_MAX_ATTEMPTS` 次都未通过时才由 _increment_retry
    # 转入管理员决策。此前这里直接调 _await_admin_decision，导致仅视觉
    # 模式下第一次判定失败就结束验证、重试计数形同废弃。
    return await _increment_retry(
        bot,
        group_id,
        user_id,
        kind=f"验证未通过（{reason}）" if reason else "验证未通过",
        evidence=evidence,
    )


async def _await_admin_decision(
    bot: Bot,
    *,
    group_id: str,
    user_id: str,
    reason: str,
    evidence: ReadingEvidence | None = None,
) -> None:
    """验证失败后转入待管理员决策状态并通知管理员。

    会话保留为 ``awaiting_admin`` 并调度管理决策超时；管理员可通过
    ``/kick`` / ``/keep`` 决策，超时则由 :func:`handle_admin_decision_timeout`
    在群内通报并移出成员。

    """
    store = get_session_store()
    last_extracted = _to_dict(evidence) if evidence is not None else None
    record = store.await_admin(group_id, user_id, last_extracted=last_extracted)
    await _persist_session(record)
    await _record_event(
        record,
        event_type="verify.await_admin",
        success=True,
        detail={"reason": reason},
    )
    message = (
        actions.build_admin_notice(
            group_id=int(group_id),
            user_id=int(user_id),
            reader_name=(
                evidence.reader_name.value
                if evidence and evidence.reader_name
                else None
            ),
            book_name=(
                evidence.book_name.value if evidence and evidence.book_name else None
            ),
            author=evidence.author.value if evidence and evidence.author else None,
            rating=evidence.rating.value if evidence and evidence.rating else None,
            publish_time=(
                evidence.publish_time.value
                if evidence and evidence.publish_time
                else None
            ),
        )
        if evidence is not None
        else reason
    )
    await actions.notify_admins(
        bot,
        group_id=int(group_id),
        user_id=int(user_id),
        reply_message_id=store.get_last_image_message(group_id, user_id),
        message=message,
    )


async def handle_admin_decision_timeout(group_id: str, user_id: str) -> None:
    """管理员决策超时：在群内通报并移出成员。

    该回调由会话存储的 ``awaiting_admin`` 超时任务触发；若成员已不在群
    则仅结束会话。

    """
    store = get_session_store()
    record = store.get(group_id, user_id)
    if record is None or record.status != "awaiting_admin":
        return
    bot_id = record.bot_id
    if not bot_id:
        logger.warning(
            "会话缺少 bot_id，跳过管理决策超时处理: {} trace={}",
            (group_id, user_id),
            record.trace_id,
        )
        await _persist_session(store.end(group_id, user_id, status="expired"))
        return
    bot = await _get_bot(bot_id)
    if bot is None:
        # bot 不在线时不能直接放过成员：记为待补踢，重连后由
        # retry_pending_kicks 补偿（重启后恢复的过期会话走的就是这条）。
        logger.warning(
            "找不到 Bot {}，会话转入待补踢 trace={}", bot_id, record.trace_id
        )
        await _persist_session(store.end(group_id, user_id, status="kick_pending"))
        _schedule_kick_retry()
        return

    member = await actions.get_member_info(bot, int(group_id), int(user_id))
    if member is None:
        logger.info(
            "管理决策超时：成员 {} 已不在群 {} 中，直接结束 trace={}",
            user_id,
            group_id,
            record.trace_id,
        )
        ended = store.end(group_id, user_id, status="expired")
        await _persist_session(ended)
        await _record_event(
            ended,
            event_type="verify.admin_timeout",
            success=False,
            detail={"left_group": True},
        )
        return

    await actions.announce_admin_timeout(
        bot,
        int(group_id),
        int(user_id),
        reply_message_id=store.get_last_image_message(group_id, user_id),
    )
    kicked = await actions.kick_member(bot, int(group_id), int(user_id), member)
    if not kicked:
        # 踢出失败（掉线/权限/网络异常）：保留为待补踢，不误标 kicked。
        logger.warning(
            "管理决策超时踢出失败，转待补踢 group={} user={} trace={}",
            group_id,
            user_id,
            record.trace_id,
        )
        await _persist_session(store.end(group_id, user_id, status="kick_pending"))
        await _record_event(
            record,
            event_type="verify.admin_timeout",
            success=False,
            detail={"left_group": False, "kick_failed": True},
        )
        _schedule_kick_retry()
        return

    kicked_record = store.end(group_id, user_id, status="kicked")
    await _persist_session(kicked_record)
    await _record_event(
        record,
        event_type="verify.admin_timeout",
        success=True,
        detail={"left_group": False},
    )
    logger.info(
        "管理决策超时：成员 {} 已从群 {} 移出 trace={}",
        user_id,
        group_id,
        record.trace_id,
    )


async def retry_pending_kicks(bot: Bot) -> int:
    """机器人重连后补踢所有待补踢成员（掉线/重启期间的容灾补偿）。

    遍历 ``kick_pending`` 会话逐个重试踢出：成功转为 ``kicked``，成员已
    不在群视为达成目标同样转 ``kicked``，仍失败则保留待下次补偿。

    Args:
        bot: 当前已连接的 Bot 实例。

    Returns:
        本次补踢完成（转为 kicked）的会话数量。

    """
    store = get_session_store()
    pending = store.list_kick_pending()
    if not pending:
        return 0
    logger.info("开始补踢 {} 个待处理成员", len(pending))
    kicked = 0
    for record in pending:
        group_id, user_id = record.group_id, record.user_id
        member = await actions.get_member_info(bot, int(group_id), int(user_id))
        if not await actions.kick_member(bot, int(group_id), int(user_id), member):
            logger.warning(
                "补踢仍失败，保留待补踢 group={} user={} trace={}",
                group_id,
                user_id,
                record.trace_id,
            )
            continue
        ended = store.end(group_id, user_id, status="kicked")
        await _persist_session(ended)
        await _record_event(
            record,
            event_type="verify.kick_retry",
            success=True,
            detail={"left_group": member is None},
        )
        kicked += 1
        logger.info(
            "补踢完成：成员 {} 已从群 {} 移出 trace={}",
            user_id,
            group_id,
            record.trace_id,
        )
    logger.info("补踢结束：成功 {} / 待处理 {}", kicked, len(pending))
    return kicked


def _schedule_kick_retry() -> None:
    """后台启动一次踢人重试循环（不阻塞当前超时任务）。"""
    from ...core.async_utils import fire_and_forget

    fire_and_forget(schedule_kick_retry(), name="schedule_kick_retry")


async def retry_pending_kicks_once() -> int:
    """补踢一轮：自动为待补踢会话查找各自的已连接 bot。

    与 :func:`retry_pending_kicks` 的区别是不需要调用方提供 bot，供定时
    重试循环使用（此时 bot 可能刚恢复连接）。

    Returns:
        本轮完成补踢的会话数量。

    """
    store = get_session_store()
    bot_ids = {record.bot_id for record in store.list_kick_pending() if record.bot_id}
    done = 0
    for bot_id in bot_ids:
        bot = await _get_bot(bot_id)
        if bot is None:
            logger.info("补踢跳过：Bot {} 尚未连接", bot_id)
            continue
        done += await retry_pending_kicks(bot)
    return done


async def schedule_kick_retry() -> None:
    """按配置的轮数与间隔后台重试待补踢成员。

    覆盖「bot 一直在线、只是单次踢出失败（权限/瞬时错误）」的情况；若成员
    在此期间已补踢完或已退群，循环提前结束。重试用尽仍失败的会话保持
    ``kick_pending``，由机器人重连时的 :func:`retry_pending_kicks` 兜底。

    """
    times = max(0, plugin_config.fanqie_kick_retry_times)
    delay = max(1, plugin_config.fanqie_kick_retry_delay)
    store = get_session_store()
    for attempt in range(1, times + 1):
        await asyncio.sleep(delay)
        if not store.list_kick_pending():
            return
        done = await retry_pending_kicks_once()
        remaining = len(store.list_kick_pending())
        logger.info(
            "踢人重试第 {}/{} 轮：完成 {}，剩余 {}", attempt, times, done, remaining
        )
        if not remaining:
            return
    logger.warning("踢人重试已用尽 {} 轮，保留待补踢等待重连补偿", times)


async def handle_member_left(
    *,
    group_id: str,
    user_id: str,
    sub_type: str,
    operator_id: int | None = None,
) -> bool:
    """成员离群：终止其验证会话并落库终态。

    覆盖 OneBot11 ``group_decrease`` 的两种成员级 ``sub_type``：

    - ``leave``（主动退群）→ 终态 ``left_group``
    - ``kick``（被踢）→ 终态 ``kicked``

    用 ``operator_id`` 与机器人自身账号比对，在事件 detail 里标记是否为
    机器人踢出，便于日后查账区分「机器人按流程踢的」与「管理员手踢」。

    Args:
        group_id: 群号。
        user_id: 离开的成员 QQ 号。
        sub_type: 事件子类型（``leave`` / ``kick``）。
        operator_id: 操作者 QQ 号（主动退群时通常是本人）。

    Returns:
        是否处理了既有会话（无会话时为 ``False``）。

    """
    store = get_session_store()
    record = store.get(group_id, user_id)
    if record is None:
        return False
    status = "left_group" if sub_type == "leave" else "kicked"
    ended = store.abandon(group_id, user_id, status=status)
    await _persist_session(ended)
    by_bot = operator_id is not None and str(operator_id) == str(record.bot_id)
    await _record_event(
        ended or record,
        event_type="verify.member_left",
        success=True,
        detail={
            "sub_type": sub_type,
            "status": status,
            "operator_id": operator_id,
            "by_bot": by_bot,
            "previous_status": record.status,
        },
    )
    logger.info(
        "成员 {} 离开群 {}（{}），会话终止为 {} by_bot={} trace={}",
        user_id,
        group_id,
        sub_type,
        status,
        by_bot,
        record.trace_id,
    )
    return True


async def handle_bot_left_group(*, group_id: str) -> int:
    """机器人被移出群或主动退群：终止该群全部会话并落库。

    这些会话所在群已不可用，任何超时/提醒任务都不可能完成，必须落库为
    终态，否则重启后会被 :func:`restore_pending_sessions` 重新恢复。

    Args:
        group_id: 群号。

    Returns:
        被终止的会话数量。

    """
    store = get_session_store()
    ended = store.abandon_group(group_id, status="expired")
    for record in ended:
        await _persist_session(record)
        await _record_event(
            record,
            event_type="verify.group_unavailable",
            success=False,
            detail={"previous_status": record.status},
        )
    if ended:
        logger.info("机器人已离开群 {}，终止该群 {} 个会话", group_id, len(ended))
    return len(ended)


async def restore_pending_sessions() -> int:
    """重启后从数据库恢复待处理会话并重建超时调度。

    恢复 ``waiting``（等待成员提交截图）与 ``awaiting_admin``（等待管理员
    决策）两类会话，确保重启不会把验证中的成员当作已通过。

    Returns:
        恢复的会话数量。

    """
    if not plugin_config.fanqie_message_store_enabled:
        return 0
    store = get_session_store()
    restored = 0
    try:
        async with get_session() as session:
            records = await repository.list_pending_sessions(session)
    except Exception:  # noqa: BLE001 - 恢复失败不阻断启动
        logger.exception("恢复待处理验证会话失败")
        return 0
    now = datetime.now(UTC)
    for row in records:
        expires_at = row.expires_at or _fallback_deadline(row.status, now)
        record = SessionRecord(
            group_id=row.group_id,
            user_id=row.user_id,
            bot_id=row.bot_id,
            platform_id=row.platform_id,
            adapter_id=row.adapter_id,
            protocol_id=row.protocol_id,
            trigger_time=row.trigger_time,
            expires_at=expires_at,
            retry_count=row.retry_count,
            review_count=getattr(row, "review_count", 0),
            is_muted=row.is_muted,
            last_extracted=row.last_extracted,
            status=row.status,
        )
        store.restore(record)
        restored += 1
    if restored:
        logger.info("已从数据库恢复 {} 个待处理验证会话", restored)
    return restored


def _fallback_deadline(status: str, now: datetime) -> datetime:
    """按会话状态推算默认截止时间（expires_at 缺失时兜底）。"""
    if status == "awaiting_admin":
        return now + timedelta(seconds=plugin_config.fanqie_admin_decision_timeout)
    return now + timedelta(seconds=plugin_config.fanqie_response_timeout)


def _trace_suffix(trace_id: str | None) -> str:
    """返回拼接在用户可见消息末尾的追踪标识后缀（便于入口追溯）。"""
    if not trace_id:
        return ""
    return f"（追踪ID: {trace_id}）"


async def _persist_session(record: SessionRecord | None) -> None:
    """把会话记录写入数据库（尽力而为）。"""
    if record is None or not plugin_config.fanqie_message_store_enabled:
        return
    try:
        async with get_session() as session, session.begin():
            await repository.upsert_verification_session(
                session,
                repository.VerificationSessionWrite(
                    platform_id=record.platform_id,
                    adapter_id=record.adapter_id,
                    protocol_id=record.protocol_id,
                    bot_id=record.bot_id,
                    group_id=record.group_id,
                    user_id=record.user_id,
                ),
                status=record.status,
                retry_count=record.retry_count,
                review_count=record.review_count,
                is_muted=record.is_muted,
                last_extracted=record.last_extracted,
                trigger_time=record.trigger_time,
                expires_at=record.expires_at,
                trace_id=record.trace_id,
            )
    except Exception:  # noqa: BLE001 - 持久化失败不阻断主流程
        logger.exception(
            "持久化验证会话失败: {} trace={}",
            (record.group_id, record.user_id),
            record.trace_id,
        )


async def _record_event(
    record: SessionRecord | None,
    *,
    event_type: str,
    success: bool | None = None,
    detail: dict | None = None,
) -> None:
    """把验证流程关键事件写入事件表（尽力而为）。

    事件类型覆盖全部流程节点：verify.start / verify.pass / verify.reject /
    verify.timeout / verify.reminder / verify.admin_timeout / verify.admin_keep /
    verify.admin_kick / verify.review / verify.retry / verify.download_fail。
    """
    if record is None or not plugin_config.fanqie_message_store_enabled:
        return
    trace = record.trace_id
    try:
        async with get_session() as session, session.begin():
            await repository.record_verification_event(
                session,
                repository.VerificationEventWrite(
                    platform_id=record.platform_id,
                    adapter_id=record.adapter_id,
                    protocol_id=record.protocol_id,
                    bot_id=record.bot_id,
                    group_id=record.group_id,
                    user_id=record.user_id,
                    event_type=event_type,
                    success=success,
                    detail=detail or None,
                    trace_id=trace,
                ),
            )
    except Exception:  # noqa: BLE001 - 事件记录失败不阻断主流程
        logger.exception("记录验证事件失败: {} trace={}", event_type, trace)
    if trace:
        logger.info(
            "验证事件 trace={} type={} success={} group={} user={} detail={}",
            trace,
            event_type,
            success,
            record.group_id,
            record.user_id,
            detail,
        )


async def _persist_last_extracted(
    record: SessionRecord,
    evidence: ReadingEvidence,
) -> None:
    """把最近一次提取结果写入会话。"""
    store = get_session_store()
    updated = store.update_last_extracted(
        record.group_id,
        record.user_id,
        _to_dict(evidence),
    )
    await _persist_session(updated)


async def _get_bot(bot_id: str) -> Bot | None:
    """按 self_id 查找已连接 OneBot11 Bot 实例。"""
    from nonebot import get_bots

    found = get_bots().get(bot_id)
    return found if isinstance(found, Bot) else None


__all__ = [
    "admin_decision",
    "handle_admin_decision_timeout",
    "handle_private_submission",
    "handle_reminder",
    "handle_submission",
    "handle_timeout",
    "restore_pending_sessions",
    "retry_pending_kicks",
    "start_verification",
]
