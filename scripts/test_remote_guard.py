# -*- coding: utf-8 -*-
"""remote_guard 远程看家与寻物抓拍单元测试（目标提取、多机位扫描、拼图与声明式快照配置）"""

import io
import os
import sys
from unittest.mock import patch
from PIL import Image, ImageDraw

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from skills.remote_guard import main as rg
from skills.feishu_bot import main as fb
from core.skill_manager import SkillManager
from core.agent_reasoner import _has_visual_snapshot, is_skill_failure


def _make_color_jpg(rgb):
    img = Image.new("RGB", (320, 240), rgb)
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 200, 180], fill=(255 - rgb[0], rgb[1], 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_target_extraction_and_directions():
    cases = [
        ("帮我找一下猫", "猫"),
        ("帮我拍一张猫的照片", "猫"),
        ("看看我家橘猫在干嘛", "橘猫"),
        ("找找遥控器在哪", "遥控器"),
        ("看看家", ""),
        ("拍张照", ""),
        ("向右看看", ""),
        ("环顾一下客厅", ""),
    ]
    for query, expected in cases:
        got = rg._extract_target_from_question(query)
        assert got == expected, f"extract({query!r}) -> {got!r}, expected {expected!r}"

    assert rg._resolve_direction("环顾四周")[0] == "scan"
    assert rg._resolve_direction("left")[0] == "left"
    assert rg._resolve_direction("right")[0] == "right"
    assert rg._resolve_direction("up")[0] == "up"
    assert rg._resolve_direction("down")[0] == "down"
    assert rg._resolve_direction("center")[0] == "center"


def test_patrol_collage_and_static_detection():
    f_center = _make_color_jpg((20, 40, 80))
    f_left = _make_color_jpg((180, 110, 30))
    f_right = _make_color_jpg((40, 160, 70))
    f_down = _make_color_jpg((130, 50, 150))

    assert rg._is_static_duplicate(f_center, f_center) is True
    assert rg._is_static_duplicate(f_center, f_left) is False

    collage = rg._build_patrol_collage([
        ("正前 90°", 90, 90, f_center),
        ("左侧 135°", 135, 92, f_left),
        ("右侧 45°", 45, 92, f_right),
        ("俯视 108°", 90, 108, f_down),
    ])
    im_c = Image.open(io.BytesIO(collage))
    assert im_c.size == (640, 480)


def test_target_search_hits_and_misses():
    f_center = _make_color_jpg((20, 40, 80))
    f_left = _make_color_jpg((180, 110, 30))
    f_right = _make_color_jpg((40, 160, 70))
    f_down = _make_color_jpg((130, 50, 150))

    frame_map = {
        (90.0, 90.0): f_center,
        (135.0, 92.0): f_left,
        (149.0, 92.0): f_left,
        (45.0, 92.0): f_right,
        (90.0, 108.0): f_down,
    }
    moves = []
    pushed = []

    def fake_move(pan, tilt, desc, settle_sec=0.45):
        moves.append((pan, tilt))
        best = min(frame_map.items(), key=lambda kv: abs(kv[0][0] - pan) + abs(kv[0][1] - tilt))[1]
        return best, True, "ok", "physical"

    def fake_check(img_bytes, target, view_desc, cfg):
        if img_bytes == f_left:
            return {"found": True, "confidence": 0.95, "position": "left", "summary": "猫咪正在左侧沙发扶手上晒太阳呢～"}
        return {"found": False, "confidence": 0.05, "position": "center", "summary": "这里没有猫"}

    def fake_push(img_bytes, summary, dir_desc, is_live, target_id, id_type, card_title=None, section_title=None):
        pushed.append({"title": card_title, "summary": summary, "bytes": img_bytes})
        return True, ""

    with patch.object(rg, "_move_head_and_capture", side_effect=fake_move), \
         patch.object(rg, "_vlm_check_target", side_effect=fake_check), \
         patch.object(rg, "_push_feishu_card", side_effect=fake_push):
        out = rg.run(direction="center", question="帮我找一下猫", target="猫")
        assert "寻物抓拍·猫已完成" in out
        assert not is_skill_failure(out)
        assert len(pushed) == 1 and pushed[0]["bytes"] == f_left
        assert "猫" in pushed[0]["title"]
        assert moves[-1] == (149.0, 92.0)

    pushed.clear()
    moves.clear()
    with patch.object(rg, "_move_head_and_capture", side_effect=fake_move), \
         patch.object(rg, "_vlm_check_target", return_value={"found": False, "confidence": 0.0, "position": "center", "summary": ""}), \
         patch.object(rg, "_inspect_scene_with_vlm", return_value="四向视角环境正常"), \
         patch.object(rg, "_push_feishu_card", side_effect=fake_push):
        out2 = rg.run(direction="center", question="帮我找一下猫")
        assert not is_skill_failure(out2), f"Unexpected failure tag in: {out2}"
        assert moves[-1] == (90.0, 90.0)
        assert "没有看到「猫」" in pushed[0]["summary"]
        assert Image.open(io.BytesIO(pushed[0]["bytes"])).size == (640, 480)


def test_declarative_skill_meta_and_feishu_trigger():
    sm = SkillManager(storage_dir=os.path.join(ROOT_DIR, "data"))
    meta_rg = sm.resolve_plugin_meta("remote_guard")
    assert meta_rg.get("timeout_seconds") == 30.0
    assert meta_rg.get("visual_snapshot") is True
    assert len(meta_rg.get("prompt_desc", "")) <= 78
    assert _has_visual_snapshot([{"action": "remote_guard"}], "ok", sm) is True
    assert _has_visual_snapshot([{"action": "weather"}], "晴", sm) is False


if __name__ == "__main__":
    test_target_extraction_and_directions()
    test_patrol_collage_and_static_detection()
    test_target_search_hits_and_misses()
    test_declarative_skill_meta_and_feishu_trigger()
    print("ALL_REMOTE_GUARD_TESTS_PASSED")
