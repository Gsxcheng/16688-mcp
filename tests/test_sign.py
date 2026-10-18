import json
import shutil
import subprocess
from pathlib import Path

import pytest

from open16688_mcp.sign import canonical_value, controller_action, plain_text, sign, signed_body

PHP = shutil.which("php")
VERIFY = Path(__file__).with_name("php_verify.php")

CASES = [
    ("/openApi/order/list", {"page_no": 1, "page_size": 20, "status": "", "platform": "", "keywords": ""}),
    ("/openApi/goods/create", {"name": "Gemini 18个月 卡密", "image": "https://example.com/a/b.png",
                               "price": 10.5, "cost_price": 10.0, "delivery_method": 1,
                               "description": "说明 & 符号 = / \\ \"引号\"", "agent_status": 0}),
    ("/openApi/stockSecretBatch/add", {"goods_no": "G1", "content": "AAAA-BBBB\nCCCC-DDDD", "first": 0}),
    ("/openApi/purchase/applyComplaint", {"trade_no": "T1", "images": ["https://x/1.png", "https://x/2.png"],
                                          "extra": {"b": [1, 2.5, True], "a": "中/文", "c": {}}, "flag": False}),
    ("/openApi/merchant/balance", {}),
    ("/openApi/stockSecret/invalidateAll", {"goods_no": "G1", "nested": {"z": None, "y": []}}),
]


@pytest.mark.skipif(PHP is None, reason="php not installed")
@pytest.mark.parametrize("path,params", CASES)
def test_signature_matches_official_php_sample(path, params):
    body = signed_body(path, params, "2088000000000000", "test-secret-123")
    raw = json.dumps(body, ensure_ascii=False)
    out = subprocess.run([PHP, str(VERIFY), path, "test-secret-123"], input=raw,
                         capture_output=True, text=True, check=True).stdout
    assert out == "OK", out


def test_controller_action_lowercases():
    assert controller_action("/openApi/stockSecret/invalidateAll") == ("stocksecret", "invalidateall")
    assert controller_action("/openApi/goodsCategory/changeStatus") == ("goodscategory", "changestatus")
    with pytest.raises(ValueError):
        controller_action("/api/x")


def test_canonical_values():
    assert canonical_value(True) == "true" and canonical_value(False) == "false"
    assert canonical_value(None) == "" and canonical_value("") == ""
    assert canonical_value(10.0) == "10" and canonical_value(0.1 + 0.2) == "0.3"
    assert canonical_value({"b": 1, "a": "中/文"}) == '{"a":"中/文","b":1}'
    assert canonical_value({}) == "[]"


def test_plain_text_layout():
    text = plain_text("post", "/openApi/order/list", {"b": 2, "a": 1, "sign": "x"})
    assert text == "POST\norder\nlist\na=1&b=2"
    assert len(sign("POST", "/openApi/order/list", {"a": 1}, "k")) == 64


def test_signed_body_overrides_reserved_keys():
    body = signed_body("/openApi/order/list", {"sign": "evil", "app_id": "evil"}, "id", "s", now=1, nonce="n" * 16)
    assert body["app_id"] == "id" and body["timestamp"] == "1" and body["nonce"] == "n" * 16
    assert body["sign"] == sign("POST", "/openApi/order/list", body, "s")
