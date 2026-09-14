import asyncio
import base64
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

BASE_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = BASE_DIR / "data" / "screenshots"

try:
    from playwright.async_api import async_playwright
except Exception:
    async_playwright = None


@dataclass
class BrowserSession:
    session_id: str
    playwright: object
    browser: object
    context: object
    page: object
    profile: dict
    console: list[dict] = field(default_factory=list)
    failed_requests: list[dict] = field(default_factory=list)
    http_errors: list[dict] = field(default_factory=list)
    trace_path: str | None = None
    started_at: float = field(default_factory=time.time)


class BrowserManager:
    def __init__(self) -> None:
        self.sessions: dict[str, BrowserSession] = {}
        self.lock = asyncio.Lock()

    def available(self) -> bool:
        return async_playwright is not None

    async def start(self, profile: dict, trace_path: str | None = None) -> dict:
        if async_playwright is None:
            raise RuntimeError("Playwright no está instalado. Ejecuta: pip install playwright && playwright install chromium")
        browser_name = (profile.get("browser") or "chromium").lower()
        if browser_name not in {"chromium", "firefox", "webkit"}:
            raise ValueError("Browser no soportado. Usa chromium, firefox o webkit.")

        pw = await async_playwright().start()
        browser_type = getattr(pw, browser_name)
        browser = await browser_type.launch(headless=bool(profile.get("headless", False)))
        context = await browser.new_context(ignore_https_errors=True, viewport={"width": 1365, "height": 768})
        page = await context.new_page()
        page.set_default_timeout(int(profile.get("timeout_ms") or 30000))
        sid = uuid.uuid4().hex
        session = BrowserSession(sid, pw, browser, context, page, profile, trace_path=trace_path)
        if trace_path:
            Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)

        def on_console(msg):
            try:
                if msg.type in {"error", "warning"}:
                    session.console.append({"type": msg.type, "text": msg.text, "ts": time.time()})
                    if len(session.console) > 200:
                        del session.console[:50]
            except Exception:
                pass

        def on_request_failed(request):
            try:
                failure = request.failure
                session.failed_requests.append({
                    "url": request.url,
                    "method": request.method,
                    "error": failure or "request failed",
                    "ts": time.time(),
                })
                if len(session.failed_requests) > 200:
                    del session.failed_requests[:50]
            except Exception:
                pass

        def on_response(response):
            try:
                if int(response.status) >= 400:
                    req = response.request
                    session.http_errors.append({
                        "url": response.url, "status": int(response.status), "method": req.method,
                        "resource_type": req.resource_type, "ts": time.time(),
                    })
                    if len(session.http_errors) > 300:
                        del session.http_errors[:75]
            except Exception:
                pass

        page.on("console", on_console)
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)
        async with self.lock:
            self.sessions[sid] = session

        base_url = (profile.get("base_url") or "").strip()
        if base_url:
            await page.goto(base_url, wait_until="domcontentloaded")
        return await self.status(sid, include_screenshot=True)

    def _get(self, session_id: str) -> BrowserSession:
        s = self.sessions.get(session_id)
        if not s:
            raise ValueError("Sesión de navegador no encontrada o ya cerrada.")
        return s

    def _url(self, session: BrowserSession, target: str) -> str:
        if re.match(r"^https?://", target or "", flags=re.I):
            return target
        return urljoin((session.profile.get("base_url") or "").rstrip("/") + "/", (target or "").lstrip("/"))

    async def action(self, session_id: str, action: str, **cfg) -> dict:
        s = self._get(session_id)
        page = s.page
        action = action.lower().strip()
        started = time.perf_counter()

        if action == "goto":
            await page.goto(self._url(s, cfg.get("url") or cfg.get("path") or ""), wait_until="domcontentloaded")
        elif action == "click":
            locator = self._locator(page, cfg)
            await locator.click()
        elif action == "fill":
            locator = self._locator(page, cfg)
            await locator.fill(str(cfg.get("value", "")))
        elif action == "press":
            locator = self._locator(page, cfg)
            await locator.press(str(cfg.get("key") or "Enter"))
        elif action == "select":
            locator = self._locator(page, cfg)
            await locator.select_option(str(cfg.get("value", "")))
        elif action == "check":
            locator = self._locator(page, cfg)
            await locator.check()
        elif action == "uncheck":
            locator = self._locator(page, cfg)
            await locator.uncheck()
        elif action == "hover":
            locator = self._locator(page, cfg)
            await locator.hover()
        elif action == "expect_text":
            expected = str(cfg.get("expected", ""))
            locator = self._locator(page, cfg)
            await locator.wait_for(state="visible")
            actual = await locator.inner_text()
            if expected not in actual:
                raise AssertionError(f"Texto esperado no encontrado. Esperado: {expected!r}. Actual: {actual[:500]!r}")
        elif action == "expect_visible":
            locator = self._locator(page, cfg)
            await locator.wait_for(state="visible")
        elif action == "expect_hidden":
            locator = self._locator(page, cfg)
            await locator.wait_for(state="hidden")
        elif action == "expect_page_text":
            expected = str(cfg.get("expected", ""))
            actual = await page.locator("body").inner_text()
            if expected not in actual:
                raise AssertionError(f"Texto esperado no encontrado en la página. Esperado: {expected!r}.")
        elif action == "expect_url_contains":
            expected = str(cfg.get("expected", ""))
            if expected not in page.url:
                raise AssertionError(f"URL esperada no coincide. Debía contener {expected!r}; actual={page.url!r}")
        elif action == "click_xy":
            await page.mouse.click(float(cfg.get("x", 0)), float(cfg.get("y", 0)))
        elif action == "wait":
            await page.wait_for_timeout(int(cfg.get("ms", 500)))
        elif action == "screenshot":
            pass
        else:
            raise ValueError(f"Acción web no soportada: {action}")

        out = await self.status(session_id, include_screenshot=bool(cfg.get("screenshot", True)))
        out["action"] = action
        out["action_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return out

    def _locator(self, page, cfg):
        if cfg.get("selector"):
            return page.locator(str(cfg["selector"])).first
        if cfg.get("label"):
            return page.get_by_label(str(cfg["label"])).first
        if cfg.get("placeholder"):
            return page.get_by_placeholder(str(cfg["placeholder"])).first
        if cfg.get("role"):
            return page.get_by_role(str(cfg["role"]), name=cfg.get("name")).first
        if cfg.get("text"):
            return page.get_by_text(str(cfg["text"]), exact=bool(cfg.get("exact", False))).first
        raise ValueError("La acción necesita selector, label, placeholder, role o text.")


    async def page_snapshot(self, session_id: str, max_elements: int = 120, max_body_chars: int = 7000) -> dict:
        """Snapshot estructural para el agente. No incluye valores de inputs/passwords."""
        s = self._get(session_id)
        page = s.page
        data = await page.evaluate(r"""({maxElements,maxBodyChars}) => {
          const visible = (el) => {
            const st = getComputedStyle(el), r = el.getBoundingClientRect();
            return st.visibility !== 'hidden' && st.display !== 'none' && r.width > 0 && r.height > 0;
          };
          const selectorHint = (el) => {
            if (el.getAttribute('data-testid')) return `[data-testid=\"${el.getAttribute('data-testid')}\"]`;
            if (el.id) return `#${CSS.escape(el.id)}`;
            if (el.name) return `${el.tagName.toLowerCase()}[name=\"${el.name.replaceAll('\"','\\\"')}\"]`;
            return '';
          };
          const nodes = [...document.querySelectorAll('button,a,input,textarea,select,[role=button],[role=link],[role=checkbox],[role=radio]')].filter(visible).slice(0,maxElements);
          const elements = nodes.map((el,i) => ({
            i, tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '', role: el.getAttribute('role') || '',
            text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0,220),
            aria_label: (el.getAttribute('aria-label') || '').slice(0,220), placeholder: (el.getAttribute('placeholder') || '').slice(0,220),
            name: (el.getAttribute('name') || '').slice(0,120), testid: (el.getAttribute('data-testid') || '').slice(0,120),
            selector: selectorHint(el), disabled: !!el.disabled
          }));
          const bodyText=(document.body?.innerText || '').replace(/\s+/g,' ').trim().slice(0,maxBodyChars);
          return {title: document.title, url: location.href, elements, body_text: bodyText};
        }""", {"maxElements": max_elements, "maxBodyChars": max_body_chars})
        return data

    async def screenshot(self, session_id: str, save_name: str | None = None) -> dict:
        s = self._get(session_id)
        data = await s.page.screenshot(full_page=False)
        out = {"data_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}
        if save_name:
            raw_path = Path(save_name)
            if raw_path.is_absolute() or raw_path.parent != Path("."):
                path = raw_path
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix.lower() != ".png":
                    path = path.with_suffix(".png")
            else:
                SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", save_name)[:100] or "screenshot.png"
                if not safe.lower().endswith(".png"):
                    safe += ".png"
                path = SCREENSHOT_DIR / safe
            path.write_bytes(data)
            out["path"] = str(path)
        return out

    async def status(self, session_id: str, include_screenshot: bool = False) -> dict:
        s = self._get(session_id)
        result = {
            "session_id": session_id,
            "url": s.page.url,
            "title": await s.page.title(),
            "console": s.console[-50:],
            "failed_requests": s.failed_requests[-50:],
            "http_errors": s.http_errors[-100:],
        }
        if include_screenshot:
            result.update(await self.screenshot(session_id))
        return result

    async def stop(self, session_id: str) -> dict:
        async with self.lock:
            s = self.sessions.pop(session_id, None)
        if not s:
            return {"ok": True, "already_closed": True}
        trace_saved = None
        if s.trace_path:
            try:
                await s.context.tracing.stop(path=s.trace_path)
                trace_saved = s.trace_path
            except Exception:
                trace_saved = None
        try:
            await s.context.close()
        except Exception:
            pass
        try:
            await s.browser.close()
        except Exception:
            pass
        try:
            await s.playwright.stop()
        except Exception:
            pass
        return {"ok": True, "trace_path": trace_saved}


browser_manager = BrowserManager()
