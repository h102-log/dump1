# sprawl (가칭)

프로젝트 폴더를 네온 도시로 렌더링한다. 코드는 읽지 않는다.
폴더는 구역, 파일은 건물, 크기는 높이, mtime은 창문 불빛.

방향은 [DIRECTION.md](DIRECTION.md), 확정된 요구는 [SPEC.md](SPEC.md)에 있다.

## 쓰는 법

```
python scan.py [폴더] > tree.json      # 크기와 mtime만 읽는다. 파일 내용은 열지 않는다
python render.py tree.json -o sprawl.html
```

`sprawl.html`은 자체 완결형이다. 다른 폴더로 옮겨 더블클릭해도, 네트워크가 끊겨 있어도 열린다.

조작: `WASD` 이동 · `Space`/`Shift` 고도 · 방향키 시선 · 화면 클릭하면 마우스로 시선 · `Esc` 해제.

## 남에게 보내기 전에

**출력 HTML에는 스캔한 폴더의 파일 이름이 전부 들어간다.** `.env`, `secret-key.pem` 같은
숨김 파일도 **이름은** 실린다 (내용은 읽지도 담지도 않는다). 도시 스크린샷을 보내는 것과
HTML 파일 자체를 보내는 것은 다르다 — 후자는 파일 목록을 통째로 넘기는 것이다.

## 점검

```
python scan.py --selftest
python render.py --selftest
python render.py --check tree.json     # 렌더 없이 배치만: 겹침·구역 이탈 assert
```

## vendor/ 다시 만들기

`vendor/three.bundle.js`는 커밋되어 있다. three 버전을 올릴 때만 다시 만든다 —
방법은 [vendor/entry.js](vendor/entry.js) 맨 위 주석에 있다.
