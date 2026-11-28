# -*- coding: utf-8 -*-
"""USB 麦克风声卡漂移自愈、优先级排序与 48kHz->16kHz 重采样单元测试"""

import os
import sys
import numpy as np
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.hardware_agent import HardwareAgent


def test_rank_input_devices_prefers_usb_mic_over_camera_and_hdmi():
    fake_pa = MagicMock()
    devices = [
        {"name": "bcm2835 Headphones: - (hw:0,0)", "maxInputChannels": 1, "defaultSampleRate": 48000.0},
        {"name": "USB Camera: Audio (hw:1,0)", "maxInputChannels": 1, "defaultSampleRate": 48000.0},
        {"name": "USB PnP Sound Device: Audio (hw:2,0)", "maxInputChannels": 1, "defaultSampleRate": 44100.0},
        {"name": "vc4-hdmi: - (hw:3,0)", "maxInputChannels": 2, "defaultSampleRate": 48000.0},
    ]
    fake_pa.get_device_count.return_value = len(devices)
    fake_pa.get_device_info_by_index.side_effect = lambda i: devices[i]

    ranked = HardwareAgent._rank_input_devices(fake_pa)
    assert ranked[0][1] == 2, f"Expected index 2 (USB PnP Sound Device), got {ranked}"
    assert ranked[-2][1] == 1  # Camera behind normal devices


def test_probe_falls_back_to_48k_and_resamples_to_16k():
    fake_pa = MagicMock()
    opened_stream = MagicMock()
    # 3840 samples @ 48kHz = 80ms PCM16 mono -> 7680 bytes
    pcm48 = (np.arange(3840, dtype=np.int16) % 2000).tobytes()
    opened_stream.read.return_value = pcm48

    def fake_open(**kw):
        if kw["rate"] == 16000:
            raise OSError("Invalid sample rate 16000")
        return opened_stream

    fake_pa.open.side_effect = fake_open

    class FakePyAudioModule:
        paInt16 = 8

    with patch.dict(sys.modules, {"pyaudio": FakePyAudioModule}):
        res = HardwareAgent._probe_and_open_input(
            fake_pa, 2, {"name": "USB PnP Sound Device", "maxInputChannels": 1, "defaultSampleRate": 48000.0}
        )
    assert res is not None
    stream, rate, ch, chunk = res
    assert rate == 48000 and ch == 1 and chunk == 3840

    agent = HardwareAgent.__new__(HardwareAgent)
    agent.audio_stream = stream
    agent._mic_rate = rate
    agent._mic_channels = ch
    agent._mic_chunk = chunk
    frame_16k = agent._read_mic_16k_frame()
    assert len(frame_16k) == 1280 * 2  # 80ms @ 16kHz PCM16 mono


if __name__ == "__main__":
    test_rank_input_devices_prefers_usb_mic_over_camera_and_hdmi()
    test_probe_falls_back_to_48k_and_resamples_to_16k()
    print("ALL_MIC_RECOVERY_TESTS_PASSED")
