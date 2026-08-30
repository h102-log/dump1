#!/usr/bin/env python3
"""폴더 트리를 크기·mtime만 담은 JSON으로 stdout에 뱉는다. 파일 내용은 열지 않는다.

    python scan.py [경로] > tree.json
"""
import argparse
import json
import os
import stat
import sys

EXCLUDE = {".git"}

SKIPPED = []  # stat/scandir 실패로 건너뛴 경로. 조용히 잘린 스캔을 눈에 보이게 하려고 센다.


def is_link(st):
    """symlink, junction, 그 밖의 reparse point.

    junction은 is_symlink()가 False인데 is_dir(follow_symlinks=False)는 True다 —
    심링크만 막으면 순환에 그대로 걸어 들어간다. POSIX에는 st_file_attributes가 없어 앞항만 본다.
    """
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def file_node(name, st):
    return {"name": name, "type": "file", "size": st.st_size, "mtime": int(st.st_mtime)}


def scan(path, name):
    """디렉토리 하나를 dir 노드로 만든다. 링크류는 따라가지 않는다 (순환 방지).

    링크 자체는 file 노드로 남긴다 — 거기 뭔가 있다는 사실은 도시에 남기고, 안으로 들어가지만 않는다.
    """
    # ponytail: 재귀. 링크를 안 따라가므로 깊이는 실제 폴더 깊이가 상한이다.
    try:
        with os.scandir(path) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        SKIPPED.append(path)
        entries = []  # 열 수 없는 폴더는 빈 구역으로 남긴다

    children = []
    for e in entries:
        if e.name in EXCLUDE:
            continue
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            SKIPPED.append(e.path)  # stat 실패한 항목만 건너뛰고 계속한다
            continue
        if not is_link(st) and stat.S_ISDIR(st.st_mode):
            children.append(scan(e.path, e.name))
        else:
            children.append(file_node(e.name, st))
    return {"name": name, "type": "dir", "children": children}


def count(node):
    if node["type"] == "file":
        return 1
    return 1 + sum(count(c) for c in node["children"])


def make_loop(tmp):
    """tmp 안에 tmp를 가리키는 디렉토리 링크를 만든다. 못 만들면 False."""
    try:
        os.symlink(tmp, os.path.join(tmp, "loop"), target_is_directory=True)
        return True
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt":
        # 심링크는 권한이 필요하지만 junction은 아니다. junction이 실제로 물리는 케이스다.
        import subprocess
        return subprocess.run(
            ["cmd", "/c", "mklink", "/J", os.path.join(tmp, "loop"), tmp],
            capture_output=True,
        ).returncode == 0
    return False


def selftest():
    import tempfile
    import time

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".git"))
        os.makedirs(os.path.join(tmp, "empty"))
        os.makedirs(os.path.join(tmp, "sub"))
        open(os.path.join(tmp, ".git", "HEAD"), "w").close()
        with open(os.path.join(tmp, "b.txt"), "wb") as f:
            f.write(b"x" * 42)
        open(os.path.join(tmp, "sub", "a.txt"), "w").close()
        looped = make_loop(tmp)

        t = scan(tmp, "root")  # 여기서 돌아오는 것 자체가 무한루프 없음의 증거다
        if looped:
            os.rmdir(os.path.join(tmp, "loop"))  # 링크를 먼저 끊어야 tmp 정리가 안전하다

    names = [c["name"] for c in t["children"]]
    by = {c["name"]: c for c in t["children"]}
    assert ".git" not in names, names
    assert names == sorted(names), names
    assert by["b.txt"]["size"] == 42, by["b.txt"]
    assert abs(by["b.txt"]["mtime"] - time.time()) < 60, by["b.txt"]
    assert "size" not in by["sub"] and "mtime" not in by["sub"], by["sub"]
    assert by["empty"]["children"] == [], by["empty"]
    assert by["sub"]["children"][0]["size"] == 0, by["sub"]
    assert not SKIPPED, SKIPPED
    if looped:
        assert by["loop"]["type"] == "file", by["loop"]  # 따라 들어가지 않았다
        assert count(t) == 6, count(t)  # root + b.txt + empty + loop + sub + a.txt

    print("selftest ok" + ("" if looped else "  (no dir link could be made - loop check skipped)"))


def main():
    ap = argparse.ArgumentParser(description="폴더 트리를 JSON으로 뱉는다. 파일 내용은 열지 않는다.")
    ap.add_argument("path", nargs="?", default=".", help="스캔할 폴더 (기본: 현재 폴더)")
    ap.add_argument("--selftest", action="store_true", help="임시 폴더로 자체 검증만 하고 끝낸다")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        sys.exit("scan.py: 폴더가 아니거나 읽을 수 없다: " + args.path)

    tree = scan(root, os.path.basename(root) or root)
    json.dump(tree, sys.stdout)  # ensure_ascii 기본값 유지 — 콘솔 인코딩과 무관하게 안전하다
    msg = "%d entries" % count(tree)
    if SKIPPED:
        msg += ", %d skipped (%s ...)" % (len(SKIPPED), SKIPPED[0])
    print(msg, file=sys.stderr)


if __name__ == "__main__":
    main()
