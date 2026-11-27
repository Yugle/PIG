# -*- coding: utf-8 -*-
"""
PIG 机器人 - 高并发流式推理与即时断句音频合成流水线 (streaming_pipeline.py)
核心机制：
1. StreamingJsonReplyExtractor: 结构化 JSON 流式增量状态机提取器（避免读出 thought 和 skill 等内部字段）。
2. SentenceChunker: 依据中英文标点进行流式断句切片（8~18字最佳听觉分块）。
3. AsyncEdgeTTSWorker: 异步并行短句语音合成器，首句极速出声，后续在后台双缓冲队列无缝预合成。
"""

import os
import json
import time
import asyncio
import base64
import logging
import hashlib
import threading
from typing import Dict, Any, List, Optional, Generator, AsyncGenerator

logger = logging.getLogger("StreamingPipeline")

TTS_CACHE_DIR = "/tmp/pig_tts_cache"
os.makedirs(TTS_CACHE_DIR, exist_ok=True)

try:
    import edge_tts
except ImportError:
    edge_tts = None


class StreamingJsonReplyExtractor:
    """
    针对大模型 ReAct JSON 输出的增量字符流提取器：
    1. 实时增量提取 "thought" 字段，供前端即时呈现大脑思考，消除等待焦虑。
    2. 提取 "reply" 字段内的面向主人的口语文案，断句后供流式 TTS 与文本渲染。
    3. 过滤 skill 等内部规划字段，若检测到工具调用则静默语音，避免虚假播报。
    """
    STATE_BEFORE_REPLY = 0
    STATE_IN_REPLY = 1
    STATE_DONE = 2

    def __init__(self, punctuation_delimiters: str = "，。！？；\n!?;"):
        self.delims = set(punctuation_delimiters)
        self.state = self.STATE_BEFORE_REPLY
        self.full_raw_text = ""
        self.reply_buffer = ""
        self.reply_delta = ""
        self.full_reply_text = ""
        self.escape_next = False
        self.is_tool_step = False

        # 思考内容与前置 emotion / behaviors 增量提取器
        self.in_thought = False
        self.thought_buffer = ""
        self.thought_delta = ""
        self.thought_done = False
        self.escape_thought = False
        self.emotion_done = False
        self.behaviors_done = False
        self.behaviors_emitted: Optional[List[Dict[str, Any]]] = None
        self._emitted_sentence_count = 0

    def _proc_thought_char(self, ch: str):
        if self.escape_thought:
            real_ch = "\n" if ch == "n" else ("\t" if ch == "t" else ('"' if ch == '"' else ch))
            self.thought_delta += real_ch
            self.thought_buffer += real_ch
            self.escape_thought = False
            return
        if ch == "\\":
            self.escape_thought = True
            return
        if ch == '"':
            self.in_thought = False
            self.thought_done = True
            return
        self.thought_delta += ch
        self.thought_buffer += ch

    def pop_thought_delta(self) -> str:
        """弹出新累积的思考增量切片，供 SSE 实时推送到前端"""
        d = self.thought_delta
        self.thought_delta = ""
        return d

    def _try_extract_emotion(self):
        """流式前 5 个 token 提取前置 emotion 字段，零等待切表情"""
        if self.emotion_done or self.behaviors_done:
            return
        idx = self.full_raw_text.find('"emotion"')
        if idx == -1:
            return
        after = self.full_raw_text[idx + 9:]
        colon = after.find(":")
        if colon == -1:
            return
        val_part = after[colon + 1:].lstrip()
        if not val_part.startswith('"'):
            return
        end_q = val_part.find('"', 1)
        if end_q != -1:
            emo = val_part[1:end_q].strip().upper()
            self.emotion_done = True
            if emo:
                self.behaviors_emitted = [{
                    "emotion": emo, "action": "NOD", "pan": 90, "tilt": 88, "delay": 500
                }]

    def _try_extract_behaviors(self):
        """提取大模型输出的 behaviors 数组（兼容前置或置于 reply 之后）"""
        if self.behaviors_done:
            return
        idx = self.full_raw_text.find('"behaviors"')
        if idx == -1:
            return
        after = self.full_raw_text[idx + 11:]
        colon = after.find(":")
        if colon == -1:
            return
        arr_start = after.find("[", colon + 1)
        if arr_start == -1:
            return
        depth = 0
        in_str = False
        esc = False
        for pos, ch in enumerate(after[arr_start:], start=arr_start):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if not in_str:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        raw_arr = after[arr_start:pos + 1]
                        self.behaviors_done = True
                        try:
                            parsed = json.loads(raw_arr)
                            if isinstance(parsed, list) and parsed:
                                self.behaviors_emitted = parsed
                        except Exception:
                            pass
                        return

    def pop_behaviors(self) -> Optional[List[Dict[str, Any]]]:
        """弹出尚未发出的前置大模型 emotion / behaviors 列表"""
        b = self.behaviors_emitted
        self.behaviors_emitted = None
        return b

    def pop_reply_delta(self) -> str:
        """弹出尚未发出的实时打字文本增量（0等待上屏，独立于 TTS 断句缓冲）"""
        d = self.reply_delta
        self.reply_delta = ""
        return d

    def _has_active_skill_before(self, reply_idx: int) -> bool:
        before_reply = self.full_raw_text[:reply_idx]
        if '"skill"' in before_reply:
            idx = before_reply.find('"skill"')
            after = before_reply[idx + 7:]
            colon = after.find(":")
            if colon != -1:
                val = after[colon + 1:].strip()
                if val and not val.startswith("null") and not val.startswith("None") and not val.startswith("false"):
                    return True
        return False

    def feed(self, delta: str) -> List[str]:
        """
        向状态机喂入增量 Token 文本，当命中完整短句标点且达到最小字数时，立即返回切片句子。
        若检测到当前轮次为技能工具调用轮次，则静默缓冲，不向外发射语音切片，避免与后续真实执行结果冲突。
        """
        emitted_sentences = []
        for ch in delta:
            self.full_raw_text += ch

            # 思考内容增量检测与捕获
            if not self.thought_done:
                if self.in_thought:
                    self._proc_thought_char(ch)
                else:
                    idx = self.full_raw_text.find('"thought"')
                    if idx != -1:
                        after = self.full_raw_text[idx + 9:]
                        colon = after.find(":")
                        if colon != -1:
                            val_part = after[colon + 1:].lstrip()
                            if val_part.startswith('"'):
                                self.in_thought = True

            if self.state == self.STATE_BEFORE_REPLY:
                self._try_extract_emotion()
                self._try_extract_behaviors()
                # 兜底：如果模型未按要求输出 JSON (不以 { 开头)，自动平滑切换为纯文本流式输出
                trimmed = self.full_raw_text.lstrip()
                if trimmed and not trimmed.startswith("{") and len(trimmed) >= 8:
                    self.state = self.STATE_IN_REPLY
                    self.reply_buffer = ""
                    self.full_reply_text = ""
                    for sub_ch in trimmed:
                        sents = self._process_reply_char(sub_ch)
                        emitted_sentences.extend(sents)
                    continue

                if '"reply"' in self.full_raw_text:
                    idx = self.full_raw_text.rfind('"reply"')
                    after = self.full_raw_text[idx + 7:]
                    colon_idx = after.find(":")
                    if colon_idx != -1:
                        val_part = after[colon_idx + 1:].lstrip()
                        if val_part.startswith('"'):
                            self.state = self.STATE_IN_REPLY
                            self.reply_buffer = ""
                            self.full_reply_text = ""
                            # 处理双引号之后紧随的字符
                            for sub_ch in val_part[1:]:
                                sents = self._process_reply_char(sub_ch)
                                emitted_sentences.extend(sents)

            elif self.state == self.STATE_IN_REPLY:
                sents = self._process_reply_char(ch)
                emitted_sentences.extend(sents)

            else:
                # STATE_DONE 阶段：提取 reply 之后补全的 behaviors 序列
                self._try_extract_behaviors()

        return emitted_sentences

    def _process_reply_char(self, ch: str) -> List[str]:
        sents = []
        if self.escape_next:
            ch_real = "\n" if ch == "n" else ("\t" if ch == "t" else ch)
            self.reply_buffer += ch_real
            self.reply_delta += ch_real
            self.full_reply_text += ch_real
            self.escape_next = False
            return sents

        if ch == "\\":
            self.escape_next = True
            return sents

        if ch == '"':
            # reply 字段正常结束闭合
            self.state = self.STATE_DONE
            rem = self.reply_buffer.strip()
            if rem:
                sents.append(rem)
                self.reply_buffer = ""
            return sents

        self.reply_buffer += ch
        self.reply_delta += ch
        self.full_reply_text += ch

        # 智能平滑断句 (兼顾首句抢跑与后续不断流)
        # 0. 第 1 句抢跑切片：遇到首个标点且 >= 5 字立刻发出，与 LLM 后续吐字并行合成，显著压低首音等待
        # 1. 后续强句末标点 (。！？!?\n) 且字数 >= 16 字：切片发射
        # 2. 后续弱停顿标点 (，；,;)：积累到长意群 (>= 22 字) 才切片，保证语调流畅不断音
        is_major_delim = ch in "。！？!?\n"
        is_minor_delim = ch in "，；,;"
        buf_len = len(self.reply_buffer.strip())

        should_split = False
        if self._emitted_sentence_count == 0 and (is_major_delim or is_minor_delim) and buf_len >= 5:
            should_split = True
        elif is_major_delim and buf_len >= 16:
            should_split = True
        elif is_minor_delim and buf_len >= 22:
            should_split = True

        if should_split:
            sents.append(self.reply_buffer.strip())
            self.reply_buffer = ""
            self._emitted_sentence_count += 1

        return sents

    def flush(self) -> List[str]:
        """流结束时将缓冲区剩余的尾句全部冲刷出来"""
        rem = self.reply_buffer.strip()
        self.reply_buffer = ""
        if rem:
            return [rem]
        return []


class AsyncEdgeTTSStreamer:
    """
    基于 Edge-TTS 的高并发短句音频合成引擎
    """
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.voice = voice

    async def synthesize_chunk(self, text: str) -> bytes:
        """异步并发合成单句音频为 MP3 二进制流"""
        clean_text = (text or "").strip()
        if not clean_text or not edge_tts:
            return b""

        try:
            comm = edge_tts.Communicate(clean_text, self.voice)
            chunks = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)
        except Exception as e:
            logger.warning(f"Edge-TTS 流式单句合成失败 [{clean_text[:15]}]: {e}")
            return b""


class AsyncMiMoTTSStreamer:
    """
    基于小米 MiMo-V2.5-TTS 的高并发短句大模型语音合成引擎
    支持预置音色（如'冰糖'、'茉莉'）、音色设计与参考音频声音克隆，
    并在网络抖动或异常时自动降级到 Edge-TTS 兜底出声。
    """
    def __init__(
        self,
        api_key: str,
        voice: str = "冰糖",
        clone_audio: str = "",
        design_prompt: str = ""
    ):
        self.api_key = api_key
        self.voice = voice or "冰糖"
        self.clone_audio = clone_audio
        self.design_prompt = design_prompt
        self._clone_audio_b64 = None
        self._fallback_edge = AsyncEdgeTTSStreamer()
        self._thread_local = threading.local()

    def _sync_synthesize(self, text: str) -> bytes:
        clean_text = (text or "").strip()
        if not clean_text or not self.api_key:
            return b""

        # 0. 快速检查本地零延时 LRU/MD5 磁盘缓存
        cache_key = hashlib.md5(
            f"{self.voice}_{self.clone_audio}_{self.design_prompt}_{clean_text}".encode("utf-8")
        ).hexdigest()
        cache_file = os.path.join(TTS_CACHE_DIR, f"{cache_key}.mp3")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "rb") as cf:
                    cached_bytes = cf.read()
                    if cached_bytes:
                        return cached_bytes
            except Exception:
                pass

        import requests
        try:
            url = "https://api.xiaomimimo.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            from drivers.tts_engine import resolve_mimo_tts_params
            model, user_prompt, audio_voice, self._clone_audio_b64 = resolve_mimo_tts_params(
                voice=self.voice,
                clone_audio=self.clone_audio,
                design_prompt=self.design_prompt,
                clone_b64_cache=self._clone_audio_b64,
            )


            payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": clean_text}
                ],
                "audio": {
                    "format": "mp3"
                },
                "stream": False
            }
            if audio_voice is not None:
                payload["audio"]["voice"] = audio_voice

            # 单句完整合成，每线程复用 Keep-Alive 连接池减少重复 TLS 握手耗时
            sess = getattr(self._thread_local, "session", None)
            if sess is None:
                sess = requests.Session()
                self._thread_local.session = sess
            res = sess.post(url, json=payload, headers=headers, timeout=12)
            if res.status_code == 200:
                data = res.json()
                choices = data.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    audio_obj = msg.get("audio", {})
                    b64 = audio_obj.get("data", "")
                    if b64:
                        full_audio = base64.b64decode(b64)
                        try:
                            with open(cache_file, "wb") as cf:
                                cf.write(full_audio)
                        except Exception:
                            pass
                        return full_audio

            logger.warning(f"小米 MiMo 流式单句调用异常 [{res.status_code}]: {res.text[:120]}")
            return b""
        except Exception as e:
            logger.warning(f"小米 MiMo 请求失败 [{clean_text[:15]}]: {e}")
            return b""

    async def synthesize_chunk(self, text: str) -> bytes:
        """异步执行单句合成，若失败自动降级至 Edge-TTS 兜底"""
        audio_bytes = await asyncio.to_thread(self._sync_synthesize, text)
        if audio_bytes:
            return audio_bytes
        if self._fallback_edge:
            return await self._fallback_edge.synthesize_chunk(text)
        return b""


def get_tts_streamer(cfg: Optional[Dict[str, Any]] = None):
    """根据系统配置工厂式分发流式 TTS 引擎 (支持 小米 MiMo-TTS 预置/声线定制 与 Edge-TTS)"""
    from drivers.tts_engine import is_edge_voice
    cfg = cfg or {}
    mode = cfg.get("tts_mode", "edge")
    mimo_key = cfg.get("mimo_api_key", "")
    selected_voice = cfg.get("mimo_voice") or cfg.get("tts_voice") or cfg.get("edge_voice") or "冰糖"
    if is_edge_voice(selected_voice):
        return AsyncEdgeTTSStreamer(voice=selected_voice)
    if mode == "mimo" and mimo_key:
        clone_audio = cfg.get("mimo_clone_audio", "") if cfg.get("mimo_clone_enabled", False) else ""
        return AsyncMiMoTTSStreamer(
            api_key=mimo_key,
            voice=selected_voice,
            clone_audio=clone_audio,
            design_prompt=cfg.get("mimo_design_prompt", "")
        )
    return AsyncEdgeTTSStreamer(voice=cfg.get("edge_voice", "zh-CN-XiaoxiaoNeural"))


class StreamingAudioOrchestrator:
    """
    流式音频协调器：
    连接 LLM 句子流与 TTS 异步并发队列，
    实时生成包含 base64 MP3 音频切片的事件流
    """
    def __init__(self, streamer=None, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.streamer = streamer or AsyncEdgeTTSStreamer(voice=voice)

    async def stream_audio_events(self, sentence_generator) -> AsyncGenerator[Dict[str, Any], None]:
        seq = 0
        for event in sentence_generator:
            ev_type = event.get("type")
            if ev_type == "sentence":
                text = event.get("text", "").strip()
                if text:
                    seq += 1
                    t0 = time.time()
                    audio_bytes = await self.streamer.synthesize_chunk(text)
                    dt = time.time() - t0
                    b64 = base64.b64encode(audio_bytes).decode("utf-8") if audio_bytes else ""
                    yield {
                        "type": "audio_chunk",
                        "seq": seq,
                        "text": text,
                        "audio": b64,
                        "duration_ms": int(dt * 1000)
                    }
            else:
                # 透传 tool_start, observation, done 等控制事件
                yield event

