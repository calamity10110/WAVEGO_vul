import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from settings_api import SPEC, get_settings, validate_patch, apply_patch

BASE = {"video": {"default_res_w": 640, "default_res_h": 480, "default_quality": 20},
        "args_config": {"max_speed": 1.3},
        "base_config": {"robot_name": "WAVEGO"}}


def test_get_settings_flattens():
    s = get_settings(BASE)
    assert s["video.default_res_w"] == 640
    assert s["base_config.robot_name"] == "WAVEGO"
    assert s["args_config.mid_rate"] is None


def test_valid_patch_coerces():
    clean, errors = validate_patch({
        "video.default_quality": "55",
        "args_config.max_speed": 1.4,
        "base_config.robot_name": "  Rex  ",
    })
    assert errors == []
    assert clean == {"video.default_quality": 55,
                     "args_config.max_speed": 1.4,
                     "base_config.robot_name": "Rex"}


def test_unknown_key_rejected():
    clean, errors = validate_patch({"use_lidar": True})
    assert clean == {} and "unknown setting" in errors[0]


def test_range_rejections():
    for key, bad in (("video.default_quality", 0), ("video.default_quality", 101),
                     ("args_config.max_speed", 5), ("base_config.robot_name", ""),
                     ("base_config.robot_name", "x" * 33)):
        clean, errors = validate_patch({key: bad})
        assert clean == {} and errors, (key, bad)


def test_type_coercion_failure_rejected():
    clean, errors = validate_patch({"video.default_res_w": "wide"})
    assert clean == {} and errors


def test_non_dict_rejected():
    assert validate_patch([1, 2])[1]


def test_apply_patch_mutates_nested():
    cfg = {"video": {"default_res_w": 640}}
    apply_patch(cfg, {"video.default_res_w": 1280, "base_config.robot_name": "Rex"})
    assert cfg["video"]["default_res_w"] == 1280
    assert cfg["base_config"]["robot_name"] == "Rex"


def test_whitelist_covers_expected_surfaces():
    assert "video.default_quality" in SPEC
    assert "args_config.max_speed" in SPEC
    assert "base_config.robot_name" in SPEC
