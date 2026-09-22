"""入群验证业务层。

承载 FR3 信息提取、FR4 综合判断与 FR1~FR9 的流程编排。验证目标页面为
番茄小说「书评详情」页，核心判定为「我」徽章 + 书名 + 作者。

放行策略来自可编辑的 TOML 配置文件（见 :mod:`.policy`），业务层自身
不硬编码界面关键字。

"""

from .actions import (
    MemberInfo,
    announce_admin_timeout,
    announce_kick_reminder,
    announce_member_timeout,
    build_admin_notice,
    get_member_info,
    kick_member,
    notify_admins,
    send_guide,
    send_welcome,
)
from .backfill import (
    BackfillCandidate,
    collect_candidates,
    format_candidate_list,
    handle_reconnect,
    run_backfill,
)
from .extractor import extract_reading_evidence
from .flow import (
    admin_decision,
    handle_admin_decision_timeout,
    handle_private_submission,
    handle_reminder,
    handle_submission,
    handle_timeout,
    restore_pending_sessions,
    retry_pending_kicks,
    review_verification,
    start_verification,
)
from .judgment import Judgment, judge_evidence
from .models import ExtractedField, ReadingEvidence
from .policy import (
    SUPPORTED_ELEMENTS,
    AuthorEntry,
    GroupPolicy,
    PolicyCheckResult,
    PolicyConfigError,
    VerificationPolicy,
    get_policy,
    load_policy,
    reload_policy,
)
from .session import SessionRecord, SessionStore, get_session_store

__all__ = [
    "SUPPORTED_ELEMENTS",
    "AuthorEntry",
    "BackfillCandidate",
    "ExtractedField",
    "GroupPolicy",
    "Judgment",
    "MemberInfo",
    "PolicyCheckResult",
    "PolicyConfigError",
    "ReadingEvidence",
    "SessionRecord",
    "SessionStore",
    "VerificationPolicy",
    "admin_decision",
    "announce_admin_timeout",
    "announce_kick_reminder",
    "announce_member_timeout",
    "build_admin_notice",
    "collect_candidates",
    "extract_reading_evidence",
    "format_candidate_list",
    "get_member_info",
    "get_policy",
    "get_session_store",
    "handle_admin_decision_timeout",
    "handle_private_submission",
    "handle_reconnect",
    "handle_reminder",
    "handle_submission",
    "handle_timeout",
    "judge_evidence",
    "kick_member",
    "load_policy",
    "notify_admins",
    "reload_policy",
    "restore_pending_sessions",
    "retry_pending_kicks",
    "review_verification",
    "run_backfill",
    "send_guide",
    "send_welcome",
    "start_verification",
]
