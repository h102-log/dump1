#!/usr/bin/env python3
"""tree.json을 네온 도시 HTML 한 장으로 만든다. 배치·높이·밝기를 여기서 다 계산한다.

    python render.py tree.json -o sprawl.html
    python render.py --check tree.json      # 렌더 없이 배치만 검사

은유 (DIRECTION.md 「도시의 규칙」): 폴더 = 건물 껍질(tower), 파일 = 층(floor).
층은 건물 바닥부터 쌓이고, 하위 폴더 건물은 그 층 스택 위에 선다 (SPEC 12b).
"""
import argparse
import bisect
import json
import math
import os
import sys

# --- 눈으로 보고 조정하는 값들. 계산으로 맞출 수 있는 값이 아니다. ---
CITY = 220.0           # 도시 한 변 (월드 단위)
STREET = 0.5           # 부지 안쪽 여백 = 거리. 층도 껍질에서 이만큼 들어가 벽과 겹치지 않는다
STREET_FRAC = 0.03     # 껍질 여백은 부지 짧은 변의 이 비율까지 넓어진다 — 큰 폴더 사이는 대로, 작은 폴더 사이는 골목 (SPEC 10b)
MIN_BLOCK = 2.5       # 짧은 변이 이보다 작은 부지는 더 쪼개지 않는다 (SPEC 55)
LOG_BASE = 4.0
FLOOR_SCALE = 1.0      # 층 두께 배율. 6.0은 건물 하나의 높이를 위한 값이었고 이제 최대 110번 쌓인다
MIN_HEIGHT = 0.6       # size 0인 파일도 이만큼은 보인다 (층 두께 하한)
SIZE_FLOOR_Q = 0.05    # 이 분위수의 크기를 두께 0으로 본다
MIN_TOWER_H = 2.0      # 층도 자식도 없는 폴더의 빈 껍질 높이 (SPEC 34)
ROOF = 7.0             # 지붕 여유. template의 SIGN_MAX(5) + 여유 2 — 내려다볼 때도 부모·자식 지붕 간판이 y로 갈린다


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
    """부지 면적 가중치 = 그 아래 파일 개수. 빈 폴더도 보이도록 하한이 1이다."""
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

    재귀 대신 루프인 이유는 한 폴더에 자식이 수천 개면 재귀 깊이가 그만큼 늘기 때문이다.
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
    """rect를 안쪽으로 m씩 줄인다. 변이 음수가 되면 0으로 접는다 — 아주 작은 부지의 껍질은 보이지 않을 뿐이다."""
    x, y, w, h = rect
    return (x + m, y + m, max(w - 2 * m, 0.0), max(h - 2 * m, 0.0))


def thickness(size, floor):
    """층 두께. floor = 트리 내 하위 SIZE_FLOOR_Q 크기의 로그값. 이걸 빼야 크기 차이가 눈에 보인다.

    안 빼면 모든 층이 log(p5)만큼의 공통 밑단을 깔고 서서 도시가 평평해진다 —
    node_modules에서 p95/중앙이 1.29배까지 눌리는 것을 실측했다 (2026-08-30). (SPEC 53)
    """
    return max(MIN_HEIGHT, (math.log(size + 1, LOG_BASE) - floor) * FLOOR_SCALE)


def brightness(mtime, mtimes):
    """트리 안 mtime 분포에서의 순위. 가장 오래된 것이 0, 가장 최근이 1이다.

    경과일을 절대 시간으로 감쇠시키면 npm install 직후의 node_modules처럼 mtime이
    하루 안에 몰린 트리가 통째로 1.0이 된다 — 실측에서 건물의 100%가 최대 밝기였고
    고유 mtime은 6개뿐이었다 (2026-08-30). 순위로 재면 그 6개가 6단계로 갈린다.
    동률은 그 그룹의 중간 순위를 함께 받는다 — 27%를 차지하는 최빈 mtime 하나가
    그룹 최하위로 몰리면 그것대로 거짓말이 된다. (SPEC 18, 52)
    """
    n = len(mtimes)
    if n < 2:
        return 1.0
    lo = bisect.bisect_left(mtimes, mtime)
    hi = bisect.bisect_right(mtimes, mtime)
    return (lo + hi - 1) / 2.0 / (n - 1)


def walk(node, rect, norm, towers, floors, depth=0, base=0.0, lvl=0):
    """부지 rect에 폴더 node의 건물을 세우고 그 높이를 돌려준다.

    자식을 먼저 세우고 높이를 접어 올린다 (SPEC 12b) — 재귀가 자식 높이를 돌려주므로
    한 번의 순회로 아래에서 위로 접힌다. towers 는 전위 순회 순서라 루트가 0번이고,
    어떤 건물의 부모는 그 앞에서 depth 가 하나 작은 가장 가까운 건물이다 (check 가 그렇게 복원한다).

    lvl(급)은 하위 폴더가 둘 이상인 조상의 수다. depth 는 node_modules/three 같은 외길 사슬에
    두 단을 쓰므로 위계의 축으로 못 쓴다 — 대분류 build/examples/src 가 깊이 2 였다 (SPEC 13b).
    """
    mtimes, floor = norm
    # 껍질은 부지 경계에서 거리만큼 들어간다 (SPEC 10). 거리는 부지 크기에 비례한다 — 고정 0.5는
    # 첫 화면(카메라 거리 ~250)에서 몇 픽셀이라 대분류 경계가 안 읽혔다 (SPEC 10b, 2026-09-02 실측)
    x, z, w, d = shrink(rect, max(STREET, min(rect[2], rect[3]) * STREET_FRAC))
    idx = len(towers)
    tower = {"x": round(x, 3), "z": round(z, 3), "w": round(w, 3), "d": round(d, 3),
             "y": round(base, 3), "h": 0.0, "depth": depth, "lvl": lvl, "n": node["name"]}
    towers.append(tower)

    kids = node["children"]
    dirs = [k for k in kids if k["type"] == "dir"]
    files = [k for k in kids if k["type"] == "file"]
    if dirs and min(w, d) < MIN_BLOCK:
        # 더 쪼개면 폭이 0인 부지만 나온다. 여기서 멈추고 아래 파일을 전부 이 건물의 층으로 쌓는다 (SPEC 55)
        files, dirs = files_under(node), []

    # 층: 직속 파일을 바닥부터 쌓는다. 두께를 먼저 round 해서 더하면 y 도 3자리에서 정확히 맞는다.
    fx, fz, fw, fd = shrink((x, z, w, d), STREET)
    y = base
    for f in files:
        t = round(thickness(f["size"], floor), 3)
        floors.append({"x": round(fx, 3), "z": round(fz, 3), "w": round(fw, 3), "d": round(fd, 3),
                       "y": round(y, 3), "t": t, "b": round(brightness(f["mtime"], mtimes), 3),
                       "n": f["name"], "owner": idx})
        y += t
    stack = round(y - base, 3)

    # 자식 건물: 부지를 하위 폴더에만 squarify 로 나눠 (SPEC 9) 층 스택 위에 세운다 (SPEC 11d)
    tallest = 0.0
    if dirs:
        ws = [weight(k) for k in dirs]
        areas = [wk / sum(ws) * (w * d) for wk in ws]
        klvl = lvl + (1 if len(dirs) > 1 else 0)   # 갈림이 있어야 급이 오른다
        for kid, cell in zip(dirs, squarify(areas, x, z, w, d)):
            tallest = max(tallest, walk(kid, cell, norm, towers, floors, depth + 1, base + stack, klvl))

    h = round(stack + tallest + ROOF, 3) if (files or dirs) else MIN_TOWER_H
    tower["h"] = h
    return h


def layout(tree):
    files = files_under(tree)
    # 밝기도 두께도 «트리 안에서의 상대 위치»다. 절대 시각·절대 바이트로 재면
    # node_modules 같은 트리에서 대비가 통째로 사라진다 (brightness·thickness 참조).
    mtimes = sorted(f["mtime"] for f in files)
    # 빈 파일은 크기 분포의 대표가 아니다 — 0바이트 하나가 p5를 0으로 끌어내리면 뺄 밑단이
    # 사라져 도시가 도로 평평해진다. 이 레포에서 tree.json 하나가 실제로 그랬다 (2026-08-30).
    # 크기 0은 어차피 MIN_HEIGHT가 받는다.
    sizes = sorted(f["size"] for f in files if f["size"] > 0)
    floor = math.log(sizes[int(len(sizes) * SIZE_FLOOR_Q)] + 1, LOG_BASE) if sizes else 0.0
    towers, floors = [], []
    walk(tree, (-CITY / 2, -CITY / 2, CITY, CITY), (mtimes, floor), towers, floors)
    return towers, floors


EPS = 1e-6


def parents(towers):
    """전위 순회 + depth 로 부모 인덱스를 복원한다. 루트는 -1."""
    out, stack = [], []
    for i, t in enumerate(towers):
        del stack[t["depth"]:]
        out.append(stack[-1] if stack else -1)
        stack.append(i)
    return out


def _inside(a, b):
    return (a["x"] >= b["x"] - EPS and a["z"] >= b["z"] - EPS
            and a["x"] + a["w"] <= b["x"] + b["w"] + EPS and a["z"] + a["d"] <= b["z"] + b["d"] + EPS)


def _overlap_xz(a, b):
    return (a["x"] < b["x"] + b["w"] - EPS and b["x"] < a["x"] + a["w"] - EPS
            and a["z"] < b["z"] + b["d"] - EPS and b["z"] < a["z"] + a["d"] - EPS)


def check(towers, floors):
    """SPEC 14: 11a~11d 를 assert 로 검사한다.

    11a 형제 건물끼리 xz 에서 겹치지 않는다 / 11b 자식은 부모의 xz 안에 들어간다 /
    11c 같은 건물의 두 층은 y 에서 겹치지 않는다 / 11d 자식 건물의 바닥은 부모의 층 스택 윗면 이상이다.
    """
    par = parents(towers)
    sib = {}
    for i, t in enumerate(towers):
        assert t["w"] >= 0 and t["d"] >= 0 and t["h"] > 0, t
        if par[i] >= 0:
            assert _inside(t, towers[par[i]]), ("부모 이탈", t["n"], towers[par[i]]["n"])   # 11b
            sib.setdefault(par[i], []).append(i)
    # ponytail: 형제끼리만 전수 비교 — 쌍 수가 한 폴더의 자식 수에 묶인다 (SPEC 60)
    for idxs in sib.values():
        for a in range(len(idxs)):
            for c in range(a + 1, len(idxs)):
                assert not _overlap_xz(towers[idxs[a]], towers[idxs[c]]), \
                    ("형제 겹침", towers[idxs[a]]["n"], towers[idxs[c]]["n"])                # 11a
    top = {}   # owner -> 층 스택 윗면
    stacks = {}
    for f in floors:
        assert f["t"] > 0, f
        stacks.setdefault(f["owner"], []).append(f)
    for o, fl in stacks.items():
        fl.sort(key=lambda f: f["y"])
        for lo, hi in zip(fl, fl[1:]):
            assert lo["y"] + lo["t"] <= hi["y"] + EPS, ("층 겹침", lo["n"], hi["n"])        # 11c
        top[o] = fl[-1]["y"] + fl[-1]["t"]
    for i, t in enumerate(towers):
        if par[i] >= 0:
            p = towers[par[i]]
            assert t["y"] >= top.get(par[i], p["y"]) - EPS, ("층 위에 서지 않음", t["n"], p["n"])  # 11d


def d_many(now):
    """node_modules 흉내 합성 트리 — 얕고 넓은 부지가 배치에서 가장 잘 깨진다."""
    pkgs = [{"name": "pkg%02d" % i, "type": "dir", "children":
             [{"name": "f%d.js" % j, "type": "file", "size": j * 977, "mtime": now - j * 86400}
              for j in range(20)]} for i in range(31)]
    return {"name": "big", "type": "dir", "children": pkgs}


def _must_fail(fn, what):
    """검사기가 자기 기준으로 검사하면 아무것도 안 잡는다 — 일부러 깨뜨려 잡히는지 본다 (SPEC 51)."""
    try:
        fn()
    except AssertionError:
        return
    raise AssertionError("%s을 못 잡았다" % what)


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
    T, F = layout(tree)
    check(T, F)
    tw = {t["n"]: t for t in T}
    fl = {f["n"]: f for f in F}
    assert [t["n"] for t in T] == ["root", "empty", "sub"], [t["n"] for t in T]   # 폴더 하나 = 건물 하나 (SPEC 15)
    assert len(F) == 4, [f["n"] for f in F]                                       # 파일 하나 = 층 하나
    assert fl["zero.txt"]["t"] == MIN_HEIGHT, fl["zero.txt"]                      # SPEC 33
    assert fl["big.bin"]["t"] > fl["a"]["t"], (fl["big.bin"], fl["a"])            # SPEC 12
    assert fl["big.bin"]["b"] == 0.0, fl["big.bin"]                               # 가장 오래된 파일이 0
    assert fl["zero.txt"]["b"] == fl["b"]["b"], (fl["zero.txt"], fl["b"])         # 같은 mtime = 같은 밝기
    assert fl["a"]["b"] < fl["b"]["b"], (fl["a"], fl["b"])                        # SPEC 17
    assert fl["a"]["owner"] == T.index(tw["sub"]) and fl["zero.txt"]["owner"] == 0

    # 층은 바닥부터 쌓이고, 자식 건물은 층 스택 위에 선다 (SPEC 11d, 12b)
    stack = fl["zero.txt"]["t"] + fl["big.bin"]["t"]
    assert fl["zero.txt"]["y"] == 0.0 and fl["big.bin"]["y"] == fl["zero.txt"]["t"]
    assert tw["sub"]["y"] == round(stack, 3) and tw["empty"]["y"] == round(stack, 3), (tw["sub"], tw["empty"])
    assert tw["empty"]["h"] == MIN_TOWER_H, tw["empty"]                            # SPEC 34
    assert tw["sub"]["h"] == round(fl["a"]["t"] + fl["b"]["t"] + ROOF, 3), tw["sub"]
    assert tw["root"]["h"] == round(stack + tw["sub"]["h"] + ROOF, 3), tw["root"]  # 접어 올림
    assert tw["root"]["depth"] == 0 and tw["sub"]["depth"] == 1
    assert tw["root"]["lvl"] == 0 and tw["sub"]["lvl"] == 1 and tw["empty"]["lvl"] == 1   # 루트가 둘로 갈린다 (SPEC 13b)
    # 껍질은 부지 크기에 비례한 여백만큼, 층은 껍질에서 STREET 만큼 들어간다 (SPEC 10, 10b)
    m = max(STREET, CITY * STREET_FRAC)
    assert tw["root"]["x"] == -CITY / 2 + m and tw["root"]["w"] == CITY - 2 * m, tw["root"]
    assert fl["zero.txt"]["x"] == tw["root"]["x"] + STREET

    # mtime이 한 점에 몰린 트리에서도 밝기가 갈린다. node_modules 실측(2026-08-30)에서
    # 고유 mtime이 6개뿐인데 건물 100%가 밝기 1.0이었다 — 이 회귀가 그 재발을 잡는다.
    clump = {"name": "c", "type": "dir", "children": [
        {"name": "f%d" % i, "type": "file", "size": 100, "mtime": now - (i % 3)}
        for i in range(30)]}
    _, cf = layout(clump)
    bs = sorted({f["b"] for f in cf})
    assert len(bs) == 3, bs
    assert bs[-1] - bs[0] > 0.5, bs

    # 공통 밑단을 뺀다 (thickness의 floor). 안 빼면 큰 파일과 작은 파일의 두께가 2배도 안 벌어진다.
    tall = {"name": "t", "type": "dir", "children": (
        [{"name": "f%d" % i, "type": "file", "size": 4000 + i, "mtime": now} for i in range(20)]
        + [{"name": "huge", "type": "file", "size": 4 * 10 ** 6, "mtime": now}])}
    _, tf = layout(tall)
    th = {f["n"]: f["t"] for f in tf}
    assert th["huge"] > 5 * th["f0"], (th["huge"], th["f0"])

    # 빈 트리에서 0으로 나누지 않는다 (SPEC 56)
    t0, f0 = layout({"name": "x", "type": "dir", "children": []})
    assert f0 == [] and len(t0) == 1 and t0[0]["h"] == MIN_TOWER_H, (t0, f0)

    # 깊은 중첩: 부지가 MIN_BLOCK 아래로 내려가면 멈추고 아래 파일을 전부 층으로 쌓는다 (SPEC 55).
    # 접어 올림이 맞으면 어떤 건물도 루트 지붕을 뚫지 않는다.
    deep = {"name": "f", "type": "file", "size": 1, "mtime": now}
    for i in range(40):
        deep = {"name": "d%d" % i, "type": "dir", "children": [
            deep, {"name": "s%d" % i, "type": "file", "size": 1, "mtime": now}]}
    dT, dF = layout(deep)
    check(dT, dF)
    assert len(dF) == 41 and 1 < len(dT) < 41, (len(dT), len(dF))
    assert max(t["y"] + t["h"] for t in dT) == dT[0]["y"] + dT[0]["h"]
    assert len({f["owner"] for f in dF}) == len(dT)   # 멈춘 건물이 아래 파일을 전부 받았다
    # 외길 사슬이라 dT[i+1] 의 부모는 dT[i] 다. 큰 부지의 여백이 작은 부지의 여백보다 넓고, STREET 아래로는 안 내려간다 (SPEC 10b)
    assert dT[1]["x"] - dT[0]["x"] > dT[-1]["x"] - dT[-2]["x"] >= STREET, (dT[1]["x"] - dT[0]["x"], dT[-1]["x"] - dT[-2]["x"])
    assert all(t["lvl"] == 0 for t in dT), [t["lvl"] for t in dT]   # 외길 사슬은 급이 오르지 않는다 (SPEC 13b)

    # squarify 는 면적 내림차순 정렬을 전제한다. 정렬을 빼면 작은 조각이 짧은 변을 통째로
    # 먹어 종잇장이 된다 - 이 케이스에서 최악 종횡비가 25 대신 100 이 나온다.
    ratios = [max(w, h) / min(w, h) for _, _, w, h in squarify([1.0, 98.0, 1.0], 0, 0, 10, 10)]
    assert max(ratios) < 40, ratios

    # 폴더가 많은 트리에서도 형제가 겹치지 않는다. node_modules 흉내 — 얕고 넓은 폴더가 문제를 낸다.
    mT, mF = layout(d_many(now))
    check(mT, mF)
    assert len(mT) == 32 and len(mF) == 620, (len(mT), len(mF))
    assert {t["lvl"] for t in mT[1:]} == {1}, {t["lvl"] for t in mT[1:]}   # 형제 31개는 전부 1급

    # --- 검사기가 실제로 잡는지: 불변식 넷을 하나씩 깨뜨린다 (SPEC 51, wt3-tower.md B-5) ---
    # 11a: 여백을 크게 음수로 주면 형제 껍질이 서로 파고든다. 비례 여백(10b)이 max 로 음수를 삼키므로 둘 다 음수로 준다
    def neg_street():
        global STREET, STREET_FRAC
        keep, STREET, STREET_FRAC = (STREET, STREET_FRAC), -20.0, -1.0
        try:
            nT, nF = layout(tree)
        finally:
            STREET, STREET_FRAC = keep
        check(nT, nF)
    _must_fail(neg_street, "형제 겹침")
    # 11b: 자식을 부모 밖으로 밀어낸다
    out = [dict(t) for t in T]
    out[2]["x"] = out[0]["x"] + out[0]["w"]
    _must_fail(lambda: check(out, F), "부모 이탈")
    # 11c: 같은 건물의 두 층을 같은 y 에 놓는다
    dup = [dict(f) for f in F]
    dup[1]["y"] = dup[0]["y"]
    _must_fail(lambda: check(T, dup), "층 겹침")
    # 11d: 자식 건물을 부모 층 스택 안으로 내린다
    low = [dict(t) for t in T]
    low[2]["y"] = 0.0
    _must_fail(lambda: check(low, F), "층 위에 서지 않음")

    print("selftest ok  (%d towers, %d floors)" % (len(T), len(F)))


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


def emit(towers, floors, template, bundle):
    """JSON과 번들을 template.html 에 박아 자체 완결 HTML 한 장으로 만든다 (SPEC 3, 13)."""
    html = template.replace("/*TOWERS*/", js(towers))
    html = html.replace("/*FLOORS*/", js(floors))
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

    towers, floors = layout(load(args.tree))
    if args.check:
        check(towers, floors)
        print("%d towers, %d floors, invariants hold" % (len(towers), len(floors)), file=sys.stderr)
        return

    here = os.path.dirname(os.path.abspath(__file__))
    template = open(os.path.join(here, "template.html"), encoding="utf-8").read()
    bundle = open(os.path.join(here, "vendor", "three.bundle.js"), encoding="utf-8").read()
    html = emit(towers, floors, template, bundle)
    with open(args.o, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print("%s  (%d towers, %d floors, %.1f KB)" % (args.o, len(towers), len(floors),
                                                   len(html.encode()) / 1024), file=sys.stderr)


if __name__ == "__main__":
    main()
