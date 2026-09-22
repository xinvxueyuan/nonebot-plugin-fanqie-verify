"""NoneBot 解析的番茄读书入群验证插件配置。

配置项均通过环境变量（前缀 ``FANQIE_``）注入，由 NoneBot2 的
``get_plugin_config`` 统一解析。环境变量与字段名大小写不敏感，
例如 ``FANQIE_RESPONSE_TIMEOUT`` 对应 ``fanqie_response_timeout``。

"""

from typing import Literal

from pydantic import BaseModel, Field


class Config(BaseModel):
    """番茄读书入群验证插件配置。

    Attributes:
        fanqie_verify_groups: 需要执行入群验证的群号集合，空集合表示全部群。
        fanqie_admin_ids: 接收验证失败通知、可执行踢/保留命令的管理员 QQ 号集合。
        fanqie_welcome_message: 新成员入群后发送的验证提示消息。
        fanqie_response_timeout: 等待新成员发送截图的超时时间（秒），
            超时后按验证失败处理并通知管理员。
        fanqie_max_attempts: 识别失败的允许尝试次数，达上限后按验证
            失败处理并通知管理员。
        fanqie_admin_decision_timeout: 通知管理员后等待其决策的超时时间
            （秒），超时后将在群内通报并移出该成员。默认 16 小时。
        fanqie_remind_before_kick: 待管理员决策成员被移出前，提前提醒该
            成员的秒数列表（升序，如 ``[3600, 300]`` 表示最后 1 小时、
            5 分钟各提醒一次）。
        fanqie_review_max_times: 普通群成员（非群管理/群主）通过“重审”
            命令重新发起验证的最大次数，达到上限后需由管理员处理。
            管理员主动发起的重审不受此限制。默认 2 次。
        fanqie_notify_channel: 验证失败时通知管理员的渠道。``group`` 在群内
            发一条消息并 @ 全部管理员（可用 ``reply_message_id`` 引用成员原消息）；
            ``private`` 逐个私聊管理员，私聊失败时回退群内；``none`` 不发送。
            默认 ``group``。
        fanqie_book_name_max_len: FR4 综合判断中有效书名的最大字符数。
        fanqie_ocr_enabled: 是否启用 PaddleOCR 识别。为 False 时跳过 OCR，
            直接使用视觉模型判定（仅视觉模式）。默认 True（启用）。
        fanqie_ocr_api_url: PaddleOCR 云端 API 地址（留空使用官方默认服务）。
        fanqie_ocr_api_token: 调用云端 OCR API 的认证令牌。
        fanqie_ocr_timeout: 单次 HTTP 请求的超时时间（秒）。
        fanqie_ocr_poll_timeout: 等待 OCR 任务完成的总超时时间（秒）。
        fanqie_ocr_model: 使用的 PaddleOCR 模型名称（如 ``PP-OCRv6``）。
        fanqie_verification_policy_path: 放行策略的 TOML 配置文件路径，
            留空时使用 ``nonebot_plugin_localstore`` 的配置目录。策略决定
            书评详情页 OCR 识别出的哪些元素需要匹配、以及作者/书名白名单，
            可通过“重载番茄OCR配置”命令运行时重载。
        fanqie_message_store_enabled: 是否启用消息与审计记录存储。
        fanqie_message_store_summary_limit: 文本摘要的最大字符数。
        fanqie_message_store_cleanup_enabled: 是否在关闭时清理过期记录。
        fanqie_message_store_retention_days: 记录的保留天数。
        fanqie_message_store_record_api_calls: 是否记录平台 API 调用审计。
        fanqie_backfill_enabled: 是否启用「补验」（把错过入群事件的成员补进
            验证流程，用于 LLBot 掉线重连后漏掉的成员）。默认 True。
        fanqie_backfill_default_hours: 补验默认扫描窗口（小时）：只补入群时间在
            最近该时长内且无验证记录的成员。默认 24。
        fanqie_backfill_reconnect_mode: LLBot 重连时的补验行为，取值
            ``notify``（默认，仅在群里提醒管理员有漏验）/ ``auto``（自动补验）/
            ``off``（什么都不做）。
        fanqie_backfill_confirm_first: 补验是否先列候选名单等管理员确认。
            ``False``（默认）直接开启验证；``True`` 只列名单，待管理员发送
            「补验确认」后执行。
        fanqie_backfill_max_batch: 单次补验的人数上限（防止一次性给大量成员
            开启验证造成刷屏）。默认 20。
        fanqie_extend_enabled: 是否启用「延期」命令（推迟待管理员决策成员的
            移出时间，供管理员暂时无法处理时使用）。默认 True。
        fanqie_extend_default_hours: 「延期」不带时长参数时的默认延期小时数。
            默认 6 小时。
        fanqie_extend_max_hours: 「延期」单次可延长的最大小时数，超过时按该
            上限处理。默认 48 小时。不限制累计延期次数。
        fanqie_private_verify_enabled: 是否启用「私聊发图完成验证」通道。
            为 False 时停用私聊图片验证与「验证 <群号>」选群命令，仅保留
            群内验证。默认 True（启用）。
        fanqie_allow_group_admin_commands: 是否允许群内管理员（admin/群主）
            使用 /keep、/kick 等命令。为 True 时，除配置的管理员外，
            群内的管理员与群主也可执行；为 False 时仅配置的管理员可执行。
            默认 True（开启，群管理员默认可用）。

    """

    fanqie_verify_groups: set[int] = Field(default_factory=set)
    fanqie_admin_ids: set[int] = Field(default_factory=set)
    fanqie_allow_group_admin_commands: bool = True
    fanqie_welcome_message: str = (
        "欢迎新人进群，记得看先去群公告或群文件教程，"
        "如果需要留下需要发带有阅读时长的书评。"
        "为了验证您是真实的读者，请发送一张您在番茄小说发布的「书评详情页」截图"
        "（需显示顶部「书评详情」标题、您的书评及「我」徽章），"
        "直接截取手机整个屏幕即可。谢谢配合！"
    )
    fanqie_backfill_enabled: bool = True
    fanqie_backfill_default_hours: int = 24
    fanqie_backfill_reconnect_mode: str = "notify"
    fanqie_backfill_confirm_first: bool = False
    fanqie_backfill_max_batch: int = 20
    fanqie_extend_enabled: bool = True
    fanqie_extend_default_hours: int = 6
    fanqie_extend_max_hours: int = 48
    fanqie_private_verify_enabled: bool = True
    fanqie_response_timeout: int = 600
    fanqie_max_attempts: int = 3
    fanqie_admin_decision_timeout: int = 57600  # 16 小时（秒）
    fanqie_remind_before_kick: tuple[int, ...] = (3600, 300)
    fanqie_review_max_times: int = 2
    fanqie_notify_channel: Literal["group", "private", "none"] = "group"
    fanqie_book_name_max_len: int = 100
    fanqie_ocr_enabled: bool = True
    fanqie_ocr_api_url: str = ""
    fanqie_ocr_api_token: str = ""
    fanqie_ocr_timeout: float = 15.0
    fanqie_ocr_poll_timeout: float = 120.0
    fanqie_ocr_model: str = "PaddleOCR-VL-1.6"
    fanqie_vision_enabled: bool = True
    fanqie_vision_api_base: str = "https://api.deepseek.com"
    fanqie_vision_api_key: str = ""
    fanqie_vision_model: str = "deepseek-v4-flash-vision-exp"
    fanqie_vision_timeout: float = 60.0
    fanqie_similarity_threshold: float = 0.9
    fanqie_ocr_models: tuple[str, ...] = (
        "PaddleOCR-VL-1.6",
        "PP-OCRv6",
        "PP-StructureV3",
    )
    fanqie_verification_policy_path: str = ""
    fanqie_message_store_enabled: bool = True
    fanqie_message_store_summary_limit: int = 500
    fanqie_message_store_cleanup_enabled: bool = True
    fanqie_message_store_retention_days: int = 7
    fanqie_message_store_record_api_calls: bool = False
