"""Conservative candidate labels, not an assertion of account eligibility."""
import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Decision:
    kind: str
    reason: str
    reset_at: str | None = None


def classify(text: str, context: str = '') -> Decision:
    t = text.lower().replace('’', "'")
    reset = bool(re.search(r'\breset\w*\b|重置|刷新额度', t))
    usage = bool(re.search(r'\busage\b|\bquota\b|\blimits?\b|额度|用量', t))
    product = bool(re.search(r'\bcodex\b', t))
    if not reset and not (product and usage):
        # Never turn surrounding user demands into a confirmed announcement.
        # Only short response/timing/denial cues combined with a reset-related
        # context produce a reviewable lead; a weekday is not a timestamp.
        response = re.search(r"\b(ok(?:ay)?|fine|yes|yep|sure|agreed|done|coming|tuesday|monday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|no|not|never|won't)\b|好吧|好的|行[。！!，,]|会到|周[一二三四五六日天]|星期|明天|今晚|不会|不行|同意", t)
        if context and response and re.search(r'\breset\w*\b|重置|刷新额度', context, re.I):
            if classify(context).kind not in ('ignore', 'routine'):
                return Decision('uncertain', '对话上文涉及重置，作者回复需结合上下文核实；不据此确认额度范围或具体时间')
        return Decision('ignore', '未出现额度事件线索')
    # A monitored author may omit the product/object in a short follow-up.
    # Keep that as a lead, never infer Codex eligibility from a model name/quote.
    objects = r'(?:passwords?|devices?|routers?|cache|settings|phones?|computers?)'
    unrelated = (r'\breset\w*\s+(?:(?:the|your|my|our|their|a|an|all|its)\s+){0,2}' + objects + r'\b'
                 r'|\b' + objects + r'\s+(?:(?:has|have|been|was|is|a)\s+)*reset\w*\b'
                 r'|\bfactory\s+reset\w*\b|\breset\w*\s+to\s+factory\s+settings\b'
                 r'|重置(?:你的|您的|我的|所有)?(?:密码|路由器|缓存|设备|设置)')
    # Every reset mention must have an explicit unrelated object. A nearby
    # troubleshooting sentence mentioning a device/cache must not hide a lead.
    if reset and not usage and len(re.findall(unrelated, t)) == len(re.findall(r'\breset\w*\b|重置|刷新额度', t)):
        return Decision('ignore', '重置对象为密码、设备或设置等，未出现额度线索')
    if not product:
        return Decision('uncertain' if reset else 'ignore', '出现重置线索，但原帖未明确 Codex 或额度范围；需核对上下文')
    if re.search(r'\b(postpon\w*|delay\w*|cancel\w*|retract\w*)\b|延期|撤回|取消', t) and reset:
        return Decision('correction', '疑似延期或撤回；请查看原帖确认所指事件')
    if re.search(r"\b(not|never|maybe|might|could|if|rumou?r|wish|hope|joke|yesterday)\b|won't|n't|\?|？|不会|可能|假如|昨天|据说", t):
        return Decision('uncertain', '含否定、假设、提问或历史语境')
    if re.search(r'\b(credit|credits|voucher|coupon|redeem)\b|重置券|兑换', t) and reset:
        return Decision('credit', '疑似可手动兑换的重置权益，不等于全局自动重置')
    if reset and re.search(r'\b(every|daily|weekly|regular|normally)\b|每隔|每天|每周|周期', t):
        return Decision('routine', '普通周期恢复线索')
    if not reset:
        return Decision('limits', '额度相关变化，未检测到重置声明')
    if not usage:
        return Decision('uncertain', '重置对象不明确')
    if re.search(r'\b(will|going to|plan to|scheduled)\b|即将|将会|计划', t):
        matches = re.findall(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})', text)
        when = None
        if len(matches) == 1:
            try:
                when = datetime.fromisoformat(matches[0].replace('Z', '+00:00')).isoformat()
            except ValueError:
                pass
        return Decision('upcoming', '未来重置候选，适用账户与时间仍需核对原文', when)
    if re.search(r'\b(have|has|already|just|done|completed)\b|已经|已重置', t):
        return Decision('completed', '已宣布重置候选，请检查实际账户')
    return Decision('uncertain', '检测到重置线索，但时态或范围不明确')
