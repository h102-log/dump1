// three.js와 쓰는 애드온을 전역 SPRAWL 하나로 노출한다.
//
// r160부터 three는 UMD를 배포하지 않고, file:// 에서는 type="module" 의 import 가 CORS로
// 막힌다. 그래서 한 번 번들해서 커밋해 두고, render.py 가 그 내용을 출력 HTML에 인라인한다.
//
// 빌드는 최초 1회. three 버전을 올릴 때만 다시 돌린다:
//
//   npm i three
//   npx esbuild vendor/entry.js --bundle --format=iife --global-name=SPRAWL \
//       --minify --outfile=vendor/three.bundle.js
//
// 빌드에 쓴 버전: three 0.185.1

export * as THREE from "three";
export { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
export { RenderPass } from "three/addons/postprocessing/RenderPass.js";
export { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
export { PointerLockControls } from "three/addons/controls/PointerLockControls.js";

// 지금은 쓰지 않는다 — 젖은 바닥은 낮은 roughness + 블룸 흉내로 간다 (SPEC 20, S13).
// 그 결정을 번복할 때 번들을 다시 만들지 않아도 되도록 미리 넣어 둔다 (SPEC 49).
export { Reflector } from "three/addons/objects/Reflector.js";
