"""运行 annotation_tool/test.html 浏览器自动化测试。

使用 Playwright headless 模式打开 test.html，收集测试结果。
"""
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright


def main():
    test_html = pathlib.Path("annotation_tool/test.html").resolve()
    if not test_html.exists():
        print(f"ERROR: {test_html} 不存在", file=sys.stderr)
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(test_html.as_uri())
        page.wait_for_selector("#summary", timeout=10000)
        page.wait_for_selector("#results .test-case", timeout=5000)
        time.sleep(1)

        summary_el = page.locator("#summary")
        summary = summary_el.inner_text() if summary_el.count() > 0 else ""

        cases = page.locator(".test-case")
        n = cases.count()
        results = []
        for i in range(n):
            case = cases.nth(i)
            cls = case.get_attribute("class") or ""
            name = case.locator(".test-name").inner_text()
            err_el = case.locator(".test-error")
            err = err_el.inner_text() if err_el.count() > 0 else ""
            results.append({"name": name, "pass": "pass" in cls, "error": err})

        browser.close()

    passed = sum(1 for r in results if r["pass"])
    failed = sum(1 for r in results if not r["pass"])

    print("=" * 60)
    print("BidAgent 标注工具 - 浏览器自动化测试结果")
    print("=" * 60)
    if summary:
        print(summary)
    print(f"\nTOTAL={n} PASSED={passed} FAILED={failed}")
    if failed > 0:
        print("\n失败用例:")
        for r in results:
            if not r["pass"]:
                print(f"  X {r['name']}")
                if r["error"]:
                    print(f"    错误: {r['error']}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
