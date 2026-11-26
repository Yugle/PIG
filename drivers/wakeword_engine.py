# -*- coding: utf-8 -*-
"""
PIG 机器人 - 统一离线语音唤醒引擎 (drivers/wakeword_engine.py)

双引擎自适应支持：
1. sherpa_cn (默认优先): 基于 sherpa-onnx WenetSpeech 3.3M int8 中文流式关键词唤醒，
   纯离线、超低内存，支持免训练热更新任意中文唤醒词 (如 "你好小猪"、"小猪小猪")。
2. openwakeword (兼容回退): 支持 hey_jarvis 等英文预训练唤醒模型。
"""

import os
import re
import shutil
import subprocess
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("WakeWordEngine")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SHERPA_MODEL_DIR = os.path.join(
    PROJECT_ROOT, "models", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
)

# 常用中文唤醒字 ppinyin 兜底映射表 (确保即使未安装 pypinyin，默认唤醒词也可 100% 离线转换)
BUILTIN_PPINYIN_MAP = {
    "你": "n ǐ",
    "好": "h ǎo",
    "小": "x iǎo",
    "猪": "zh ū",
    "同": "t óng",
    "学": "x ué",
    "助": "zh ù",
    "手": "sh ǒu",
    "醒": "x ǐng",
    "在": "z ài",
    "吗": "m a",
    "宝": "b ǎo",
    "贝": "b èi",
    "机": "j ī",
    "器": "q ì",
    "人": "r én",
    "胖": "p àng",
    "虎": "h ǔ",
}

ZH_INITIALS = (
    "zh", "ch", "sh",
    "b", "p", "m", "f", "d", "t", "n", "l",
    "g", "k", "h", "j", "q", "x",
    "r", "z", "c", "s", "y", "w"
)


def parse_keywords_list(raw_keywords: Any) -> List[str]:
    """将配置中的唤醒词(字符串或列表)标准化为去重非空列表"""
    if isinstance(raw_keywords, str):
        items = re.split(r"[,，;\n]+", raw_keywords)
    elif isinstance(raw_keywords, (list, tuple)):
        items = [str(x) for x in raw_keywords]
    else:
        items = []
    cleaned: List[str] = []
    for item in items:
        s = item.strip()
        if s and s not in cleaned:
            cleaned.append(s)
    return cleaned or ["你好小猪", "小猪小猪"]


def _load_valid_tokens(tokens_path: str) -> set:
    valid = set()
    if os.path.exists(tokens_path):
        with open(tokens_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if parts:
                    valid.add(parts[0])
    return valid


def _split_syllable_to_ppinyin(syllable: str, valid_tokens: set) -> List[str]:
    """将单个带声调全拼音节 (如 'xiǎo') 按声母/韵母切分为 tokens.txt 中存在的 ppinyin token"""
    s = syllable.strip()
    if not s:
        return []
    if not valid_tokens or s in valid_tokens:
        return [s]

    for ini in ZH_INITIALS:
        if s.startswith(ini) and len(s) > len(ini):
            fin = s[len(ini):]
            if (not valid_tokens) or (ini in valid_tokens and fin in valid_tokens):
                return [ini, fin]
    return [s]


def build_ppinyin_keywords_file(
    keywords: List[str],
    tokens_path: str,
    output_path: str,
) -> bool:
    """
    将中文唤醒词列表转换为 sherpa-onnx KWS 所需的 keywords.txt。
    优先使用官方 sherpa-onnx-cli text2token；若不可用则回退 pypinyin / 内置映射。
    """
    if not keywords:
        return False

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    raw_lines = []
    custom_formatted = []
    for kw in keywords:
        if "@" in kw:
            custom_formatted.append(kw)
        else:
            raw_lines.append(kw)

    generated_lines: List[str] = list(custom_formatted)

    # 1. 尝试调用 sherpa-onnx-cli text2token --tokens-type ppinyin
    cli_bin = shutil.which("sherpa-onnx-cli")
    if cli_bin and raw_lines and os.path.exists(tokens_path):
        tmp_raw = output_path + ".raw.tmp"
        tmp_out = output_path + ".cli.tmp"
        try:
            with open(tmp_raw, "w", encoding="utf-8") as f:
                f.write("\n".join(raw_lines) + "\n")
            res = subprocess.run(
                [
                    cli_bin, "text2token",
                    "--tokens", tokens_path,
                    "--tokens-type", "ppinyin",
                    tmp_raw, tmp_out,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            if res.returncode == 0 and os.path.exists(tmp_out):
                with open(tmp_out, "r", encoding="utf-8") as f:
                    cli_lines = [ln.strip() for ln in f if ln.strip()]
                if len(cli_lines) == len(raw_lines):
                    for kw, tok_line in zip(raw_lines, cli_lines):
                        label = kw.replace(" ", "_")
                        if "@" in tok_line:
                            generated_lines.append(tok_line)
                        else:
                            generated_lines.append(f"{tok_line} @{label}")
                    raw_lines = []
        except Exception as e:
            logger.debug(f"sherpa-onnx-cli text2token 跳过: {e}")
        finally:
            for p in (tmp_raw, tmp_out):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

    # 2. 纯 Python 回退 (pypinyin + 内置高频唤醒字表)
    if raw_lines:
        valid_tokens = _load_valid_tokens(tokens_path)
        try:
            from pypinyin import Style, pinyin as py_pinyin
        except ImportError:
            py_pinyin = None

        for kw in raw_lines:
            parts: List[str] = []
            if py_pinyin is not None:
                syls = py_pinyin(kw, style=Style.TONE, errors="ignore")
                for item in syls:
                    if item and item[0]:
                        parts.extend(_split_syllable_to_ppinyin(item[0], valid_tokens))
            if not parts:
                for ch in kw:
                    if ch in BUILTIN_PPINYIN_MAP:
                        parts.extend(BUILTIN_PPINYIN_MAP[ch].split())
            if parts:
                label = kw.replace(" ", "_")
                generated_lines.append(f"{' '.join(parts)} @{label}")

    if not generated_lines:
        return False

    # 为含“你好”(n ǐ h ǎo) 的唤醒词自动追加口语连读变调 (n í h ǎo)，显著提升自然语调命中率
    extra_lines: List[str] = []
    for ln in generated_lines:
        if "n ǐ h ǎo" in ln:
            sandhi_ln = ln.replace("n ǐ h ǎo", "n í h ǎo")
            if sandhi_ln not in generated_lines and sandhi_ln not in extra_lines:
                extra_lines.append(sandhi_ln)
    generated_lines.extend(extra_lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(generated_lines) + "\n")
    return True


def _pick_onnx(model_dir: str, prefix: str) -> str:
    """优先选用 int8 量化版 ONNX (树莓派 3B 提速降内存)，否则回退全精度版"""
    candidates = sorted(
        f for f in os.listdir(model_dir)
        if f.startswith(prefix) and f.endswith(".onnx")
    )
    int8_files = [f for f in candidates if ".int8.onnx" in f]
    chosen = int8_files[0] if int8_files else (candidates[0] if candidates else "")
    return os.path.join(model_dir, chosen) if chosen else ""


class WakeWordEngine:
    """
    统一离线语音唤醒引擎：
    自动优先加载 sherpa-onnx 中文 KWS，若模型/库未就绪则平滑回退到 openWakeWord。
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self.backend: str = "none"
        self.kws = None
        self.stream = None
        self.oww_model = None
        self.keywords: List[str] = parse_keywords_list(self.cfg.get("wakeword_keywords"))
        self.model_label: str = str(self.cfg.get("wakeword_model", "hey_jarvis"))
        self._load()

    def _load(self):
        pref = str(self.cfg.get("wakeword_engine", "auto")).lower()
        model_dir = str(self.cfg.get("sherpa_kws_dir") or DEFAULT_SHERPA_MODEL_DIR)

        if pref in ["auto", "sherpa", "sherpa_cn"] and self._try_init_sherpa(model_dir):
            return

        if pref in ["auto", "openwakeword"] and self._try_init_openwakeword():
            return

        logger.warning("⚠️ [WakeWord] 未找到可用的离线唤醒引擎 (sherpa-onnx 中文模型或 openWakeWord)")

    def _try_init_sherpa(self, model_dir: str) -> bool:
        try:
            import sherpa_onnx
        except ImportError:
            return False

        if not os.path.isdir(model_dir):
            return False

        tokens = os.path.join(model_dir, "tokens.txt")
        encoder = _pick_onnx(model_dir, "encoder")
        decoder = _pick_onnx(model_dir, "decoder")
        joiner = _pick_onnx(model_dir, "joiner")
        if not (os.path.exists(tokens) and encoder and decoder and joiner):
            return False

        kw_file = os.path.join(PROJECT_ROOT, "data", "sherpa_keywords.txt")
        if not build_ppinyin_keywords_file(self.keywords, tokens, kw_file):
            fallback_kw = os.path.join(model_dir, "test_wavs", "test_keywords.txt")
            if os.path.exists(fallback_kw):
                kw_file = fallback_kw
            else:
                return False

        thresh = max(0.05, min(0.80, float(self.cfg.get("wakeword_threshold", 0.20))))
        score_boost = float(self.cfg.get("sherpa_keywords_score", 2.2))

        try:
            self.kws = sherpa_onnx.KeywordSpotter(
                tokens=tokens,
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                num_threads=2,
                keywords_file=kw_file,
                keywords_score=score_boost,
                keywords_threshold=thresh,
                provider="cpu",
            )
            self.stream = self.kws.create_stream()
            self.backend = "sherpa_cn"
            self.model_label = " / ".join(self.keywords)
            logger.info(f"🎙️ [WakeWord] 中文离线唤醒已就绪 (sherpa-onnx): {self.model_label}")
            return True
        except Exception as e:
            logger.warning(f"⚠️ [WakeWord] sherpa-onnx 初始化失败，将尝试回退: {e}")
            self.kws = None
            self.stream = None
            return False

    def _try_init_openwakeword(self) -> bool:
        try:
            from openwakeword.model import Model
            model_name = str(self.cfg.get("wakeword_model", "hey_jarvis"))
            if model_name in ["sherpa_cn", "auto", ""]:
                model_name = "hey_jarvis"
            self.oww_model = Model(wakeword_models=[model_name], inference_framework="onnx")
            self.backend = "openwakeword"
            self.model_label = model_name
            logger.info(f"🎙️ [WakeWord] 英文唤醒引擎就绪 (openWakeWord): {model_name}")
            return True
        except Exception as e:
            logger.debug(f"openWakeWord 初始化跳过: {e}")
            return False

    @property
    def is_ready(self) -> bool:
        return self.backend != "none"

    def predict(self, audio_int16) -> Tuple[float, str]:
        """输入 16kHz int16 numpy 单声道切片，返回 (置信分 [0~1], 命中唤醒词文本)"""
        if self.backend == "sherpa_cn" and self.kws and self.stream:
            import numpy as np
            samples = audio_int16.astype(np.float32) / 32768.0
            self.stream.accept_waveform(16000, samples)
            while self.kws.is_ready(self.stream):
                self.kws.decode_stream(self.stream)
            res = self.kws.get_result(self.stream)
            kw_str = res.strip() if isinstance(res, str) else str(getattr(res, "keyword", "")).strip()
            if kw_str:
                self.kws.reset_stream(self.stream)
                return 1.0, kw_str
            return 0.0, ""

        if self.backend == "openwakeword" and self.oww_model:
            preds = self.oww_model.predict(audio_int16)
            if not preds:
                return 0.0, ""
            best_label = max(preds, key=preds.get)
            return float(preds[best_label]), str(best_label)

        return 0.0, ""

    def reset(self):
        """清空流式解码状态缓存"""
        if self.backend == "sherpa_cn" and self.kws and self.stream:
            try:
                self.kws.reset_stream(self.stream)
            except Exception:
                pass
        elif self.backend == "openwakeword" and self.oww_model:
            try:
                self.oww_model.reset()
            except Exception:
                pass

    def reload(self, cfg: Dict[str, Any]):
        """热重载唤醒词或灵敏度配置"""
        self.cfg = cfg or {}
        self.keywords = parse_keywords_list(self.cfg.get("wakeword_keywords"))
        self.backend = "none"
        self.kws = None
        self.stream = None
        self.oww_model = None
        self._load()
