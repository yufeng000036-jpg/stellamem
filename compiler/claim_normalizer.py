"""One authoritative claim representation. No configuration or I/O on import."""

import hashlib
import json
import math
import re
import unicodedata
from security import apply_policy

ROUTE = {
    "preference": "core/preferences.md",
    "decision": "long-term/decisions.md",
    "lesson": "long-term/lessons.md",
    "procedure": "long-term/lessons.md",
    "project": "long-term/projects.md",
    "fact": "semantic/facts.md",
    "relationship": "semantic/profile.md",
    "identity": "core/user-core.md",
    "user-core": "core/user-core.md",
}

# Deterministic kind correction. The local 4B model defaults to "fact" for
# everything (78% of the live inbox vs 25% in the gold set), which both pollutes
# semantic/profile.md and defeats per-kind confidence thresholds. Rules are
# priority-ordered: specific intent words beat generic nouns, so "不把项目发到
# 网上" stays a preference instead of becoming a project. Identity classes are
# never rewritten.
KIND_RULES = (
    ("preference", r"偏好|喜欢|不喜欢|偏爱|宁愿|讨厌|更愿意|prefer|favorite|preference"),
    ("decision", r"决定|已定|选定|否决|不采用|改用|采用|迁移到|批准|拍板|decided|chose|rejected|switch(?:ed)? to"),
    ("lesson", r"教训|踩坑|以后(?:别|不要|不能)|切勿|别再|根因|失败原因|会导致|报错|pitfall|root cause"),
    ("relationship", r"关系|朋友|家人|姐姐|父母|妈妈|爸爸|同事|伴侣|relationship"),
    ("project", r"项目|进度|已完成|完成度|里程碑|上线|交付|开发中|milestone|feature|功能"),
    ("procedure", r"流程|步骤|先.{0,8}再|workflow|step\s*\d|pipeline|安装步骤"),
)
_KIND_PATTERNS = tuple((kind, re.compile(pattern, re.I)) for kind, pattern in KIND_RULES)

# --- Hard kind rules -------------------------------------------------------
# A keyword alone is not enough. Each class requires several conditions at
# once, checked in the order decision -> preference -> lesson -> procedure ->
# project -> fact. The model's label is only preserved for classes the extractor
# is already reliable at (preference/lesson/relationship); the polluted classes
# (fact/procedure/project/decision) are always re-derived.
_RELATIONSHIP = re.compile(r"关系|朋友|家人|姐姐|父母|妈妈|爸爸|同事|伴侣|relationship", re.I)
# A relationship claim needs a person/role subject plus a relational signal.
# Names alone ("宇枫与刘阔") are the strongest signal and were previously missed.
_REL_SUBJECT = re.compile(
    r"(?:与|和|跟|and|with)\s*[^\s，。；]{2,}|我|他|她|爸爸|妈妈|姐姐|父亲|母亲|父亲"
    r"|\bYu\s?Feng\b|\bassistant\b",
    re.I,
)
_REL_SIGNAL = re.compile(
    r"照顾者|caregiver|主动\s*\d+\s*%|相识|陪伴|关系|互动|亲密|intimacy|affection|love"
    r"|表白|告白|情感",
    re.I,
)
_REL_MILESTONE = re.compile(r"\d+\s*(?:天|days?).{0,20}(?:相识|认识|陪伴|interaction)", re.I)
_CHOICE = re.compile(
    r"决定|选定|改用|否决|不采用|采用|迁移到|批准|拍板|定为|回滚|恢复为"
    r"|补充|新增|移除|替换|切换至|换成|暂不|不升级|放弃|保留|精简"
    r"|reverted to|rolled back|decided|chose|rejected|switch(?:ed)? to"
    r"|retain|keep only|prune|not upgraded|decided against",
    re.I,
)
_STRONG_CHOICE = re.compile(
    r"reverted to|改回|切换回|恢复为|回滚到|补充|新增|暂不|不升级|放弃|not upgraded",
    re.I,
)
_RATIONALE = re.compile(
    r"因为|理由|原因|考虑到|为了|由于|替代|而不是|改为|换成|避免|以便|权衡"
    r"|分析后|评估后|考虑后|权衡后|验证后|实测|快\s*\d|算了|暂时不|先不|只保留"
    r"|instead of|because|so that|essential",
    re.I,
)
_POSITIONING = re.compile(r"not a .{1,12} but|不是.{1,8}而是|定位为|conceived as", re.I)
_NOT_A_DECISION = re.compile(r"(?:不|避免|不要).{0,4}做?.{0,4}决定", re.I)
_NAMING = re.compile(r"命名|取名|候选名|名字", re.I)
_OPTIONS = re.compile(r"/|、|还是|或")
_PREFERENCE = re.compile(
    r"偏好|喜欢|不喜欢|偏爱|宁愿|讨厌|更愿意|习惯|倾向|希望|不建议|最好|宁可"
    r"|情绪低时|不再|不要|别\b|不依赖|主动做事|prefer|favorite|preference"
    r"|must (?:include|deliver|contain)|必须(?:包含|交付|提供|附带)"
    r"|\bshould\b|\bnever\b|\btone\b|\bstyle\b|\bwording\b",
    re.I,
)
# One-off task instructions are not long-term preferences.
_ONE_TIME = re.compile(r"这次|本次|今天|临时|一次性|this time", re.I)
_LONG_TERM = re.compile(r"以后|长期|一直|总是|习惯|每次", re.I)
_PRIVACY = re.compile(r"仅处理|不联网|不上传|剔除|隐私|PII|权限|不外传", re.I)
_LESSON = re.compile(
    r"导致|崩溃|报错|错误|失败|教训|踩坑|根因|避免|以后别|别再|问题在于"
    r"|crash|bug|pitfall|root cause",
    re.I,
)
# Explicit lesson markers; these do not need a past-tense verb.
_LESSON_EXPLICIT = re.compile(
    r"踩坑|教训|根因|以后别|别再|避免.{0,8}(?:问题|坑|错误)|pitfall|root cause", re.I
)
_PAST = re.compile(r"了|过|被|最终|曾经|已|caused|led to", re.I)
_SEQUENCE = re.compile(r"先.{0,12}再|第[一二三1-3]步|步骤|流程|依次|然后|接着|→|->", re.I)
_ACTION = re.compile(
    r"运行|执行|安装|配置|启动|调用|打开|输入|点击|复制|移动|修改|设置|下载|上传"
    r"|备份|生成|部署|重启|搜索|获取|请求|使用|删除"
    r"|解压|挂载|修正|校对|处理"
    r"|\bstart\b|\brun\b|\binstall\b|\bconfigure\b|\bcreate\b|\bdeploy\b"
    r"|\bopen\b|\bcopy\b|\bmove\b|\bedit\b|\bset\b|\bdownload\b|\bupload\b"
    r"|\bbackup\b|\bgenerate\b|\brestart\b|\bsearch\b|\bfetch\b|\buse\b",
    re.I,
)
_EXECUTABLE = re.compile(
    r"[A-Za-z]:[\\/]|\.(?:py|bat|sh|exe|ps1|json|md|txt)\b|`[^`]+`|--[a-z-]+|=[^\s]+"
    r"|注册表|registry|快捷方式|shortcut|脚本|script|[A-Z]\s*盘",
    re.I,
)
_IMPERATIVE = re.compile(r"需|请|要|必须|先|再|\bmust\b|\bplease\b", re.I)
_PROJECT_STATUS = re.compile(
    r"进行中|已完成|完成度|进度|里程碑|交付|上线|开发中|待办|尚未交付|未交付"
    r"|全部完成|完成(?:了|度|\d|，|,|$)|写完|已写完|已交付"
    r"|milestone|delivered|in progress|completed|finished",
    re.I,
)
_OBJECT = re.compile(r"项目|产品|课程|视频|报告|游戏|系统|工具|平台|仓库|账号", re.I)
_GENERIC_SUBJECT = {"user", "assistant", "system", "项目", "流程", "工作流", "任务", "问题", "状态"}


def _decision(text, value, support):
    if _NOT_A_DECISION.search(text):
        return False
    if _POSITIONING.search(support):
        return True
    naming = _NAMING.search(text) and len(_OPTIONS.findall(value)) >= 1 and len(value) >= 8
    choice = bool(_STRONG_CHOICE.search(text) or (_CHOICE.search(text) and _RATIONALE.search(support)))
    return bool(naming or choice)


def _preference(text):
    if _ONE_TIME.search(text) and not _LONG_TERM.search(text):
        return False
    return bool(_PREFERENCE.search(text) or _PRIVACY.search(text))


def _lesson(text, support):
    explicit = _LESSON_EXPLICIT.search(text)
    return bool(_LESSON.search(text) and (_PAST.search(support) or explicit))


def _relationship(text, subject):
    # A person/role subject is mandatory; a bare number or tool name never is.
    if _REL_MILESTONE.search(text):
        return True
    return bool(_REL_SIGNAL.search(text) and _REL_SUBJECT.search(subject))


def _procedure(text, value):
    if is_noise_claim({"value": text}):
        return False
    # Feature statements ("supports X", "provides Y") are facts, not procedures.
    if re.match(r"^(?:支持|具备|提供|可|功能[:：])", value.strip()):
        return False
    sequence = _SEQUENCE.search(text) and _ACTION.search(text)
    executable = _ACTION.search(text) and _EXECUTABLE.search(text)
    imperative = _ACTION.search(text) and _IMPERATIVE.search(text) and len(text) <= 120
    return bool(sequence or executable or imperative)


def _project(text, subject):
    if not _PROJECT_STATUS.search(text):
        return False
    object_present = (
        subject.strip().lower() not in _GENERIC_SUBJECT
        and len(subject.strip()) >= 2
    ) or _OBJECT.search(text)
    return bool(object_present)


def _hard_kind(text, subject, value, evidence):
    support = text + " " + evidence
    if _decision(text, value, support):
        return "decision"
    if _preference(text):
        return "preference"
    if _lesson(text, support):
        return "lesson"
    if _relationship(text, subject):
        return "relationship"
    if _procedure(text, value):
        return "procedure"
    if _project(text, subject):
        return "project"
    return "fact"

# Operational telemetry that must never become long-term memory. Deliberately
# narrow: it targets IDs, durations, hashes and bare paths, not every number
# (real facts like "B站粉丝 8,973" must survive).
_NOISE_VALUE = re.compile(
    r"^(?:yes|no|true|false|ok|done|pass(?:es)?|通过|是|否|\d+(?:\.\d+)?)$"
    r"|^[a-zA-Z]:[\\/]\S*$"
    r"|^https?://\S+$",
    re.I,
)
_NOISE_TEXT = re.compile(
    r"\bPID\s*\d+"
    r"|\b\d+(?:\.\d+)?\s*(?:seconds?|secs?|ms|毫秒|秒)"
    r"|\bexit\s*code\b|\btest\s+files?\b|\bcall\s+count\b"
    r"|\b\d+\s*(?:bytes|KB|MB|GB)\b"
    r"|\bversion\s*\d|\bfile_type\b",
    re.I,
)
# Case-sensitive on purpose: re.I would treat "AAAAAA" as a hex commit hash.
_NOISE_HASH = re.compile(r"\b[0-9a-f]{7,40}\b")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")


def is_noise_claim(c):
    value = canonical(c.get("value"))
    subject = canonical(c.get("subject"))
    if _NOISE_VALUE.match(value):
        return True
    if _NOISE_TEXT.search(value) or _NOISE_TEXT.search(subject):
        return True
    if _NOISE_HASH.search(value):
        return True
    return False


def infer_kind(kind, subject, predicate, value, evidence=""):
    if kind in PROTECTED_KINDS:
        return kind
    text = " ".join(str(part or "") for part in (subject, predicate, value))
    support = text + " " + str(evidence or "")
    # Keep the classes the extractor already gets right; only override them on a
    # strong decision/lesson signal. The four polluted classes are re-derived.
    if kind == "relationship":
        if _relationship(text, subject):
            return "relationship"
        # Not every legacy relationship label is real; let the hard rules below
        # decide instead of forcing it to fact.
    if kind == "preference":
        if _decision(text, value, support):
            return "decision"
        if _lesson(text, support):
            return "lesson"
        return "preference"
    if kind == "lesson":
        if _decision(text, value, support):
            return "decision"
        return "lesson"
    return _hard_kind(text, subject, value, str(evidence or ""))
THRESHOLDS = dict(
    project=0.85,
    decision=0.85,
    lesson=0.85,
    procedure=0.90,
    preference=0.95,
    fact=0.90,
    relationship=0.95,
    identity=1.01,
    **{"user-core": 1.01},
)
VALID_KINDS = set(ROUTE)
PROTECTED_KINDS = {"identity", "user-core"}


def number(value, default=0.0):
    def unit(n):
        # Values above 1 are treated as percentages (90 -> 0.9); 1 stays 1.
        if not math.isfinite(n):
            return default
        if n > 1.0:
            return max(0.0, min(1.0, n / 100.0))
        return max(0.0, n)

    try:
        return unit(float(value))
    except (TypeError, ValueError):
        pass
    # Fallbacks: models often return a word instead of a number. Map common
    # labels so a valid judgement is not silently flattened to zero.
    if isinstance(value, str):
        labels = {
            "very high": 0.95,
            "high": 0.9,
            "medium": 0.7,
            "moderate": 0.7,
            "low": 0.5,
            "very low": 0.3,
            "certain": 1.0,
            "none": 0.0,
        }
        text = value.strip().casefold()
        if text in labels:
            return labels[text]
        match = re.search(r"\d+(?:\.\d+)?", text)
        if match:
            try:
                return unit(float(match.group(0)))
            except (TypeError, ValueError):
                pass
    return default


def canonical(value):
    return unicodedata.normalize("NFC", str(value or "")).strip()


def claim_slot(c):
    return tuple(canonical(c.get(k)).casefold() for k in ("kind", "subject", "scope", "predicate"))


def claim_key(c):
    # Values retain punctuation and case: C++, C#, paths and credentials differ.
    return claim_slot(c) + (canonical(c.get("value")),)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _id_part(value):
    return re.sub(r"[^\w-]+", "_", canonical(value).casefold()).strip("_") or "unknown"


def claim_id(c):
    # Hash avoids legacy lossy slug collisions.
    return "c-" + digest(claim_slot(c))[:24]


def claim_revision_id(c):
    return "r-" + digest(claim_key(c))[:24]


def normalize_claim(raw):
    if not isinstance(raw, dict):
        raise ValueError("claim must be an object")
    c = {k: canonical(raw.get(k)) for k in ("kind", "subject", "predicate", "value", "evidence")}
    c["kind"] = infer_kind(c["kind"], c["subject"], c["predicate"], c["value"], c["evidence"])
    c["scope"] = canonical(raw.get("scope")) or "global"
    c["source"] = canonical(raw.get("source", "unknown")).lower()
    c["confidence"] = number(raw.get("confidence"))
    c["importance"] = number(raw.get("importance"), 0.5)
    c["valid"] = c["kind"] in VALID_KINDS and all(c[k] for k in ("subject", "predicate", "value"))
    for key in ("suspicious", "temporary", "sarcasm", "outdated", "sensitive", "heavy_emotion"):
        value = raw.get(key, False)
        # Unknown flag encodings are conservative, never treated as false.
        c[key] = value if isinstance(value, bool) else value not in (None, 0, "", "false")
    c["sensitivity"] = str(raw.get("sensitivity", "")).upper()
    c["requested_write_policy"] = raw.get("requested_write_policy", raw.get("write_policy"))
    # Verified provenance is attached by pipeline after normalization, not the LLM.
    c["evidence_verified"] = False
    c["schema_version"] = 2
    return apply_policy(c)
