"""诗词生成/改写器（启发式，非大模型）。

生成器基于「格律模板 + 主题字库填充」的启发式实现：
- 按 style 取格律谱（句数/字数/韵位，见 services.poetry_phonology._METER_PROFILES）。
- 按 theme 取主题字库（内置小型主题池），逐字按目标平仄填充，韵脚字取自同韵部候选。
- 若传入 llm_client（含 chat/complete 方法）且 use_llm=True，则优先尝试 LLM 生成，失败回退启发式。

改写器基于 validate_meter / evaluate_prosody 的失败项给出逐处建议（每处 1-3 个候选字/句 + 原因）。
"""
from __future__ import annotations

import random
from typing import Any

from services.poetry_phonology import (
    _METER_ALIASES,
    _METER_PROFILES,
    _RHYME_POOLS,
    classify_char,
    evaluate_prosody,
    find_rhyme_candidates,
    validate_meter,
)

# ─────────────────────────────────────────────────────────────
# 主题字库（轻量内置；仅保留已收录平仄的字，保证生成可验证）
# ─────────────────────────────────────────────────────────────
_THEME_POOLS: dict[str, list[str]] = {
    "春": ["春", "花", "风", "雨", "夜", "月", "明", "青", "山", "水", "云",
           "归", "飞", "红", "新", "开", "来", "东", "暖", "绿", "芳", "香", "深"],
    "秋": ["秋", "风", "霜", "月", "夜", "凉", "山", "云", "黄", "落", "寒",
           "露", "雁", "思", "愁", "深", "清", "河", "客", "孤", "远"],
    "山": ["山", "高", "青", "云", "石", "泉", "松", "林", "谷", "峰", "远",
           "深", "空", "水", "烟", "翠", "孤", "寒", "鸟", "归", "月"],
    "水": ["江", "河", "湖", "海", "波", "流", "泉", "舟", "浪", "潮", "帆",
           "水", "烟", "岸", "桥", "渔", "渡", "白", "清", "碧"],
    "月": ["月", "明", "光", "夜", "霜", "星", "影", "凉", "圆", "照", "寒",
           "清", "玉", "云", "露", "时", "孤", "满"],
    "夜": ["夜", "月", "星", "风", "雨", "眠", "梦", "深", "寒", "静", "明",
           "窗", "更", "长", "客", "孤", "思"],
    "思": ["思", "故", "乡", "梦", "归", "望", "忆", "愁", "心", "情", "怀",
           "远", "飞", "雁", "泪", "吟", "寻", "念", "相", "无"],
    "别": ["别", "离", "送", "客", "舟", "柳", "亭", "泪", "孤", "寒", "远",
           "行", "回", "念", "愁", "伤", "暮", "去", "归"],
    "志": ["志", "高", "远", "长", "行", "千", "里", "万", "功", "名", "心",
           "海", "天", "江", "潮", "云", "鹏", "风", "苍"],
    "酒": ["酒", "杯", "月", "花", "歌", "吟", "醉", "夜", "风", "云", "孤",
           "春", "秋", "客", "琴", "诗", "霞", "友"],
    "隐": ["隐", "林", "泉", "云", "山", "松", "竹", "月", "琴", "鹤", "闲",
           "深", "幽", "石", "苔", "静", "春", "秋", "日"],
}
_GENERIC_POOL = [
    "天", "地", "山", "水", "风", "云", "花", "月", "江", "河", "春", "秋", "日",
    "夜", "明", "光", "新", "高", "长", "远", "归", "来", "飞", "流", "清", "深",
    "寒", "孤", "白", "青", "红", "翠", "望", "思", "知", "无", "不", "有", "千",
    "万", "一", "三", "人", "客", "舟", "林", "泉", "石", "松", "星", "霜", "雪",
    "雨", "烟", "时", "故", "乡", "心", "梦", "行", "海", "晴", "凉",
]
_DEFAULT_RHYME_GROUPS = ["一东", "七阳", "十一尤", "一先", "八庚"]


def _known_pool(chars: list[str]) -> tuple[list[str], list[str]]:
    """按已收录平仄把字库拆成 平/仄 两组（未收录字丢弃）。"""
    ping, ze = [], []
    for ch in chars:
        pz = classify_char(ch)["ping_ze"]
        if pz == "平":
            ping.append(ch)
        elif pz == "仄":
            ze.append(ch)
    return ping, ze


def _pick_pool(theme: str) -> tuple[list[str], list[str]]:
    key = (theme or "").strip()
    chars = _THEME_POOLS.get(key) or []
    ping, ze = _known_pool(chars)
    g_ping, g_ze = _known_pool(_GENERIC_POOL)
    # 主题字库优先，通用字库兜底
    return (ping or g_ping), (ze or g_ze)


def _pick_rhyme(rng: random.Random, rhyme: str | None, theme: str) -> tuple[str, str]:
    """返回 (韵脚字, 韵部)。未指定 rhyme 时按主题哈希选一个平声韵部。"""
    if rhyme:
        info = classify_char(rhyme)
        if info["rhyme_group"]:
            return rhyme, info["rhyme_group"]
    group = _DEFAULT_RHYME_GROUPS[rng.randrange(len(_DEFAULT_RHYME_GROUPS))]
    pool = _RHYME_POOLS.get(group, [])
    if not pool:
        return "风", "一东"
    return rng.choice(pool), group


def generate_poem(
    style: str,
    theme: str,
    rhyme: str | None = None,
    n: int = 4,
    seed: int | None = None,
    llm_client: Any = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """按格律模板 + 主题字库启发式生成；line_count 取谱要求（n 仅作提示保留）。

    返回 {style, theme, rhyme_char, rhyme_group, lines, text, engine, note}。
    """
    profile_key = _METER_ALIASES.get((style or "").strip())
    if profile_key is None:
        profile_key = "五绝"
        style_note = f"未知样式「{style}」，回退五绝"
    else:
        style_note = ""
    profile = _METER_PROFILES[profile_key]
    line_lengths = profile["line_lengths"]
    expected_count = profile["line_count"]

    if use_llm and llm_client is not None:
        try:
            text = _try_llm_generate(llm_client, profile_key, theme, rhyme)
            if text and len(_split_text(text)) == expected_count:
                return {
                    "style": profile_key, "theme": theme, "rhyme_char": rhyme or "",
                    "rhyme_group": classify_char(rhyme)["rhyme_group"] if rhyme else "",
                    "lines": _split_text(text), "text": text, "engine": "llm",
                    "note": style_note or "由配置的 LLM 生成",
                }
        except Exception:
            pass  # 回退启发式

    rng = random.Random(seed if seed is not None else hash(f"{style}:{theme}:{rhyme or ''}") & 0xFFFFFF)
    rhyme_char, rhyme_group = _pick_rhyme(rng, rhyme, theme)
    rhyme_pool = _RHYME_POOLS.get(rhyme_group, [rhyme_char])
    ping_pool, ze_pool = _pick_pool(theme)

    # 韵位（1-based 偶句；近体首句可入韵）
    if profile.get("rhyme_positions"):
        rhyme_positions = profile["rhyme_positions"]
        first_rhymes = True
    else:
        rhyme_positions = [i for i in range(expected_count) if (i + 1) % 2 == 0]
        first_rhymes = rng.random() < 0.5
        if first_rhymes:
            rhyme_positions = [0] + [p for p in rhyme_positions if p != 0]

    lines: list[str] = []
    for idx in range(expected_count):
        length = line_lengths[idx] if idx < len(line_lengths) else 5
        is_rhyme = idx in rhyme_positions
        line = _build_line(
            rng, length, is_rhyme, rhyme_pool, ping_pool, ze_pool,
            last_prefer_ze=not is_rhyme and (idx + 1) % 2 == 1,
        )
        lines.append(line)

    text = "，\n".join(lines[: len(lines) - 1]) + "。\n" + lines[-1] + "。" if len(lines) > 1 else lines[0] + "。"
    return {
        "style": profile_key,
        "theme": theme,
        "rhyme_char": rhyme_char,
        "rhyme_group": rhyme_group,
        "n": n,
        "lines": lines,
        "text": text,
        "engine": "heuristic",
        "note": style_note or "启发式生成（字库填充，非大模型）；可传 llm_client + use_llm=True 改用 LLM",
    }


def _build_line(
    rng: random.Random,
    length: int,
    is_rhyme: bool,
    rhyme_pool: list[str],
    ping_pool: list[str],
    ze_pool: list[str],
    last_prefer_ze: bool = False,
) -> str:
    chars: list[str] = []
    used: set[str] = set()
    for pos in range(length):
        if is_rhyme and pos == length - 1:
            candidates = [c for c in rhyme_pool if c not in used] or rhyme_pool
            ch = rng.choice(candidates)
        elif pos == length - 1 and last_prefer_ze:
            candidates = [c for c in ze_pool if c not in used] or ze_pool
            ch = rng.choice(candidates)
        else:
            # 二四字相对近似：偶数位偏平、奇数位偏仄（宽松不强制）
            prefer_ping = (pos % 2 == 0)
            pool = (ping_pool if prefer_ping else ze_pool)
            candidates = [c for c in pool if c not in used]
            if not candidates:
                candidates = (ze_pool if prefer_ping else ping_pool)
            ch = rng.choice(candidates or ["天"])
        chars.append(ch)
        used.add(ch)
    return "".join(chars)


def _try_llm_generate(llm_client: Any, style: str, theme: str, rhyme: str | None) -> str:
    prompt = (
        f"请创作一首「{style}」，主题「{theme}」，每行字数与韵位符合该体式，"
        f"全诗只输出正文、每句一行、不加标题和解释。"
        + (f"要求押「{rhyme}」所在韵部。" if rhyme else "请用平声韵。")
    )
    if hasattr(llm_client, "chat"):
        result = llm_client.chat([{"role": "user", "content": prompt}])
    elif hasattr(llm_client, "complete"):
        result = llm_client.complete(prompt)
    else:
        raise TypeError("llm_client 缺少 chat/complete 方法")
    if isinstance(result, dict):
        result = result.get("text") or result.get("content") or ""
    return str(result or "").strip()


def _split_text(text: str) -> list[str]:
    import re as _re
    lines = []
    for raw in _re.split(r"\n+", text):
        clean = "".join(ch for ch in raw if ch not in "，。！？；：、,.!?;:")
        if clean.strip():
            lines.append(clean.strip())
    return lines


def _infer_style(lines: list[str]) -> str | None:
    lengths = [len(line) for line in lines]
    for key, profile in _METER_PROFILES.items():
        if len(lines) == profile["line_count"] and lengths == profile["line_lengths"]:
            return key
    return None


def rewrite_poem(text: str, target: str = "prosody_fix", style: str | None = None) -> dict[str, Any]:
    """基于 validate_meter / evaluate_prosody 的失败项给出逐处改写建议。

    每处建议 1-3 个候选字/句，附原因。target 当前仅支持 prosody_fix（格律修复）。
    """
    clean_lines = _split_text(text)
    resolved_style = style or _infer_style(clean_lines) or "五绝"
    meter = validate_meter(text, resolved_style)
    prosody = evaluate_prosody(clean_lines, resolved_style)

    issues: list[dict[str, Any]] = []
    rule_map = {r["name"]: r for r in meter["rules"]}

    # ── 字数问题 ──
    length_rule = rule_map.get("line_lengths")
    if length_rule and length_rule["status"] == "fail":
        for item in length_rule.get("items", []):
            if item.get("ok"):
                continue
            expected = item.get("expected")
            actual = item.get("actual")
            line_idx = item["line"] - 1
            if expected is None:
                continue
            if actual < expected:
                need = expected - actual
                suggestions = [{
                    "text": f"在句中补 {need} 字（可用 平/仄 各 1 候选补足）："
                            + "、".join(_pick_fill_chars(need)),
                    "reason": f"第{item['line']}句当前 {actual} 字，{resolved_style} 需 {expected} 字",
                }]
            else:
                drop = actual - expected
                suggestions = [{
                    "text": f"删除句中冗余 {drop} 字（保留实词），使成 {expected} 字",
                    "reason": f"第{item['line']}句当前 {actual} 字，超出 {expected} 字",
                }]
            issues.append({
                "position": line_idx + 1, "line_index": line_idx, "severity": "fail",
                "issue": f"字数不符（{actual}≠{expected}）", "suggestions": suggestions,
            })

    # ── 押韵问题 ──
    rhyme_rule = rule_map.get("rhyme_consistency")
    if rhyme_rule and rhyme_rule["status"] == "fail":
        rhyme_items = rhyme_rule.get("items", [])
        known = [it for it in rhyme_items if it.get("ok")]
        groups = {it["rhyme_group"] for it in known}
        majority = None
        if groups:
            majority = sorted(groups)[0] if len(groups) > 1 else list(groups)[0]
        for it in rhyme_items:
            pos = it["position"]
            if not it.get("ok"):
                suggestions = [{
                    "text": "该韵脚字未收录平水韵，请改押本诗主流韵部（可先用韵部候选检索）",
                    "reason": f"第{pos}句韵脚「{it.get('char')}」韵部未知",
                }]
            elif majority and it.get("rhyme_group") != majority:
                candidates = find_rhyme_candidates(rhyme_items[0].get("char") or "风", 3)
                suggestions = [{
                    "text": "、".join(candidates) if candidates else "查韵部候选后替换",
                    "reason": f"第{pos}句韵脚「{it.get('char')}」属 {it.get('rhyme_group')}，"
                              f"与主流韵部 {majority} 不一致，建议改押 {majority}",
                }]
            else:
                continue
            issues.append({
                "position": pos, "line_index": pos - 1, "severity": "fail",
                "issue": f"韵脚出韵（{it.get('rhyme_group')}）", "suggestions": suggestions,
            })

    # ── 对/粘问题 ──
    key_rule = rule_map.get("key_positions")
    if key_rule and key_rule["status"] == "warn":
        for item in key_rule.get("items", []):
            issues.append({
                "position": 0, "line_index": 0, "severity": "warn",
                "issue": item.get("type", "对粘问题"),
                "suggestions": [{
                    "text": "调整相关句第 2 字为与当前相反的平仄字（二四六分明原则）",
                    "reason": item.get("detail", ""),
                }],
            })

    # ── 拗句提醒 ──
    notes_rule = rule_map.get("prosody_notes")
    if notes_rule and notes_rule["status"] == "warn":
        for item in notes_rule.get("items", []):
            issues.append({
                "position": 0, "line_index": 0, "severity": "warn",
                "issue": item.get("type", "拗句"),
                "suggestions": [{
                    "text": "调整句末第 3 字为相反平仄以破三平/三仄脚",
                    "reason": item.get("detail", ""),
                }],
            })

    return {
        "target": target,
        "style": resolved_style,
        "original_text": text,
        "issues": issues,
        "issue_count": len(issues),
        "note": "改写建议基于确定性格律规则生成；候选人需作者结合语义取舍（启发式，非大模型）",
    }


def _pick_fill_chars(n: int) -> list[str]:
    """返回 n 个常见 平/仄 填充候选字。"""
    if n <= 0:
        return []
    ping, ze = _known_pool(_GENERIC_POOL)
    picks = []
    for i in range(n):
        pool = ping if i % 2 == 0 else ze
        picks.append(pool[i % len(pool)] if pool else "天")
    return picks
