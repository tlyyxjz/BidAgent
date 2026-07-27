import pytest
pytestmark = pytest.mark.skip(reason='W2-06 smoke test is a standalone script, not pytest. Run with: python tests/test_w2_06_smoke.py')

"""冒烟测试脚本 - 测试 W2-06 新增的 UI 和 API。"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

def test_endpoint(name, url, method="GET", data=None, expected_status=200):
    print(f"\n=== 测试 {name} ===")
    try:
        headers = {}
        req_data = None
        if data:
            req_data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()
            print(f"  状态码: {resp.status}")
            if resp.status != expected_status:
                print(f"  ❌ 期望 {expected_status}，实际 {resp.status}")
                return False
            try:
                json_data = json.loads(body)
                if "code" in json_data:
                    print(f"  code: {json_data['code']}")
                if "data" in json_data:
                    if isinstance(json_data["data"], dict) and "fields" in json_data["data"]:
                        print(f"  fields: {len(json_data['data']['fields'])}")
                        for f in json_data["data"]["fields"]:
                            ev_count = sum(
                                len(v.get("acceptable_evidence_spans", []))
                                for v in f.get("values", [])
                            )
                            print(f"    - {f['field_name']}: {len(f.get('values', []))} values, {ev_count} evidences")
                    elif isinstance(json_data["data"], dict) and "slots" in json_data["data"]:
                        print(f"  slots: {json_data['data']['slots']}")
            except Exception:
                print(f"  响应长度: {len(body)} 字符")
            print(f"  ✅ 通过")
            return True
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP 错误: {e.code} - {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        return False


def main():
    results = []

    results.append(test_endpoint("健康检查", f"{BASE}/health"))
    results.append(test_endpoint("UI 首页", f"{BASE}/ui"))
    results.append(test_endpoint("UI 招标列表页", f"{BASE}/ui/tenders"))
    results.append(test_endpoint("UI 详情页", f"{BASE}/ui/tenders/0?doc=tender_06_4e47868721c5"))
    results.append(test_endpoint("UI 聊天页", f"{BASE}/ui/chat"))
    results.append(test_endpoint("Demo 原文接口", f"{BASE}/ui/api/demo/raw?doc=tender_06_4e47868721c5"))
    results.append(test_endpoint("Demo 标注接口", f"{BASE}/ui/api/demo/annotation?doc=tender_06_4e47868721c5"))
    results.append(test_endpoint("Demo 文档列表", f"{BASE}/ui/api/demo/doc-list"))
    results.append(test_endpoint(
        "聊天 API",
        f"{BASE}/api/chat",
        method="POST",
        data={"message": "找上海最近7天的IT采购项目"},
    ))

    print(f"\n\n{'='*50}")
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    if passed == total:
        print("🎉 全部通过！")
    else:
        print("⚠️  有失败项，请检查")

    return passed == total


if __name__ == "__main__":
    main()
