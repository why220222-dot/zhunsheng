# -*- coding: utf-8 -*-
"""把 app 目录推上 GitHub，不需要装 git。

用法（PowerShell）：
  $env:GITHUB_TOKEN='ghp_xxx'
  python deploy_github.py --repo zhunsheng --owner <你的用户名>

token 需要 repo 权限（Fine-grained token 勾 Contents: Read and write 即可）。
"""
import argparse
import base64
import json
import mimetypes
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

APP = pathlib.Path(__file__).resolve().parent

SKIP_DIRS = {"data", "tools", "__pycache__", "shots", ".git"}
SKIP_FILES = {".env"}
SKIP_SUFFIX = {".pyc", ".log", ".png", ".jpg", ".jpeg"}
SKIP_NAMES = {"state.json", "anchors_cache.json"}


def api(method, path, token, body=None):
    url = "https://api.github.com" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zhunsheng-deploy",
        **({"Content-Type": "application/json"} if data else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, raw[:400]


def collect():
    files = []
    for p in APP.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(APP)
        parts = set(rel.parts)
        if parts & SKIP_DIRS:
            continue
        if rel.name in SKIP_FILES or rel.name in SKIP_NAMES:
            continue
        if rel.suffix.lower() in SKIP_SUFFIX:
            continue
        if rel.name.startswith("_"):      # 临时补丁/测试脚本不上传
            continue
        files.append((str(rel).replace("\\", "/"), p))
    return sorted(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="zhunsheng")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    files = collect()
    print(f"准备上传 {len(files)} 个文件 -> {a.owner}/{a.repo}")
    for rel, _ in files[:10]:
        print("  ", rel)
    if len(files) > 10:
        print(f"   … 其余 {len(files)-10} 个")
    if a.dry or not token:
        if not token:
            print("\n[提示] 没设置 GITHUB_TOKEN，仅做了预演。")
        return

    st, body = api("POST", "/user/repos", token, {
        "name": a.repo, "private": bool(a.private), "auto_init": False,
        "description": "准绳 · 绩效评审智能体（MatrixNEXT 2026 参赛作品）"})
    if st == 201:
        print("仓库已创建:", body.get("html_url"))
    elif st == 422:
        print("仓库已存在，直接覆盖文件")
    else:
        print("创建仓库失败:", st, body)
        sys.exit(1)

    ok = fail = 0
    for rel, path in files:
        content = base64.b64encode(path.read_bytes()).decode()
        safe = urllib.parse.quote(rel)
        payload = {"message": "add " + rel, "content": content}
        # 文件已存在时更新必须带 sha
        st0, cur = api("GET", f"/repos/{a.owner}/{a.repo}/contents/{safe}", token)
        if st0 == 200 and isinstance(cur, dict) and cur.get("sha"):
            payload["sha"] = cur["sha"]
        st, body = api("PUT", f"/repos/{a.owner}/{a.repo}/contents/{safe}", token, payload)
        if st in (200, 201):
            ok += 1
        else:
            fail += 1
            print("  失败", rel, st, str(body)[:120])
    print(f"完成：成功 {ok}，失败 {fail}")
    print("仓库地址: https://github.com/%s/%s" % (a.owner, a.repo))


if __name__ == "__main__":
    main()
