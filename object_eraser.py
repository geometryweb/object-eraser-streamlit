import base64
import io
import os
import re
import time
from pathlib import Path

# 클라우드 컨테이너(Streamlit Cloud) 스레드 생성 한도(pids.max) 초과 및 CPU 병목 방지
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

from PIL import Image
import cv2
import numpy as np
import streamlit as st

cv2.setNumThreads(1)

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "resources" / "light_inpaint.onnx"

# ==========================================
# [설정] 1인당 하루 무료 사용량 한도 (상수 설정)
# ==========================================
DAILY_LIMIT = 2


# ==========================================
# 0. Streamlit 페이지 설정
# ==========================================
st.set_page_config(
    page_title="AI 객체 지우개 | AI Object Eraser",
    page_icon="🪄",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ==========================================
# 0-1. 프론트엔드 최소 인라인 패치 (BidiComponent + Html + iframeResizer 단 3개만 인라인)
# ==========================================
@st.cache_resource(show_spinner=False)
def _ensure_minimal_frontend_bundle(_v: str = "v9"):
    """
    st.file_uploader(Axios 500 에러 원인), st.slider(외부 JS 청크 에러 원인), st.image(미디어 404 원인)를
    완전히 제거하고 단일 HTML5 통합 워크스페이스로 대체함에 따라,
    진입 번들(index.RuhrnD1v.js)에 BidiComponent, Html, iframeResizer 단 3개(38KB)만 인라인 병합합니다.
    """
    try:
        st_base = Path(st.__file__).resolve().parent / "static"
        st_static = st_base / "static" / "js"
        main_path = st_static / "index.RuhrnD1v.js"
        html_path = st_base / "index.html"
        if not main_path.exists():
            return False

        main_txt = main_path.read_text(encoding="utf-8")
        if "/*__AGY_MINIMAL_PATCH_V4__*/" in main_txt:
            patched_txt = main_txt
        else:
            for marker in (
                "/*__AGY_MINIMAL_PATCH_V4__*/",
                "/*__AGY_MONOLITHIC_PATCH_V3__*/",
                "/*__AGY_MONOLITHIC_PATCH_V2__*/",
                "/*__AGY_MONOLITHIC_PATCH__*/",
            ):
                if marker in main_txt:
                    main_txt = (
                        main_txt.split(f"\n{marker}\n")[0]
                        + main_txt[main_txt.rfind("export{") :]
                    )

            exp_idx = main_txt.rfind("export{")
            if exp_idx == -1:
                return False
            exp_body = main_txt[exp_idx + 7 : main_txt.rfind("}")]
            main_obj_entries = []
            for item in exp_body.split(","):
                item = item.strip()
                if " as " in item:
                    orig, alias = item.split(" as ")
                    main_obj_entries.append(f"{alias.strip()}:{orig.strip()}")
                else:
                    main_obj_entries.append(f"{item}:{item}")

            topo_files = [
                "./index.BOtk_OgO.js",
                "./index.BQ4RSzjA.js",
                "./iframeResizer.contentWindow.DP8LuJZf.js",
            ]

            def transform_chunk(rel_path: str) -> str:
                fp = st_static / rel_path.replace("./", "")
                code = fp.read_text(encoding="utf-8").strip()

                def repl_import(m):
                    spec, src = m.group(1), m.group(2)
                    if spec is None:
                        return ""
                    bindings = []
                    for b in spec.split(","):
                        b = b.strip()
                        if " as " in b:
                            k, v = b.split(" as ")
                            bindings.append(f"{k.strip()}:{v.strip()}")
                        else:
                            bindings.append(b)
                    return f'const {{{",".join(bindings)}}}=window.__st_chunks["{src}"];'

                code = re.sub(
                    r'import(?:\{([^}]+)\}from)?"(\./[^"]+)";', repl_import, code
                )

                def repl_export(m):
                    spec = m.group(1)
                    exps = []
                    for b in spec.split(","):
                        b = b.strip()
                        if " as " in b:
                            orig, alias = b.split(" as ")
                            exps.append(f"{alias.strip()}:{orig.strip()}")
                        else:
                            exps.append(f"{b}:{b}")
                    return f'return {{{",".join(exps)}}};'

                code = re.sub(r"export\{([^}]+)\};?$", repl_export, code)
                return f'window.__st_chunks["{rel_path}"]=(function(){{{code}}})();'

            inlined_chunks = "\n".join(transform_chunk(f) for f in topo_files)

            patched_txt = main_txt
            for w_name, chunk_key in {
                "BidiComponent": "./index.BOtk_OgO.js",
                "Html": "./index.BQ4RSzjA.js",
            }.items():
                pattern = rf'{w_name}=reactExports\.lazy\(\(\)=>__vitePreload\(\(\)=>import\("{re.escape(chunk_key)}"\),.*?,import\.meta\.url\)\)'
                replacement = f'{w_name}=reactExports.lazy(()=>Promise.resolve(window.__st_chunks["{chunk_key}"]))'
                patched_txt = re.sub(pattern, replacement, patched_txt)

            patched_txt = patched_txt.replace(
                '__vitePreload(()=>import("./iframeResizer.contentWindow.DP8LuJZf.js").then(i=>i.i),[],import.meta.url)',
                'Promise.resolve(window.__st_chunks["./iframeResizer.contentWindow.DP8LuJZf.js"].i)',
            )

            final_exp_idx = patched_txt.rfind("export{")
            injected_block = (
                "\n/*__AGY_MINIMAL_PATCH_V4__*/\n"
                f"window.__st_chunks={{'./index.RuhrnD1v.js':{{{','.join(main_obj_entries)}}}}};\n"
                f"{inlined_chunks}\n"
            )
            patched_txt = (
                patched_txt[:final_exp_idx]
                + injected_block
                + patched_txt[final_exp_idx:]
            )
            try:
                main_path.write_text(patched_txt, encoding="utf-8")
            except Exception:
                pass

        patched_txt = (
            patched_txt.replace(
                "return e.length<=2?e:e.slice(0,2)}", "return e.slice(0,1)}"
            )
            .replace(
                "WEBSOCKET_TIMEOUT_MS=isAndroidDevice()?60*1e3:15*1e3,PING_TIMEOUT_MS=15*1e3",
                "WEBSOCKET_TIMEOUT_MS=2200,PING_TIMEOUT_MS=2*1e3",
            )
            .replace(
                "WEBSOCKET_TIMEOUT_MS=4*1e3,PING_TIMEOUT_MS=2*1e3",
                "WEBSOCKET_TIMEOUT_MS=2200,PING_TIMEOUT_MS=2*1e3",
            )
        )
        patched_bytes = patched_txt.encode("utf-8")
        patched_html_txt = """<!DOCTYPE html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
    <link rel="shortcut icon" href="data:," />
    <title>AI 오브젝트 지우개</title>
    <script>
      window.prerenderReady = false;
      (function() {
        const origFetch = window.fetch.bind(window);
        const hostCfg = JSON.stringify({
          allowedOrigins: [
            "https://devel.streamlit.test",
            "https://*.streamlit.apptest",
            "https://*.streamlitapp.test",
            "https://*.streamlitapp.com",
            "https://share.streamlit.io",
            "https://share-demo.streamlit.io",
            "https://share-head.streamlit.io",
            "https://share-staging.streamlit.io",
            "https://*.demo.streamlit.run",
            "https://*.head.streamlit.run",
            "https://*.staging.streamlit.run",
            "https://*.streamlit.run",
            "https://*.demo.streamlit.app",
            "https://*.head.streamlit.app",
            "https://*.staging.streamlit.app",
            "https://*.streamlit.app"
          ],
          useExternalAuthToken: false,
          enableCustomParentMessages: false,
          enforceDownloadInNewTab: false,
          metricsUrl: "",
          blockErrorDialogs: false,
          resourceCrossOriginMode: null
        });
        let healthCount = 0;
        window.fetch = function(input, init) {
          const u = typeof input === "string" ? input : (input && input.url ? input.url : String(input));
          if (u.includes("_stcore/health")) {
            const delay = healthCount++ === 0 ? 0 : 60;
            return new Promise(r => setTimeout(() => r(new Response("ok", { status: 200, headers: { "Content-Type": "text/plain" } })), delay));
          }
          if (u.includes("_stcore/host-config")) {
            return Promise.resolve(new Response(hostCfg, { status: 200, headers: { "Content-Type": "application/json" } }));
          }
          if (u.includes("data.streamlit.io") || u.includes("fivetran.com")) {
            return Promise.resolve(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
          }
          return origFetch(input, init);
        };

        let loaded = false;
        let headerReceived = false;
        const controllers = [];
        function loadAttempt(idx) {
          if (loaded || headerReceived) return;
          const ctrl = new AbortController();
          controllers.push(ctrl);
          const hdrTimer = setTimeout(() => {
            if (!headerReceived) ctrl.abort();
          }, 1100);
          const qs = idx === 0 ? "?v=agy9" : ("?v=agy9&r=" + idx + "_" + Date.now());
          origFetch("./static/js/index.RuhrnD1v.js" + qs, { signal: ctrl.signal })
            .then(r => {
              clearTimeout(hdrTimer);
              if (!r.ok) throw new Error("HTTP " + r.status);
              if (!headerReceived) {
                headerReceived = true;
                controllers.forEach(c => { if (c !== ctrl) { try { c.abort(); } catch(e) {} } });
              }
              return r.text();
            })
            .then(code => {
              if (loaded) return;
              loaded = true;
              const s = document.createElement("script");
              s.type = "module";
              s.textContent = code;
              document.head.appendChild(s);
            })
            .catch(() => {
              clearTimeout(hdrTimer);
              if (!loaded) {
                headerReceived = false;
                setTimeout(() => loadAttempt(idx + 1), 20);
              }
            });
        }
        setTimeout(() => loadAttempt(0), 60);
      })();
    </script>
  </head>
  <body>
    <noscript>You need to enable JavaScript to run this app.</noscript>
    <div id="root"></div>
  </body>
</html>
"""
        patched_html_bytes = patched_html_txt.encode("utf-8")

        try:
            html_path.write_text(patched_html_txt, encoding="utf-8")
        except Exception:
            pass

        import tornado.web
        from streamlit.web.server import routes as st_routes

        orig_get_content = getattr(
            st_routes.StaticFileHandler,
            "_agy_orig_get_content",
            tornado.web.StaticFileHandler.get_content,
        )
        orig_get_content_size = getattr(
            st_routes.StaticFileHandler,
            "_agy_orig_get_content_size",
            tornado.web.StaticFileHandler.get_content_size,
        )
        orig_set_extra_headers = getattr(
            st_routes.StaticFileHandler,
            "_agy_orig_set_extra_headers",
            st_routes.StaticFileHandler.set_extra_headers,
        )
        st_routes.StaticFileHandler._agy_orig_get_content = orig_get_content
        st_routes.StaticFileHandler._agy_orig_get_content_size = orig_get_content_size
        st_routes.StaticFileHandler._agy_orig_set_extra_headers = orig_set_extra_headers

        @classmethod
        def _patched_get_content(cls, abspath: str, start=None, end=None):
            if abspath.endswith("index.RuhrnD1v.js"):
                s = start or 0
                e = end if end is not None else len(patched_bytes)
                yield patched_bytes[s:e]
                return
            if abspath.endswith("index.html"):
                s = start or 0
                e = end if end is not None else len(patched_html_bytes)
                yield patched_html_bytes[s:e]
                return
            yield from orig_get_content(abspath, start, end)

        def _patched_get_content_size(self) -> int:
            ap = getattr(self, "absolute_path", "") or ""
            if ap.endswith("index.RuhrnD1v.js"):
                return len(patched_bytes)
            if ap.endswith("index.html"):
                return len(patched_html_bytes)
            return orig_get_content_size(self)

        def _patched_set_extra_headers(self, path: str) -> None:
            orig_set_extra_headers(self, path)
            if "index.RuhrnD1v.js" in path:
                self.set_header("Cache-Control", "public, max-age=31536000")

        st_routes.StaticFileHandler.get_content = _patched_get_content
        st_routes.StaticFileHandler.get_content_size = _patched_get_content_size
        st_routes.StaticFileHandler.set_extra_headers = _patched_set_extra_headers
        tornado.web.StaticFileHandler.reset()
        # 4) 앱 시작 시점(유저 접속 전)에 백그라운드에서 ONNX 모델을 미리 1.4초 만에 로드하여 첫 추론 대기시간 제거
        def _preload():
            try:
                time.sleep(1.2)
                get_onnx_session()
            except Exception:
                pass

        import threading
        threading.Thread(target=_preload, daemon=True, name="onnx-boot-preload").start()
        return True
    except Exception:
        return False


_ensure_minimal_frontend_bundle("v9")


# ==========================================
# 1. 초고속(1.4초) ONNX 세션 (@st.cache_resource 단일 캐시, 백그라운드 스레드 경합 제거)
# ==========================================
@st.cache_resource(show_spinner=False)
def get_onnx_session():
    """ONNX Runtime 추론 세션 생성 (최초 1회 1.4초 초고속 로드 후 영구 캐싱)"""
    import onnxruntime as ort

    ort.set_default_logger_severity(3)

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"모델 파일을 찾을 수 없습니다: {MODEL_PATH}")

    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = 2
    session_options.inter_op_num_threads = 1
    session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    # O(N^2) 메모리 플래너 비활성화로 27.3초 걸리던 로드를 1.4초로 단축
    session_options.enable_mem_pattern = False
    session_options.enable_mem_reuse = False
    session_options.graph_optimization_level = (
        ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    )

    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    available_providers = ort.get_available_providers()
    selected_providers = [p for p in providers if p in available_providers]

    return ort.InferenceSession(
        str(MODEL_PATH), sess_options=session_options, providers=selected_providers
    )


# ==========================================
# 2. 인페인팅 추론 파이프라인 (결과 이미지를 PNG Base64 Data URL로 즉시 생성)
# ==========================================
def run_inpaint_pipeline(orig_b64: str, mask_b64: str):
    t_start = time.time()

    _, orig_encoded = orig_b64.split(";base64,", 1)
    orig_pil = Image.open(io.BytesIO(base64.b64decode(orig_encoded))).convert("RGB")
    original_rgb = np.array(orig_pil)
    orig_h, orig_w, _ = original_rgb.shape

    _, mask_encoded = mask_b64.split(";base64,", 1)
    mask_pil = Image.open(io.BytesIO(base64.b64decode(mask_encoded))).convert("RGBA")
    mask_rgba = np.array(mask_pil)
    mask_layer = np.maximum(mask_rgba[:, :, 3], mask_rgba[:, :, 0])
    mask_binary_small = (mask_layer > 10).astype(np.uint8) * 255

    if np.sum(mask_binary_small) == 0:
        return None, 0.0, orig_w, orig_h, "⚠️ 수정할 영역을 브러시로 칠해주세요."

    mask_binary_data = cv2.resize(
        mask_binary_small, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST
    )

    # 마스크 외곽 팽창(Dilation): 경계선에 글자나 선택 영역 잔상이 남지 않도록 안전 마진 확장
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask_dilated = cv2.dilate(mask_binary_data, kernel, iterations=2)

    # 512x512 텐서 생성
    img_resized = cv2.resize(original_rgb, (512, 512), interpolation=cv2.INTER_AREA)
    img_input = np.transpose(img_resized.astype(np.float32) / 255.0, (2, 0, 1))
    img_input = np.expand_dims(img_input, axis=0)

    mask_resized = cv2.resize(
        mask_dilated, (512, 512), interpolation=cv2.INTER_NEAREST
    )
    mask_input = np.where(mask_resized > 128, 1.0, 0.0).astype(np.float32)
    mask_input = np.expand_dims(np.expand_dims(mask_input, axis=0), axis=0)

    # 모델 추론
    session = get_onnx_session()
    ort_inputs = {
        session.get_inputs()[0].name: img_input,
        session.get_inputs()[1].name: mask_input,
    }
    ort_outs = session.run(None, ort_inputs)

    # 후처리 및 고화질 블렌딩
    out_img = ort_outs[0][0]
    out_img = np.transpose(out_img, (1, 2, 0))
    if np.max(out_img) <= 1.01:
        out_img = out_img * 255.0
    out_img = np.clip(out_img, 0, 255).astype(np.uint8)

    inpainted_full = cv2.resize(
        out_img, (orig_w, orig_h), interpolation=cv2.INTER_LANCZOS4
    )

    # 팽창된 마스크 경계면을 부드럽게 가우시안 블러링 처리하여 합성
    mask_blur = cv2.GaussianBlur(mask_dilated, (7, 7), 0)
    alpha = np.expand_dims(mask_blur.astype(np.float32) / 255.0, axis=2)

    final_rgb = (alpha * inpainted_full + (1.0 - alpha) * original_rgb).astype(np.uint8)

    # PNG Base64 인코딩 (st.image 미디어 서버를 거치지 않고 브라우저로 직송)
    out_buf = io.BytesIO()
    Image.fromarray(final_rgb).save(out_buf, format="PNG")
    res_b64 = "data:image/png;base64," + base64.b64encode(out_buf.getvalue()).decode(
        "ascii"
    )

    elapsed = time.time() - t_start
    return res_b64, elapsed, orig_w, orig_h, None


# ==========================================
# 2-1. 일일 사용량 제한 (클라이언트 IP 및 세션 연동)
# ==========================================
# 서버 프로세스 메모리에 저장되는 일일 사용량 캐시: { "IP": {"date": "YYYY-MM-DD", "count": N} }
_IP_USAGE_CACHE = {}

def get_client_ip() -> str:
    """Streamlit 리버스 프록시 헤더 및 WebSocket 클라이언트로부터 실제 IP를 판별합니다."""
    try:
        from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
        from streamlit import runtime
        ctx = get_script_run_ctx()
        if ctx:
            client = runtime.get_instance().get_client(ctx.session_id)
            if client and client.client_context:
                cc = client.client_context
                if getattr(cc, "remote_ip", None):
                    return str(cc.remote_ip).strip()
                headers = dict(cc.headers or [])
                xfwd = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
                if xfwd:
                    return str(xfwd).split(",")[0].strip()
                cf_ip = headers.get("cf-connecting-ip") or headers.get("CF-Connecting-IP")
                if cf_ip:
                    return str(cf_ip).strip()
                xreal = headers.get("x-real-ip") or headers.get("X-Real-IP")
                if xreal:
                    return str(xreal).strip()
    except Exception:
        pass

    try:
        if hasattr(st, "context") and hasattr(st.context, "headers"):
            headers = st.context.headers or {}
            xfwd = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
            if xfwd:
                return str(xfwd).split(",")[0].strip()
            cf_ip = headers.get("cf-connecting-ip") or headers.get("CF-Connecting-IP")
            if cf_ip:
                return str(cf_ip).strip()
            xreal = headers.get("x-real-ip") or headers.get("X-Real-IP")
            if xreal:
                return str(xreal).strip()
            if hasattr(st.context, "ip_address") and st.context.ip_address:
                return str(st.context.ip_address).strip()
    except Exception:
        pass
    return "127.0.0.1"

def check_and_increment_usage(client_ip: str) -> tuple[bool, int, int]:
    """
    접속자 IP 및 세션 기준으로 오늘 사용량을 검사하고 증가시킵니다.
    반환: (허용여부: bool, 오늘사용횟수: int, 잔여횟수: int)
    """
    from datetime import date
    today_str = date.today().isoformat()
    
    # 1. 서버 전역 IP 캐시 갱신
    record = _IP_USAGE_CACHE.get(client_ip)
    if not record or record.get("date") != today_str:
        record = {"date": today_str, "count": 0}
        _IP_USAGE_CACHE[client_ip] = record

    # 2. 세션 상태에도 독립 카운터 동기화
    sess_date = st.session_state.get("_sess_usage_date")
    if sess_date != today_str:
        st.session_state["_sess_usage_date"] = today_str
        st.session_state["_sess_usage_count"] = 0

    cur_count = max(record["count"], st.session_state.get("_sess_usage_count", 0))

    if cur_count >= DAILY_LIMIT:
        return False, cur_count, 0

    cur_count += 1
    record["count"] = cur_count
    st.session_state["_sess_usage_count"] = cur_count
    rem = max(0, DAILY_LIMIT - cur_count)
    return True, cur_count, rem

def get_usage_info(client_ip: str) -> tuple[int, int]:
    """현재 사용량 및 잔여 횟수를 조회합니다."""
    from datetime import date
    today_str = date.today().isoformat()
    record = _IP_USAGE_CACHE.get(client_ip)
    ip_count = record["count"] if (record and record.get("date") == today_str) else 0
    sess_date = st.session_state.get("_sess_usage_date")
    sess_count = st.session_state.get("_sess_usage_count", 0) if sess_date == today_str else 0
    count = max(ip_count, sess_count)
    rem = max(0, DAILY_LIMIT - count)
    return count, rem


# ==========================================
# 3. 글로벌 레이아웃 CSS & 상단 헤더 (DESIGN_SYSTEM_PASTEL_SKY)
# ==========================================
st.html(
    """
<style>
.stApp {
    background-color: #f8fafc !important;
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif !important;
}
.block-container {
    max-width: 1360px !important;
    padding-top: 0.7rem !important;
    padding-bottom: 0px !important;
    margin-bottom: 0px !important;
}
iframe {
    margin-bottom: 0px !important;
}
[data-testid="stHeader"],
[data-testid="stToolbar"],
footer {
    display: none !important;
}
.dashboard-header-card {
    background: #ffffff;
    border: 1px solid rgba(186, 230, 253, 0.8);
    border-radius: 16px;
    padding: 10px 22px;
    margin-top: 12px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 2px 8px rgba(14, 165, 233, 0.05);
}
.header-left {
    display: flex;
    align-items: center;
    gap: 12px;
}
.header-icon-box {
    width: 38px;
    height: 38px;
    border-radius: 12px;
    background: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
    font-size: 1.2rem;
    box-shadow: 0 3px 8px rgba(2, 132, 199, 0.22);
}
.header-title-box {
    display: flex;
    flex-direction: column;
}
.dashboard-header-card .header-title {
    font-size: 1.12rem;
    font-weight: 800;
    color: #0f172a;
    margin: 0;
    letter-spacing: -0.02em;
}
.dashboard-header-card .header-desc {
    font-size: 0.82rem;
    color: #64748b;
    margin-top: 2px;
}
.header-badge {
    display: flex;
    align-items: center;
    gap: 6px;
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    color: #16a34a;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 700;
}
.badge-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #16a34a;
}
</style>
<div class="dashboard-header-card">
    <div class="header-left">
        <div class="header-icon-box">🪄</div>
        <div class="header-title-box">
            <h1 class="header-title">AI 객체 지우개 (AI Object Eraser)</h1>
            <p class="header-desc">100% 클라이언트 연동 초고속 ONNX 신경망 기반 불필요 사물·얼룩 제거</p>
        </div>
    </div>
    <div class="header-badge">
        <span class="badge-dot"></span>
        <span>light_inpaint.onnx 엔진 가동 중</span>
    </div>
</div>
"""
)


# ==========================================
# 4. 단일 HTML5 통합 워크스페이스 컴포넌트 (Axios 0회, 외부 JS 청크 0회, 미디어 URL 0회)
# ==========================================
WORKSPACE_HTML = """
<div class="wm-app-root">
  <!-- 약관 동의 모달 팝업 (동일 HTML 워크스페이스 내장 3:4 Pastel Sky 팝업) -->
  <div id="wm-terms-modal-overlay" class="terms-modal-overlay" style="display:none;">
    <div class="terms-modal-card">
      <div class="terms-modal-header">
        <span class="terms-modal-icon">🌿</span>
        <h3 class="terms-modal-title">서비스 이용 안내 및 확인</h3>
      </div>
      <div class="terms-modal-welcome">
        <div class="welcome-title">✨ AI 객체 지우개</div>
        <div class="welcome-desc">깨끗하고 안전한 창작 환경을 위해 아래 이용 원칙을 확인해 주세요.</div>
      </div>
      <div class="terms-modal-body">
        <div class="terms-rule-item">
          <div class="rule-icon">🌱</div>
          <div class="rule-content">
            <b>권리 보유 이미지 편집</b><br>
            <span>본 도구는 직접 창작하셨거나 적법한 편집 권한이 있는 이미지의 불필요한 사물 및 흠집 제거를 위해 제공됩니다.</span>
          </div>
        </div>
        <div class="terms-rule-item">
          <div class="rule-icon">🛡️</div>
          <div class="rule-content">
            <b>올바른 창작 윤리 준수</b><br>
            <span>타인의 권리 표식을 무단 훼손하여 발생하는 책임은 사용자 본인에게 있으므로 건전한 이용을 부탁드립니다.</span>
          </div>
        </div>
      </div>
      <div class="terms-modal-footer">
        <button type="button" id="wm-terms-agree-btn" class="modal-btn-agree">🌱 동의하고 시작하기</button>
        <button type="button" id="wm-terms-cancel-btn" class="modal-btn-cancel">취소 (둘러보기)</button>
      </div>
    </div>
  </div>

  <!-- 2번 모던 심플 그리드 (좌측 편집창 / 우측 결과창 카드) -->
  <div class="wm-main-grid">

    <!-- 좌측 카드: 원본 이미지 & 마스크 칠하기 -->
    <div class="modern-card">
      <div class="card-header-row">
        <div class="card-title-group">
          <span class="card-title-icon">🎨</span>
          <h2 class="card-title-text">원본 이미지 & 마스크 칠하기</h2>
        </div>
        <div class="card-header-actions">
          <button type="button" id="wm-upload-btn" class="btn-action-outline">
            <span>📁 이미지 불러오기</span>
          </button>
          <input type="file" id="wm-file-input" accept="image/png,image/jpeg,image/webp" style="display:none;" />
        </div>
      </div>

      <!-- 캔버스 뷰포트 (체커보드 격자 배경) -->
      <div class="viewport-box" id="wm-editor-frame">
        <div id="wm-editor-empty" class="viewport-empty">
          <div class="empty-icon-circle">☁️</div>
          <p class="empty-title">이미지를 끌어다 놓거나 클릭하여 업로드</p>
          <p class="empty-desc">PNG, JPG, WEBP 지원 (고화질 자동 유지)</p>
          <span id="wm-file-name" style="display:none;">선택된 파일 없음</span>
        </div>
        <div id="wm-canvas-wrap" class="canvas-stage" style="display:none;">
          <canvas id="wm-bg-canvas"></canvas>
          <canvas id="wm-mask-canvas"></canvas>
        </div>
      </div>

      <!-- 하단 브러시 툴바 & 작업 실행 버튼 -->
      <div class="card-bottom-toolbar">
        <div class="brush-control-group">
          <span class="toolbar-label">브러시 크기</span>
          <span id="wm-brush-val" class="brush-size-chip">25px</span>
          <input type="range" id="wm-brush-slider" min="5" max="100" value="25" class="modern-slider" />
        </div>
        <div class="action-btn-group">
          <button type="button" id="wm-clear-btn" class="btn-action-ghost">
            <span>🔄 마스크 리셋</span>
          </button>
          <button type="button" id="wm-run-btn" class="btn-action-primary">
            <span>✨ 객체 지우기</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 우측 카드: 복원 결과 -->
    <div class="modern-card">
      <div class="card-header-row">
        <div class="card-title-group">
          <span class="card-title-icon">✨</span>
          <h2 class="card-title-text">복원 결과</h2>
        </div>
        <div class="card-header-actions">
          <span id="wm-usage-metric" class="usage-limit-chip" style="display:inline-flex;">오늘 잔여: __DAILY_LIMIT__/__DAILY_LIMIT__회</span>
          <span id="wm-status-metric" class="result-time-chip" style="display:none;"></span>
          <span id="wm-status-subtext" style="display:none;"></span>
        </div>
      </div>

      <!-- 결과 뷰포트 (체커보드 격자 배경) -->
      <div class="viewport-box" id="wm-result-frame">
        <div id="wm-result-empty" class="viewport-empty">
          <div class="empty-icon-circle">✨</div>
          <p class="empty-title">왼쪽에서 마스킹 후 "객체 지우기"를 클릭하세요</p>
        </div>
        <img id="wm-result-img" class="result-preview-img" style="display:none;" alt="복원 결과 이미지" />
      </div>

      <!-- 하단 다운로드 툴바 -->
      <div class="card-bottom-toolbar justify-end">
        <button type="button" id="wm-download-btn" class="btn-action-primary" disabled>
          <span>💾 고화질 이미지 저장</span>
        </button>
      </div>
    </div>

  </div>

  <!-- 푸터 영역 -->
  <footer class="wm-app-footer">
    <div class="footer-content">
      <span>Copyright © 2026 Kim Jeongman. All rights reserved.</span>
      <span class="footer-divider">|</span>
      <a href="https://blog.naver.com/jungman3" target="_blank" rel="noopener noreferrer" class="footer-blog-link">
        <span class="blog-icon">📗</span> Naver Blog
      </a>
    </div>
  </footer>
</div>
"""

WORKSPACE_CSS = """
* {
  box-sizing: border-box;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
.wm-app-root {
  width: 100%;
  color: #0f172a;
  position: relative;
}
/* 내장 동의 모달 팝업 스타일 (3:4 비율, Pastel Sky 화이트/하늘색 테마) */
.terms-modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background-color: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(5px);
  -webkit-backdrop-filter: blur(5px);
  z-index: 99999;
  display: flex;
  align-items: center;
  justify-content: center;
}
.terms-modal-card {
  background: #ffffff;
  border: 1.5px solid #bae6fd;
  border-radius: 20px;
  width: 440px;
  max-width: 90vw;
  padding: 24px 26px;
  box-shadow: 0 20px 45px -10px rgba(14, 165, 233, 0.25), 0 2px 10px rgba(0, 0, 0, 0.05);
  display: flex;
  flex-direction: column;
  animation: modalFadeIn 0.2s ease-out;
}
@keyframes modalFadeIn {
  from { opacity: 0; transform: scale(0.96); }
  to { opacity: 1; transform: scale(1); }
}
.terms-modal-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
.terms-modal-icon {
  font-size: 1.3rem;
}
.terms-modal-title {
  font-size: 1.15rem;
  font-weight: 800;
  color: #0f172a;
  margin: 0;
}
.terms-modal-welcome {
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  border: 1px solid #bae6fd;
  border-radius: 12px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.welcome-title {
  font-weight: 800;
  font-size: 0.95rem;
  color: #0284c7;
  margin-bottom: 4px;
}
.welcome-desc {
  font-size: 0.83rem;
  color: #475569;
  line-height: 1.45;
}
.terms-modal-body {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 20px;
}
.terms-rule-item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.rule-icon {
  font-size: 1.05rem;
  line-height: 1.4;
}
.rule-content {
  font-size: 0.84rem;
  line-height: 1.5;
  color: #334155;
}
.rule-content b {
  color: #0f172a;
}
.rule-content span {
  color: #64748b;
  display: inline-block;
  margin-top: 2px;
}
.terms-modal-footer {
  display: flex;
  gap: 10px;
}
.modal-btn-agree {
  flex: 1.6;
  background: linear-gradient(135deg, #0284c7 0%, #0ea5e9 100%);
  border: none;
  border-radius: 12px;
  color: #ffffff;
  font-weight: 700;
  font-size: 0.88rem;
  padding: 11px 16px;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(14, 165, 233, 0.35);
  transition: all 0.2s ease;
}
.modal-btn-agree:hover {
  background: linear-gradient(135deg, #0369a1 0%, #0284c7 100%);
  transform: translateY(-1px);
}
.modal-btn-cancel {
  flex: 1;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  color: #475569;
  font-weight: 600;
  font-size: 0.86rem;
  padding: 11px 14px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.modal-btn-cancel:hover {
  background: #e2e8f0;
  color: #1e293b;
}
.wm-main-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  width: 100%;
}
.modern-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.025);
  transition: all 0.2s ease;
}
.modern-card:hover {
  border-color: #bae6fd;
  box-shadow: 0 4px 16px rgba(14, 165, 233, 0.07);
}
.card-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid #e2e8f0;
  margin-bottom: 10px;
  min-height: 38px;
  height: 38px;
  box-sizing: border-box;
}
.card-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  max-width: calc(100% - 110px);
  overflow: hidden;
}
.card-title-group {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
  white-space: nowrap;
}
.card-title-icon {
  font-size: 1.05rem;
}
.card-title-text {
  font-size: 0.92rem;
  font-weight: 700;
  color: #0f172a;
  margin: 0;
}
.btn-action-outline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  color: #334155;
  font-size: 0.8rem;
  font-weight: 600;
  padding: 5px 11px;
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.btn-action-outline:hover {
  background: #f8fafc;
  border-color: #94a3b8;
  color: #0f172a;
}
.result-time-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 140px;
  width: auto;
  white-space: nowrap;
  text-align: center;
  font-size: 0.76rem;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-variant-numeric: tabular-nums;
  color: #0284c7;
  font-weight: 700;
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  padding: 3px 10px;
  border-radius: 18px;
  line-height: 1.2;
  box-sizing: border-box;
}
.usage-limit-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.76rem;
  font-weight: 700;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  padding: 3px 10px;
  border-radius: 14px;
  line-height: 1.2;
  white-space: nowrap;
}

/* 이전 480px에서 딱 60px만 줄인 420px 황금 비율 뷰포트 */
.viewport-box {
  background-color: #f1f5f9;
  background-image: 
    linear-gradient(45deg, #e2e8f0 25%, transparent 25%),
    linear-gradient(-45deg, #e2e8f0 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #e2e8f0 75%),
    linear-gradient(-45deg, transparent 75%, #e2e8f0 75%);
  background-size: 20px 20px;
  background-position: 0 0, 0 10px, 10px -10px, -10px 0px;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  height: 480px;
  min-height: 480px;
  max-height: 480px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
}
.viewport-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  cursor: pointer;
  padding: 18px;
  user-select: none;
}
.empty-icon-circle {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0284c7;
  font-size: 1.5rem;
  margin-bottom: 10px;
  box-shadow: 0 2px 8px rgba(14, 165, 233, 0.09);
}
.empty-title {
  font-size: 0.92rem;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 4px 0;
}
.empty-desc {
  font-size: 0.79rem;
  color: #64748b;
  margin: 0;
}

/* 하단 툴바 */
.card-bottom-toolbar {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.card-bottom-toolbar.justify-end {
  justify-content: flex-end;
}
.brush-control-group {
  display: flex;
  align-items: center;
  gap: 9px;
}
.toolbar-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: #475569;
}
.brush-size-chip {
  font-size: 0.78rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-weight: 700;
  color: #0284c7;
}
.modern-slider {
  width: 115px;
  accent-color: #0284c7;
  cursor: pointer;
  height: 5px;
}
.action-btn-group {
  display: flex;
  align-items: center;
  gap: 7px;
}
.btn-action-ghost {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  color: #475569;
  font-size: 0.8rem;
  font-weight: 600;
  padding: 6px 12px;
  border-radius: 7px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.btn-action-ghost:hover {
  background: #f1f5f9;
  color: #1e293b;
}
.btn-action-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
  border: none;
  color: #ffffff;
  font-size: 0.8rem;
  font-weight: 700;
  padding: 6px 15px;
  border-radius: 7px;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(2, 132, 199, 0.22);
  transition: all 0.2s ease;
}
.btn-action-primary:hover:not(:disabled) {
  background: linear-gradient(135deg, #0ea5e9 0%, #0369a1 100%);
  transform: translateY(-1px);
}
.btn-action-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  box-shadow: none;
}

.canvas-stage {
  position: relative;
  margin: auto;
  border-radius: 8px;
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}
.canvas-stage canvas {
  position: absolute;
  top: 0;
  left: 0;
  display: block;
  border-radius: 8px;
}
#wm-mask-canvas {
  z-index: 2;
  touch-action: none;
}
#wm-bg-canvas {
  z-index: 1;
}
.result-preview-img {
  margin: auto;
  border-radius: 8px;
  display: block;
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.08);
}
/* 이전 28px에서 20px 줄인 8px 여백의 균형 잡힌 푸터 */
.wm-app-footer {
  margin-top: 14px;
  padding: 10px 0 0px 0;
  margin-bottom: 0px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  justify-content: center;
  align-items: center;
}
.footer-content {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 0.79rem;
  color: #64748b;
  flex-wrap: wrap;
  justify-content: center;
}
.footer-divider {
  color: #cbd5e1;
}
.footer-blog-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #0284c7;
  text-decoration: none;
  font-weight: 600;
  padding: 2.5px 8px;
  border-radius: 6px;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  transition: all 0.2s ease;
}
.footer-blog-link:hover {
  background: #e0f2fe;
  color: #0369a1;
  border-color: #7dd3fc;
  text-decoration: none;
  transform: translateY(-1px);
}
.blog-icon {
  font-size: 0.82rem;
}

@media (max-width: 900px) {
  .wm-top-grid, .wm-main-grid {
    grid-template-columns: 1fr;
  }
}
"""

WORKSPACE_JS = """
const stateMap = new WeakMap();

function makeBrushCursor(strokePx) {
  const diameter = Math.max(parseInt(strokePx, 10) || 25, 8);
  const pad = 4;
  const total = diameter + pad;
  const center = (total / 2).toFixed(1);
  const radius = (diameter / 2).toFixed(1);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${total}" height="${total}" viewBox="0 0 ${total} ${total}"><circle cx="${center}" cy="${center}" r="${radius}" fill="rgba(239, 68, 68, 0.25)" stroke="#ef4444" stroke-width="1.5"/><circle cx="${center}" cy="${center}" r="1.5" fill="#ef4444"/></svg>`;
  const b64 = btoa(svg);
  return `url('data:image/svg+xml;base64,${b64}') ${center} ${center}, crosshair`;
}

function initWorkspace(parentElement) {
  const q = (sel) => parentElement.querySelector(sel);
  const termsOverlay = q("#wm-terms-modal-overlay");
  const termsAgreeBtn = q("#wm-terms-agree-btn");
  const termsCancelBtn = q("#wm-terms-cancel-btn");
  const fileInput = q("#wm-file-input");
  const uploadBtn = q("#wm-upload-btn");
  const fileNameEl = q("#wm-file-name");
  const brushSlider = q("#wm-brush-slider");
  const brushValEl = q("#wm-brush-val");
  const runBtn = q("#wm-run-btn");
  const clearBtn = q("#wm-clear-btn");
  const downloadBtn = q("#wm-download-btn");
  const statusMetricEl = q("#wm-status-metric");
  const statusSubtextEl = q("#wm-status-subtext");
  const editorEmptyEl = q("#wm-editor-empty");
  const canvasWrapEl = q("#wm-canvas-wrap");
  const bgCanvas = q("#wm-bg-canvas");
  const maskCanvas = q("#wm-mask-canvas");
  const resultEmptyEl = q("#wm-result-empty");
  const resultImgEl = q("#wm-result-img");
  const usageMetricEl = q("#wm-usage-metric");

  const st = {
    origDataUrl: null,
    resultDataUrl: null,
    hasMask: false,
    isDrawing: false,
    lastX: 0,
    lastY: 0,
    strokeWidth: 25,
    reqSeq: 0,
    lastHandledId: 0,
    timerId: null,
    setStateValue: null,
    termsAgreed: false,
  };

  function stopTimer() {
    if (st.timerId) {
      clearInterval(st.timerId);
      st.timerId = null;
    }
  }

  function updateCursor() {
    maskCanvas.style.cursor = makeBrushCursor(st.strokeWidth);
  }
  updateCursor();

  brushSlider.addEventListener("input", () => {
    st.strokeWidth = parseInt(brushSlider.value, 10) || 25;
    brushValEl.textContent = st.strokeWidth + " px";
    updateCursor();
  });

  function loadSelectedFile(file) {
    if (!file) return;
    if (!st.termsAgreed) {
      if (st.setStateValue) {
        st.setStateValue({ require_terms: Date.now() });
      }
      return;
    }
    fileNameEl.textContent = file.name;

    // 새로운 이미지 업로드 즉시 이전 결과창을 깨끗하게 빈 영역으로 초기화
    st.resultDataUrl = null;
    resultImgEl.style.display = "none";
    resultImgEl.removeAttribute("src");
    resultEmptyEl.style.display = "flex";
    downloadBtn.disabled = true;
    statusMetricEl.style.display = "none";

    const reader = new FileReader();
    reader.onload = (ev) => {
      const img = new Image();
      img.onload = () => {
        // 클라우드 WebSocket 전송 한도 및 추론 최적화를 위해 최대 변 1440px 이내로 안전 정규화
        const maxOrigDim = 1440;
        let ow = img.naturalWidth || img.width;
        let oh = img.naturalHeight || img.height;
        if (ow > maxOrigDim || oh > maxOrigDim) {
          const ratio = Math.min(maxOrigDim / ow, maxOrigDim / oh);
          ow = Math.round(ow * ratio);
          oh = Math.round(oh * ratio);
        }
        const offCanvas = document.createElement("canvas");
        offCanvas.width = ow;
        offCanvas.height = oh;
        offCanvas.getContext("2d").drawImage(img, 0, 0, ow, oh);
        st.origDataUrl = offCanvas.toDataURL("image/jpeg", 0.92);

        // 에디터 캔버스 크기 계산 (최대 560 x 450)
        const maxW = 560, maxH = 450;
        const aspect = ow / oh;
        let cw = maxW, ch = maxH;
        if (aspect > maxW / maxH) {
          cw = maxW;
          ch = Math.max(Math.round(maxW / aspect), 100);
        } else {
          ch = maxH;
          cw = Math.max(Math.round(maxH * aspect), 100);
        }

        canvasWrapEl.style.width = cw + "px";
        canvasWrapEl.style.height = ch + "px";
        bgCanvas.width = cw;
        bgCanvas.height = ch;
        maskCanvas.width = cw;
        maskCanvas.height = ch;

        const bgCtx = bgCanvas.getContext("2d");
        bgCtx.clearRect(0, 0, cw, ch);
        bgCtx.drawImage(img, 0, 0, cw, ch);

        const maskCtx = maskCanvas.getContext("2d");
        maskCtx.clearRect(0, 0, cw, ch);
        st.hasMask = false;

        editorEmptyEl.style.display = "none";
        canvasWrapEl.style.display = "block";

        statusMetricEl.style.color = "#0369a1";
        statusMetricEl.innerHTML = "이미지 로드 완료 · 수정할 영역을 브러시로 칠해주세요.";
        statusSubtextEl.textContent = "마우스로 불필요한 사물이나 얼룩을 빨간색으로 칠한 뒤 [🚀 객체 지우기]를 누르세요.";
      };
      img.src = ev.target.result;
    };
    reader.readAsDataURL(file);
  }

  // 초기 로컬 스토리지 확인을 통한 동의 상태 복원 및 팝업 제어
  const storedAgreed = localStorage.getItem("object_eraser_terms_agreed") === "true";
  if (storedAgreed) {
    st.termsAgreed = true;
    termsOverlay.style.display = "none";
  } else {
    st.termsAgreed = false;
    termsOverlay.style.display = "flex";
  }

  // 동의하고 시작하기 클릭
  termsAgreeBtn.addEventListener("click", () => {
    st.termsAgreed = true;
    localStorage.setItem("object_eraser_terms_agreed", "true");
    termsOverlay.style.display = "none";
    if (st.setStateValue) {
      st.setStateValue("terms_agreed_sync", true);
    }
  });

  // 취소 (둘러보기) 클릭: 팝업만 닫고 미동의 상태 유지
  termsCancelBtn.addEventListener("click", () => {
    st.termsAgreed = false;
    termsOverlay.style.display = "none";
  });

  // 드래그 앤 드롭 지원 및 빈 영역 클릭 업로드
  const dropArea = editorEmptyEl;
  ['dragenter', 'dragover'].forEach(name => {
    dropArea.addEventListener(name, (e) => {
      e.preventDefault();
      dropArea.style.borderColor = "#38bdf8";
    });
  });
  ['dragleave', 'drop'].forEach(name => {
    dropArea.addEventListener(name, (e) => {
      e.preventDefault();
      dropArea.style.borderColor = "#e2e8f0";
    });
  });
  dropArea.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      if (!st.termsAgreed) {
        termsOverlay.style.display = "flex";
        return;
      }
      loadSelectedFile(e.dataTransfer.files[0]);
    }
  });

  editorEmptyEl.addEventListener("click", () => {
    if (!st.termsAgreed) {
      termsOverlay.style.display = "flex";
      return;
    }
    fileInput.click();
  });

  uploadBtn.addEventListener("click", () => {
    if (!st.termsAgreed) {
      // 미동의 상태에서 업로드 클릭 시: 100% 즉시 동의 팝업창을 다시 띄움!
      termsOverlay.style.display = "flex";
      return;
    }
    fileInput.click();
  });

  fileInput.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) loadSelectedFile(file);
    // 같은 파일을 초기화 후 다시 선택해도 무조건 change 이벤트가 발생하도록 value 초기화
    fileInput.value = "";
  });

  function getPos(e) {
    const rect = maskCanvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return {
      x: (clientX - rect.left) * (maskCanvas.width / rect.width),
      y: (clientY - rect.top) * (maskCanvas.height / rect.height),
    };
  }

  function drawStroke(x1, y1, x2, y2) {
    const ctx = maskCanvas.getContext("2d");
    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = st.strokeWidth;
    ctx.strokeStyle = "rgba(255, 0, 0, 0.65)";
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.restore();
    st.hasMask = true;
  }

  maskCanvas.addEventListener("pointerdown", (e) => {
    if (!st.origDataUrl) return;
    st.isDrawing = true;
    maskCanvas.setPointerCapture(e.pointerId);
    const p = getPos(e);
    st.lastX = p.x;
    st.lastY = p.y;
    drawStroke(p.x, p.y, p.x + 0.1, p.y + 0.1);
  });

  maskCanvas.addEventListener("pointermove", (e) => {
    if (!st.isDrawing) return;
    const p = getPos(e);
    drawStroke(st.lastX, st.lastY, p.x, p.y);
    st.lastX = p.x;
    st.lastY = p.y;
  });

  const endDraw = () => {
    st.isDrawing = false;
  };
  maskCanvas.addEventListener("pointerup", endDraw);
  maskCanvas.addEventListener("pointercancel", endDraw);

  // 마스크 리셋 버튼: 원본 이미지는 그대로 유지하고, 빨간색 칠해진 마스크만 깨끗하게 초기화
  clearBtn.addEventListener("click", () => {
    const maskCtx = maskCanvas.getContext("2d");
    maskCtx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
    st.hasMask = false;

    if (st.origDataUrl) {
      statusMetricEl.style.display = "none";
      statusMetricEl.style.color = "#0369a1";
      statusMetricEl.innerHTML = "마스크가 초기화되었습니다. 수정할 영역을 다시 칠해주세요.";
    }
  });

  // 실행 버튼: 원본 이미지 + 마스크를 단일 WebSocket 메시지로 파이썬 서버에 전달
  runBtn.addEventListener("click", () => {
    if (!st.origDataUrl) {
      statusMetricEl.style.display = "inline-flex";
      statusMetricEl.style.color = "#ef4444";
      statusMetricEl.style.background = "#fee2e2";
      statusMetricEl.style.borderColor = "#fca5a5";
      statusMetricEl.innerHTML = "⚠️ 먼저 원본 이미지를 업로드해주세요.";
      return;
    }
    if (!st.hasMask) {
      statusMetricEl.style.display = "inline-flex";
      statusMetricEl.style.color = "#d97706";
      statusMetricEl.style.background = "#fef3c7";
      statusMetricEl.style.borderColor = "#fcd34d";
      statusMetricEl.innerHTML = "⚠️ 수정할 영역을 브러시로 칠해주세요.";
      return;
    }
    if (!st.setStateValue) return;

    stopTimer();
    const startTs = Date.now();
    statusMetricEl.style.display = "inline-flex";
    statusMetricEl.style.color = "#0284c7";
    statusMetricEl.style.background = "#e0f2fe";
    statusMetricEl.style.borderColor = "#bae6fd";
    statusMetricEl.innerHTML = `연산 중: <b id="wm-live-timer" style="margin-left:4px; font-weight:800; font-variant-numeric:tabular-nums;">00.00초</b>`;

    // 요청 시 즉시 낙관적(Optimistic) 잔여 횟수 1회 실시간 차감 표시
    if (usageMetricEl) {
      const curTxt = usageMetricEl.textContent || "";
      const match = curTxt.match(/([0-9]+)\\/([0-9]+)/);
      if (match) {
        const curRem = parseInt(match[1], 10);
        const limit = parseInt(match[2], 10);
        const nextRem = Math.max(0, curRem - 1);
        usageMetricEl.textContent = `오늘 잔여: ${nextRem}/${limit}회`;
      }
    }

    st.timerId = setInterval(() => {
      const el = q("#wm-live-timer");
      if (!el) return;
      const totalMs = Date.now() - startTs;
      const totalSec = Math.floor(totalMs / 1000);
      const s = String(totalSec).padStart(2, "0");
      const cs = String(Math.floor((totalMs % 1000) / 10)).padStart(2, "0");
      el.textContent = `${s}.${cs}초`;
    }, 50);

    runBtn.disabled = true;
    const nextId = ++st.reqSeq;
    const maskDataUrl = maskCanvas.toDataURL("image/png");
    st.setStateValue("job", {
      id: nextId,
      orig: st.origDataUrl,
      mask: maskDataUrl,
    });
  });

  // 결과 이미지 다운로드 버튼
  downloadBtn.addEventListener("click", () => {
    if (!st.resultDataUrl) return;
    const a = document.createElement("a");
    a.href = st.resultDataUrl;
    a.download = "object_erased.png";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  });

  return {
    updateFromPython(data, setStateValue) {
      st.setStateValue = setStateValue;
      // 로컬 스토리지에 동의한 이력이 있으면 자바스크립트 상태를 항상 true로 최우선 유지
      const stored = localStorage.getItem("object_eraser_terms_agreed") === "true";
      if (stored) {
        st.termsAgreed = true;
        termsOverlay.style.display = "none";
      } else if (data && typeof data.termsAgreed !== "undefined") {
        st.termsAgreed = !!data.termsAgreed;
      }
      if (data && data.usage && usageMetricEl) {
        const lim = typeof data.usage.limit !== "undefined" ? data.usage.limit : __DAILY_LIMIT__;
        const rem = typeof data.usage.remaining !== "undefined" ? data.usage.remaining : lim;
        
        // 잔여 한도를 모두 소진(0회)했을 때는 "오늘 잔여: 0/N회" 뱃지를 완전히 숨김
        if (rem <= 0) {
          usageMetricEl.style.display = "none";
        } else {
          usageMetricEl.style.display = "inline-flex";
          usageMetricEl.textContent = `오늘 잔여: ${rem}/${lim}회`;
          if (rem <= 5) {
            usageMetricEl.style.color = "#d97706";
            usageMetricEl.style.background = "#fef3c7";
            usageMetricEl.style.borderColor = "#fcd34d";
          } else {
            usageMetricEl.style.color = "#0284c7";
            usageMetricEl.style.background = "#f0f9ff";
            usageMetricEl.style.borderColor = "#bae6fd";
          }
        }
      }

      if (!data || !data.resId || data.resId === st.lastHandledId) return;
      st.lastHandledId = data.resId;
      stopTimer();
      runBtn.disabled = false;

      if (data.error) {
        statusMetricEl.style.display = "inline-flex";
        statusMetricEl.style.color = "#ef4444";
        statusMetricEl.style.background = "#fee2e2";
        statusMetricEl.style.borderColor = "#fca5a5";
        statusMetricEl.textContent = data.error;
        return;
      }
      if (data.resImg) {
        st.resultDataUrl = data.resImg;
        resultEmptyEl.style.display = "none";
        resultImgEl.src = data.resImg;
        resultImgEl.style.display = "block";
        resultImgEl.style.width = canvasWrapEl.style.width;
        resultImgEl.style.height = canvasWrapEl.style.height;
        downloadBtn.disabled = false;

        statusMetricEl.style.display = "inline-flex";
        statusMetricEl.style.color = "#0284c7";
        statusMetricEl.style.background = "#e0f2fe";
        statusMetricEl.style.borderColor = "#bae6fd";
        statusMetricEl.innerHTML = `추론 시간: <b style="color:#0284c7; margin-left:4px;">${data.elapsedText}</b>`;
      }
    }
  };
}

export default function(component) {
  const { data, parentElement, setStateValue } = component;
  let inst = stateMap.get(parentElement);
  if (!inst) {
    inst = initWorkspace(parentElement);
    stateMap.set(parentElement, inst);
  }
  inst.updateFromPython(data, setStateValue);
  return () => {};
}
"""


@st.cache_resource(show_spinner=False)
def _get_workspace_component(daily_limit: int = DAILY_LIMIT):
    final_html = WORKSPACE_HTML.replace("__DAILY_LIMIT__", str(daily_limit))
    final_js = WORKSPACE_JS.replace("__DAILY_LIMIT__", str(daily_limit))
    return st.components.v2.component(
        f"object_eraser_workspace_lim_{daily_limit}",
        html=final_html,
        css=WORKSPACE_CSS,
        js=final_js,
        isolate_styles=True,
    )


# ==========================================
# 5. 세션 상태 및 추론 실행 루프
# ==========================================
if "last_processed_id" not in st.session_state:
    st.session_state["last_processed_id"] = 0
init_client_ip = get_client_ip()
init_used, init_rem = get_usage_info(init_client_ip)
if "py_response" not in st.session_state:
    st.session_state["py_response"] = {
        "resId": 0,
        "resImg": None,
        "elapsedText": "",
        "resDim": "",
        "error": None,
        "termsAgreed": st.session_state.get("terms_agreed", False),
        "usage": {"used": init_used, "limit": DAILY_LIMIT, "remaining": init_rem},
    }

# 파이썬 응답 데이터에 현재 동의 상태 및 최신 잔여 사용량 동기화
st.session_state["py_response"]["termsAgreed"] = st.session_state.get("terms_agreed", False)
cur_used, cur_rem = get_usage_info(init_client_ip)
st.session_state["py_response"]["usage"] = {
    "used": cur_used,
    "limit": DAILY_LIMIT,
    "remaining": cur_rem,
}

workspace_comp = _get_workspace_component()
comp_state = workspace_comp(
    data=st.session_state["py_response"],
    key="wm_workspace_main",
    default={"job": None, "terms_agreed_sync": None},
    on_job_change=lambda: None,
    on_terms_agreed_sync_change=lambda: None,
    width="stretch",
    height="content",
)

# 프론트엔드에서 동의가 발생하면 파이썬 세션에도 즉각 반영
if isinstance(comp_state, dict) and comp_state.get("terms_agreed_sync"):
    st.session_state["terms_agreed"] = True
    st.session_state["py_response"]["termsAgreed"] = True

job = (comp_state or {}).get("job")
if isinstance(job, dict) and job.get("id", 0) > st.session_state["last_processed_id"]:
    job_id = int(job["id"])
    st.session_state["last_processed_id"] = job_id
    client_ip = get_client_ip()
    allowed, used_cnt, rem_cnt = check_and_increment_usage(client_ip)
    if not allowed:
        st.session_state["py_response"] = {
            "resId": job_id,
            "resImg": None,
            "elapsedText": "",
            "resDim": "",
            "error": f"⚠️ 오늘 사용량 한도({DAILY_LIMIT}회)를 모두 소진하셨습니다. 내일 다시 이용해주세요!",
            "usage": {"used": used_cnt, "limit": DAILY_LIMIT, "remaining": 0},
        }
    else:
        try:
            res_b64, elapsed, w, h, err_msg = run_inpaint_pipeline(
                job.get("orig", ""), job.get("mask", "")
            )
            s = int(elapsed)
            cs = int(round((elapsed - s) * 100))
            if cs >= 100:
                s += 1
                cs = 0
            formatted_time = f"{s:02d}.{cs:02d}초"
            st.session_state["py_response"] = {
                "resId": job_id,
                "resImg": res_b64,
                "elapsedText": formatted_time,
                "resDim": f"{w}x{h} px",
                "error": err_msg,
                "usage": {"used": used_cnt, "limit": DAILY_LIMIT, "remaining": rem_cnt},
            }
        except Exception as exc:
            st.session_state["py_response"] = {
                "resId": job_id,
                "resImg": None,
                "elapsedText": "",
                "resDim": "",
                "error": f"⚠️ 복원 중 오류가 발생했습니다: {exc}",
                "usage": {"used": used_cnt, "limit": DAILY_LIMIT, "remaining": rem_cnt},
            }
    st.rerun()
