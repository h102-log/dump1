#!/usr/bin/env python3
"""tree.json을 네온 도시 HTML 한 장으로 만든다. 배치·높이·밝기를 여기서 다 계산한다.

    python render.py tree.json -o sprawl.html
    python render.py --check tree.json      # 렌더 없이 배치만 검사
"""
import argparse
import bisect
import json
import math
import os
import sys

# --- 눈으로 보고 조정하는 값들. 계산으로 맞출 수 있는 값이 아니다. ---
CITY = 220.0           # 도시 한 변 (월드 단위)
STREET = 0.5           # 구역 안쪽 여백 = 거리
MIN_BLOCK = 2.5        # 짧은 변이 이보다 작은 구역은 더 쪼개지 않는다 (STREET 두 겹 + 건물 자리)
BUILDING_FILL = 0.72   # 건물이 자기 칸에서 차지하는 비율. 나머지가 골목이다
LOG_BASE = 4.0
HEIGHT_SCALE = 6.0     # 밑단을 뺀 뒤라 예전 3.0보다 커야 스카이라인이 산다
MIN_HEIGHT = 0.6       # size 0인 파일도 이만큼은 보인다
SIZE_FLOOR_Q = 0.05    # 이 분위수의 크기를 높이 0으로 본다
TIER = 2.5             # 폴더 한 단의 높이. 깊이가 고도가 된다


def load(path):
    """BOM이 붙었거나 UTF-16으로 리다이렉트된 파일도 읽는다.

    json.loads는 bytes를 받으면 선두를 보고 utf-8 / utf-8-sig / utf-16 / utf-32를 스스로
    고른다 (json.detect_encoding). PowerShell `>` 가 무엇을 붙이든 여기서 걸리지 않는다.
    """
    with open(path, "rb") as f:
        return json.loads(f.read())


def files_under(node):
    if node["type"] == "file":
        return [node]
    out = []
    for c in node["children"]:
        out.extend(files_under(c))
    return out


def weight(node):
    """구역 면적 가중치 = 그 아래 파일 개수. 빈 폴더도 보이도록 하한이 1이다."""
    return max(1, len(files_under(node)))


def _worst(row, side):
    """Bruls et al. 의 worst aspect ratio. row는 면적 목록, side는 행이 놓이는 변."""
    s = sum(row)
    return max(side * side * max(row) / (s * s), s * s / (side * side * min(row)))


def squarify(areas, x, y, w, h):
    """면적 목록을 사각형에 채운다 (squarified treemap). 입력 순서대로 rect를 돌려준다.

    Bruls et al. 은 면적 내림차순을 전제로 한다. 정렬하지 않으면 작은 조각이 먼저 와서
    짧은 변을 통째로 차지하는 종잇장이 된다 — 정렬을 빼고 돌렸더니 파일 하나짜리
    .package-lock.json 이 폭 157 x 깊이 0.13 으로 나왔다. 종횡비를 잡는 것이 이 알고리즘의
    존재 이유이므로 정렬은 선택이 아니다. 배치가 끝나면 입력 순서로 되돌려 돌려준다.

    재귀 대신 루프인 이유는 한 폴더에 파일이 수천 개면 재귀 깊이가 그만큼 늘기 때문이다.
    """
    order = sorted(range(len(areas)), key=lambda i: -areas[i])
    rest = [areas[i] for i in order]
    placed = []
    while rest:
        if w <= 0 or h <= 0:
            placed.extend((x, y, 0.0, 0.0) for _ in rest)
            break
        side = min(w, h)
        row = [rest[0]]
        while len(row) < len(rest) and _worst(row + [rest[len(row)]], side) <= _worst(row, side):
            row.append(rest[len(row)])
        thick = sum(row) / side
        if w >= h:
            cy = y
            for a in row:
                placed.append((x, cy, thick, a / thick))
                cy += a / thick
            x += thick
            w -= thick
        else:
            cx = x
            for a in row:
                placed.append((cx, y, a / thick, thick))
                cx += a / thick
            y += thick
            h -= thick
        rest = rest[len(row):]
    out = [None] * len(areas)
    for i, rect in zip(order, placed):
        out[i] = rect
    return out


def shrink(rect, m):
    x, y, w, h = rect
    return (x + m, y + m, w - 2 * m, h - 2 * m)


def height(size, floor):
    """floor = 트리 내 하위 SIZE_FLOOR_Q 크기의 로그값. 이걸 빼야 크기 차이가 눈에 보인다.

    안 빼면 모든 건물이 log(p5)만큼의 공통 밑단을 깔고 서서 도시가 평평해진다 —
    node_modules에서 p95/중앙이 1.29배까지 눌리는 것을 실측했다 (2026-08-30).
    """
    return max(MIN_HEIGHT, (math.log(size + 1, LOG_BASE) - floor) * HEIGHT_SCALE)


def brightness(mtime, mtimes):
    """트리 안 mtime 분포에서의 순위. 가장 오래된 것이 0, 가장 최근이 1이다.

    경과일을 절대 시간으로 감쇠시키면 npm install 직후의 node_modules처럼 mtime이
    하루 안에 몰린 트리가 통째로 1.0이 된다 — 실측에서 건물의 100%가 최대 밝기였고
    고유 mtime은 6개뿐이었다 (2026-08-30). 순위로 재면 그 6개가 6단계로 갈린다.
    동률은 그 그룹의 중간 순위를 함께 받는다 — 27%를 차지하는 최빈 mtime 하나가
    그룹 최하위로 몰리면 그것대로 거짓말이 된다.
    """
    n = len(mtimes)
    if n < 2:
        return 1.0
    lo = bisect.bisect_left(mtimes, mtime)
    hi = bisect.bisect_right(mtimes, mtime)
    return (lo + hi - 1) / 2.0 / (n - 1)


def building(node, cell, norm, block, base):
    mtimes, floor = norm
    cx, cz, cw, cd = cell
    return {
        "x": round(cx + cw / 2, 3),
        "z": round(cz + cd / 2, 3),
        "y": round(base, 3),          # 자기 폴더 단의 윗면. 깊을수록 높다
        "w": round(max(cw * BUILDING_FILL, 0.0), 3),
        "d": round(max(cd * BUILDING_FILL, 0.0), 3),
        "h": round(height(node["size"], floor), 3),
        "b": round(brightness(node["mtime"], mtimes), 3),
        "n": node["name"],
        "_r": block,  # 소속 구역, 여백 포함. --check 에서만 쓰고 주입 전에 뗀다
    }


def grid(files, inner, norm, out, block, base):
    """더 쪼갤 수 없는 구역에 파일을 격자로 눕힌다 (squarify 중단 시)."""
    x, z, w, d = inner
    if not files or w <= 0 or d <= 0:
        return
    cols = max(1, round(math.sqrt(len(files) * w / d)))
    rows = math.ceil(len(files) / cols)
    cw, cd = w / cols, d / rows
    for i, f in enumerate(files):
        cell = (x + (i % cols) * cw, z + (i // cols) * cd, cw, cd)
        out.append(building(f, cell, norm, block, base))


def walk(node, rect, norm, buildings, blocks, depth=0):
    # 구역은 바닥부터 자기 단 윗면까지 솟은 블록이다. 깊이가 곧 고도라서, 어느 건물이
    # 어느 폴더 소속인지 멀리서도 계단으로 읽힌다. 루트도 한 단 두께를 갖는다.
    top = (depth + 1) * TIER
    blocks.append({"x": round(rect[0], 3), "z": round(rect[1], 3),
                   "w": round(rect[2], 3), "d": round(rect[3], 3),
                   "y": round(top, 3), "n": node["name"]})
    inner = shrink(rect, STREET)
    kids = node["children"]
    if not kids:
        return  # 빈 폴더는 건물 없는 빈 구역으로 남는다
    if min(inner[2], inner[3]) < MIN_BLOCK:
        # 더 쪼개면 폭이 음수인 구역이 나온다. 여기서 멈추고 아래 파일을 전부 눕힌다.
        grid(files_under(node), inner, norm, buildings, rect, top)
        return
    ws = [weight(k) for k in kids]
    area = inner[2] * inner[3]
    areas = [w / sum(ws) * area for w in ws]
    for kid, cell in zip(kids, squarify(areas, *inner)):
        if kid["type"] == "dir":
            walk(kid, cell, norm, buildings, blocks, depth + 1)
        else:
            buildings.append(building(kid, cell, norm, rect, top))


def layout(tree):
    files = files_under(tree)
    # 밝기도 높이도 «트리 안에서의 상대 위치»다. 절대 시각·절대 바이트로 재면
    # node_modules 같은 트리에서 대비가 통째로 사라진다 (brightness·height 참조).
    mtimes = sorted(f["mtime"] for f in files)
    # 빈 파일은 크기 분포의 대표가 아니다 — 0바이트 하나가 p5를 0으로 끌어내리면 뺄 밑단이
    # 사라져 도시가 도로 평평해진다. 이 레포에서 tree.json 하나가 실제로 그랬다 (2026-08-30).
    # 크기 0은 어차피 MIN_HEIGHT가 받는다.
    sizes = sorted(f["size"] for f in files if f["size"] > 0)
    floor = math.log(sizes[int(len(sizes) * SIZE_FLOOR_Q)] + 1, LOG_BASE) if sizes else 0.0
    buildings, blocks = [], []
    walk(tree, (-CITY / 2, -CITY / 2, CITY, CITY), (mtimes, floor), buildings, blocks)
    return buildings, blocks


def check(buildings):
    """SPEC 11: 어떤 두 건물도 겹치지 않고, 어떤 건물도 자기 구역을 넘지 않는다."""
    # ponytail: O(n^2) 전수 비교. 5,000개에서 느려지면 x축 정렬 스위프로 바꾼다.
    box = []
    for b in buildings:
        x0, z0 = b["x"] - b["w"] / 2, b["z"] - b["d"] / 2
        assert b["w"] >= 0 and b["d"] >= 0 and b["h"] > 0, b
        rx, ry, rw, rh = b["_r"]
        assert x0 >= rx - 1e-9 and x0 + b["w"] <= rx + rw + 1e-9, ("구역 이탈 x", b)
        assert z0 >= ry - 1e-9 and z0 + b["d"] <= ry + rh + 1e-9, ("구역 이탈 z", b)
        box.append((x0, z0, x0 + b["w"], z0 + b["d"], b))
    for i, a in enumerate(box):
        for c in box[i + 1:]:
            if a[0] < c[2] - 1e-9 and c[0] < a[2] - 1e-9 and a[1] < c[3] - 1e-9 and c[1] < a[3] - 1e-9:
                raise AssertionError(("겹침", a[4]["n"], c[4]["n"]))


def d_many(now):
    """node_modules 흉내 합성 트리 — 얕고 넓은 구역이 배치에서 가장 잘 깨진다."""
    pkgs = [{"name": "pkg%02d" % i, "type": "dir", "children":
             [{"name": "f%d.js" % j, "type": "file", "size": j * 977, "mtime": now - j * 86400}
              for j in range(20)]} for i in range(31)]
    return {"name": "big", "type": "dir", "children": pkgs}


def selftest():
    import time

    now = int(time.time())
    tree = {
        "name": "root", "type": "dir", "children": [
            {"name": "zero.txt", "type": "file", "size": 0, "mtime": now},
            {"name": "big.bin", "type": "file", "size": 10 ** 7, "mtime": now - 400 * 86400},
            {"name": "empty", "type": "dir", "children": []},
            {"name": "sub", "type": "dir", "children": [
                {"name": "a", "type": "file", "size": 100, "mtime": now - 30 * 86400},
                {"name": "b", "type": "file", "size": 100, "mtime": now},
            ]},
        ],
    }
    b, blocks = layout(tree)
    check(b)
    by = {x["n"]: x for x in b}
    assert len(b) == 4, [x["n"] for x in b]
    assert by["zero.txt"]["h"] == MIN_HEIGHT, by["zero.txt"]        # SPEC 33
    assert by["big.bin"]["h"] > by["a"]["h"], (by["big.bin"], by["a"])
    assert by["big.bin"]["b"] == 0.0, by["big.bin"]                 # 가장 오래된 파일이 0
    assert by["zero.txt"]["b"] == by["b"]["b"], (by["zero.txt"], by["b"])  # 같은 mtime = 같은 밝기
    assert by["a"]["b"] < by["b"]["b"], (by["a"], by["b"])          # SPEC 17
    assert len(blocks) == 3, blocks                                 # root + empty + sub

    # mtime이 한 점에 몰린 트리에서도 밝기가 갈린다. node_modules 실측(2026-08-30)에서
    # 고유 mtime이 6개뿐인데 건물 100%가 밝기 1.0이었다 — 이 회귀가 그 재발을 잡는다.
    clump = {"name": "c", "type": "dir", "children": [
        {"name": "f%d" % i, "type": "file", "size": 100, "mtime": now - (i % 3)}
        for i in range(30)]}
    cb, _ = layout(clump)
    bs = sorted({x["b"] for x in cb})
    assert len(bs) == 3, bs
    assert bs[-1] - bs[0] > 0.5, bs

    # 공통 밑단을 뺀다 (height의 floor). 안 빼면 큰 파일과 작은 파일의 높이가 2배도 안 벌어진다.
    tall = {"name": "t", "type": "dir", "children": (
        [{"name": "f%d" % i, "type": "file", "size": 4000 + i, "mtime": now} for i in range(20)]
        + [{"name": "huge", "type": "file", "size": 4 * 10 ** 6, "mtime": now}])}
    tb, _ = layout(tall)
    th = {x["n"]: x["h"] for x in tb}
    assert th["huge"] > 5 * th["f0"], (th["huge"], th["f0"])

    # 깊이가 곧 고도다 (SPEC 61). sub/ 안의 파일은 루트 파일보다 한 단 위에 선다.
    assert by["zero.txt"]["y"] == TIER, by["zero.txt"]
    assert by["a"]["y"] == 2 * TIER, by["a"]
    assert {k["n"]: k["y"] for k in blocks} == {"root": TIER, "empty": 2 * TIER, "sub": 2 * TIER}

    # 빈 트리에서 0으로 나누지 않는다 (SPEC 56)
    b0, k0 = layout({"name": "x", "type": "dir", "children": []})
    assert b0 == [] and len(k0) == 1 and k0[0]["y"] == TIER, (b0, k0)

    # 깊은 중첩: 구역이 MIN_BLOCK 아래로 내려가도 겹침이 없다 (SPEC 55)
    deep = {"name": "f", "type": "file", "size": 1, "mtime": now}
    for i in range(40):
        deep = {"name": "d%d" % i, "type": "dir", "children": [
            deep, {"name": "s%d" % i, "type": "file", "size": 1, "mtime": now}]}
    db, _ = layout(deep)
    check(db)
    assert len(db) == 41, len(db)
    assert max(x["y"] for x in db) == 40 * TIER, max(x["y"] for x in db)

    # squarify 는 면적 내림차순 정렬을 전제한다. 정렬을 빼면 작은 조각이 짧은 변을 통째로
    # 먹어 종잇장이 된다 - 이 케이스에서 최악 종횡비가 25 대신 100 이 나온다.
    ratios = [max(w, h) / min(w, h) for _, _, w, h in squarify([1.0, 98.0, 1.0], 0, 0, 10, 10)]
    assert max(ratios) < 40, ratios

    # 파일이 많은 트리에서도 겹치지 않는다. node_modules 흉내 — 얕고 넓은 폴더가 문제를 낸다.
    many = d_many(now)
    mb, _ = layout(many)
    check(mb)
    assert len(mb) == 620, len(mb)

    # 여백을 크게 음수로 주면 실제 배치에서 위반이 난다 (SPEC 51).
    # 작은 음수(-1, -3)는 BUILDING_FILL 골목 여유가 흡수해 안 걸린다 — 실측으로 확인한 사실이다.
    global STREET
    keep, STREET = STREET, -20.0
    try:
        neg, _ = layout(tree)
    finally:
        STREET = keep
    try:
        check(neg)
    except AssertionError:
        pass
    else:
        raise AssertionError("음수 여백을 못 잡았다")

    # 검사기가 실제로 잡는지 (SPEC 51) — 겹치는 두 건물을 손으로 만들어 넣는다
    bad = [dict(by["a"], x=0, z=0, w=2, d=2, _r=(-1, -1, 4, 4)),
           dict(by["b"], x=1, z=1, w=2, d=2, _r=(-1, -1, 4, 4))]
    try:
        check(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError("겹침을 못 잡았다")

    print("selftest ok  (%d buildings, %d blocks)" % (len(b), len(blocks)))


def js(obj):
    r"""<script> 안에 그대로 박아도 안전한 JSON.

    파일명은 임의 문자열이라 `</script`, `<!--` 가 들어올 수 있다. `<` 를 통째로
    \u003c 로 바꾸면 둘 다 한 번에 막힌다. U+2028/2029 는 JS 소스에서 줄바꿈으로
    읽히므로 따로 뗀다. (SPEC 37)
    """
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def emit(buildings, blocks, template, bundle):
    """JSON과 번들을 template.html 에 박아 자체 완결 HTML 한 장으로 만든다 (SPEC 3)."""
    slim = [{k: v for k, v in b.items() if k != "_r"} for b in buildings]
    html = template.replace("/*BUILDINGS*/", js(slim))
    html = html.replace("/*BLOCKS*/", js(blocks))  # walk 가 이미 round 해서 넣는다
    return html.replace("/*THREE_BUNDLE*/", bundle)


def main():
    ap = argparse.ArgumentParser(description="tree.json을 네온 도시 HTML로 만든다.")
    ap.add_argument("tree", nargs="?", help="scan.py가 만든 tree.json")
    ap.add_argument("-o", default="sprawl.html", help="출력 HTML (기본: sprawl.html)")
    ap.add_argument("--check", action="store_true", help="렌더 없이 배치만 검사하고 끝낸다")
    ap.add_argument("--selftest", action="store_true", help="내장 트리로 자체 검증만 하고 끝낸다")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.tree:
        ap.error("tree.json 경로가 필요하다")

    buildings, blocks = layout(load(args.tree))
    if args.check:
        check(buildings)
        print("%d buildings, %d blocks, no overlap" % (len(buildings), len(blocks)), file=sys.stderr)
        return

    here = os.path.dirname(os.path.abspath(__file__))
    template = open(os.path.join(here, "template.html"), encoding="utf-8").read()
    bundle = open(os.path.join(here, "vendor", "three.bundle.js"), encoding="utf-8").read()
    html = emit(buildings, blocks, template, bundle)
    with open(args.o, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print("%s  (%d buildings, %.1f KB)" % (args.o, len(buildings), len(html.encode()) / 1024),
          file=sys.stderr)


if __name__ == "__main__":
    main()
