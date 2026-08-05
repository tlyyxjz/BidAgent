"""BidAgent 金标标注工具后端

v4.1 §10.4 测试集需人类标注员标注。
本服务提供文档加载、标注保存、导出金标JSON的后端API。

启动: python annotation_tool/launcher.py --port 8765
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = Path(__file__).resolve().parent
ANNOTATIONS_DIR = TOOL_DIR / "annotations"
RAW_DIRS = [ROOT / "_w3_raw", ROOT / "_w4_raw"]

app = FastAPI(title="BidAgent 金标标注工具")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def parse_raw(text: str) -> tuple[dict, str]:
    lines = text.split("\n", 4)
    meta: dict[str, str] = {}
    if len(lines) >= 1:
        meta["title"] = lines[0].lstrip("# ").strip()
    if len(lines) >= 2:
        meta["source_url"] = lines[1].replace("# URL:", "").strip()
    if len(lines) >= 3:
        meta["notice_type"] = lines[2].replace("# Type:", "").strip()
    if len(lines) >= 4:
        meta["fetched"] = lines[3].replace("# Fetched:", "").strip()
    body = lines[4] if len(lines) > 4 else ""
    return meta, body


def list_docs() -> list[dict]:
    docs: list[dict] = []
    for raw_dir in RAW_DIRS:
        if not raw_dir.exists():
            continue
        for fp in sorted(raw_dir.glob("*.txt")):
            doc_id = fp.stem
            ann_path = ANNOTATIONS_DIR / f"{doc_id}.json"
            status = "done" if ann_path.exists() else "pending"
            try:
                text = fp.read_text(encoding="utf-8")
                lines = text.split("\n", 3)
                title = lines[0].lstrip("# ").strip() if len(lines) > 0 else doc_id
                notice_type = (
                    lines[2].replace("# Type:", "").strip()
                    if len(lines) > 2
                    else "other"
                )
            except Exception:  # noqa: BLE001
                title, notice_type = doc_id, "other"
            docs.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "type": notice_type,
                    "status": status,
                }
            )
    return docs


def load_doc(doc_id: str) -> tuple[Path, dict, str] | None:
    for raw_dir in RAW_DIRS:
        fp = raw_dir / f"{doc_id}.txt"
        if fp.exists():
            text = fp.read_text(encoding="utf-8")
            meta, body = parse_raw(text)
            return fp, meta, body
    return None


class AnnotationPayload(BaseModel):
    fields: dict[str, Any]
    annotator: str = "human"


@app.get("/api/docs")
def get_docs():
    return list_docs()


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: str):
    result = load_doc(doc_id)
    if result is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    _fp, meta, body = result
    return {
        "doc_id": doc_id,
        "title": meta.get("title", ""),
        "content": body,
        "meta": meta,
    }


@app.post("/api/docs/{doc_id}/annotations")
def save_annotation(doc_id: str, payload: AnnotationPayload):
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    ann_path = ANNOTATIONS_DIR / f"{doc_id}.json"
    record = {
        "document_id": doc_id,
        "annotator": payload.annotator,
        "fields": payload.fields,
    }
    ann_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "doc_id": doc_id}


@app.get("/api/annotations")
def get_annotations():
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for fp in sorted(ANNOTATIONS_DIR.glob("*.json")):
        try:
            out.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


@app.get("/api/export")
def export():
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    docs: list[dict] = []
    for fp in sorted(ANNOTATIONS_DIR.glob("*.json")):
        try:
            docs.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return JSONResponse(content=docs)


@app.get("/")
def index():
    idx = TOOL_DIR / "index.html"
    if not idx.exists():
        raise HTTPException(404, "index.html 不存在")
    return FileResponse(idx)


@app.get("/{static_file}")
def static(static_file: str):
    allowed = {"index.html", "schema.js", "style.css", "sample_data.js"}
    if static_file not in allowed:
        raise HTTPException(404, f"文件不存在: {static_file}")
    fp = TOOL_DIR / static_file
    if not fp.exists():
        raise HTTPException(404, f"文件不存在: {static_file}")
    return FileResponse(fp)


def main() -> None:
    parser = argparse.ArgumentParser(description="BidAgent 金标标注工具后端")
    parser.add_argument("--port", type=int, default=8765, help="服务端口")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址")
    args = parser.parse_args()
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"标注工具启动: http://{args.host}:{args.port}")
    print(f"标注目录: {ANNOTATIONS_DIR}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
