"""Optional OCR backends for knowledge-base images and scanned PDFs."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from langchain_core.messages import HumanMessage

from octop.infra.agents.providers.model_flags import is_chat_eligible_model, is_vision_model
from octop.infra.agents.providers.probe import build_probe_chat_model
from octop.infra.utils.llm_text import llm_text_content
from octop.infra.utils.runtime_packages import PackageInstallSpec, install_packages

OCR_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_ENABLED_KEY = "knowledge_ocr_enabled"
_BACKEND_KEY = "knowledge_ocr_backend"
_MODEL_KEY = "knowledge_ocr_model"
_PROVIDER_ID_KEY = "knowledge_ocr_provider_id"
_LOCAL_PACKAGES = ("rapidocr>=3.4,<4", "onnxruntime>=1.17", "pymupdf>=1.24")
_LOCAL_SPEC = PackageInstallSpec(packages=_LOCAL_PACKAGES, extra_fallback="knowledge-ocr")
_PDF_SPEC = PackageInstallSpec(packages=("pymupdf>=1.24",), extra_fallback="knowledge-ocr")
_OCR_PROMPT = (
    "Transcribe all visible text in this image exactly. Preserve reading order, headings, "
    "lists, and table rows. Return only the transcription, without commentary."
)

logger = logging.getLogger(__name__)
_OCR_REFUSAL_MAX_CHARS = 400
_OCR_REFUSAL_RE = re.compile(
    r"no image (?:was |is |has been )?(?:attached|provided|found|received|included)"
    r"|(?:don'?t|do not|can'?t|cannot|unable to|didn'?t) "
    r"(?:see|find|detect|receive|locate)[^.\n]{0,30}(?:image|picture|photo|scan|attachment)"
    r"|please (?:attach|upload|provide|send|share)[^.\n]{0,40}"
    r"(?:image|picture|photo|scan|attachment|file)"
    r"|未(?:收到|看到|检测到|获取到)[^。\n]{0,10}(?:图片|图像|照片|附件)"
    r"|请(?:上传|提供|重新上传|发送)[^。\n]{0,10}(?:图片|图像|照片|附件|文件)",
    re.IGNORECASE,
)


def _is_no_image_refusal(text: str) -> bool:
    """Detect short remote OCR replies asking the caller to provide an image."""
    stripped = text.strip()
    return bool(
        stripped and len(stripped) <= _OCR_REFUSAL_MAX_CHARS and _OCR_REFUSAL_RE.search(stripped)
    )


_local_engine: Any | None = None
_local_engine_lock = threading.Lock()


class OcrExtractor(Protocol):
    def __call__(self, path: Path) -> str: ...


class PageAwareOcrExtractor(Protocol):
    def ocr_pdf_page_object(self, page: Any) -> str: ...


@dataclass(frozen=True)
class OcrConfig:
    enabled: bool
    backend: str
    model: str
    provider_id: str


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    try:
        decoded: Any = json.loads(value)
    except json.JSONDecodeError:
        decoded = value
    if isinstance(decoded, bool):
        return decoded
    return str(decoded).strip().lower() == "true"


def load_ocr_config(settings_get: Any) -> OcrConfig:
    backend = (settings_get(_BACKEND_KEY) or "onnx").strip().lower()
    return OcrConfig(
        enabled=_as_bool(settings_get(_ENABLED_KEY)),
        backend=backend if backend in {"onnx", "remote"} else "onnx",
        model=(settings_get(_MODEL_KEY) or "").strip(),
        provider_id=(settings_get(_PROVIDER_ID_KEY) or "").strip(),
    )


def local_ocr_deps_available() -> bool:
    try:
        import pymupdf  # noqa: F401
        import rapidocr  # noqa: F401
    except ImportError:
        return False
    return True


def pdf_ocr_deps_available() -> bool:
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_ocr_deps(*, backend: str) -> str:
    if backend == "onnx":
        if local_ocr_deps_available():
            return "ready"
        outcome = install_packages(
            _LOCAL_SPEC,
            is_satisfied=local_ocr_deps_available,
            import_modules=("rapidocr", "onnxruntime", "pymupdf"),
        )
        if not local_ocr_deps_available():
            raise RuntimeError("Local OCR components were installed but could not be loaded")
        return outcome
    if pdf_ocr_deps_available():
        return "ready"
    outcome = install_packages(
        _PDF_SPEC,
        is_satisfied=pdf_ocr_deps_available,
        import_modules=("pymupdf",),
    )
    if not pdf_ocr_deps_available():
        raise RuntimeError("PDF OCR components were installed but could not be loaded")
    return outcome


async def ensure_ocr_deps_async(*, backend: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: ensure_ocr_deps(backend=backend))


def _provider_for_config(provider_repo: Any, config: OcrConfig) -> Any | None:
    if provider_repo is None or not config.provider_id.isdigit():
        return None
    return provider_repo.get(int(config.provider_id))


def _remote_ready(provider: Any | None, model_id: str) -> bool:
    if not (provider and provider.enabled and provider.api_key and provider.base_url and model_id):
        return False
    return any(
        str(model.get("id") or "").strip() == model_id
        and is_chat_eligible_model(
            model,
            provider_name=provider.name,
            provider_api_key=provider.api_key,
        )
        and is_vision_model(model)
        for model in provider.get_models()
    )


def get_ocr_capability(settings_get: Any, provider_repo: Any = None) -> dict[str, Any]:
    config = load_ocr_config(settings_get)
    provider = _provider_for_config(provider_repo, config)
    prerequisites_ok = (
        local_ocr_deps_available()
        if config.backend == "onnx"
        else _remote_ready(provider, config.model)
    )
    return {
        "enabled": config.enabled,
        "backend": config.backend,
        "model": config.model,
        "provider_id": config.provider_id,
        "prerequisites_ok": prerequisites_ok,
        "usable": config.enabled and prerequisites_ok,
        "checks": {
            "deps_available": (
                local_ocr_deps_available() if config.backend == "onnx" else pdf_ocr_deps_available()
            ),
            "provider_ready": _remote_ready(provider, config.model),
        },
    }


def set_ocr_settings(
    settings_set: Any,
    *,
    enabled: bool,
    backend: str | None,
    model: str | None,
    provider_id: str | None,
    provider_repo: Any = None,
) -> None:
    config = validate_ocr_settings(
        enabled=enabled,
        backend=backend,
        model=model,
        provider_id=provider_id,
        provider_repo=provider_repo,
    )
    settings_set(_ENABLED_KEY, "true" if enabled else "false")
    settings_set(_BACKEND_KEY, config.backend)
    settings_set(_MODEL_KEY, config.model if config.backend == "remote" else "rapidocr")
    settings_set(_PROVIDER_ID_KEY, config.provider_id if config.backend == "remote" else "")


def validate_ocr_settings(
    *,
    enabled: bool,
    backend: str | None,
    model: str | None,
    provider_id: str | None,
    provider_repo: Any = None,
) -> OcrConfig:
    selected_backend = (backend or "onnx").strip().lower()
    if selected_backend not in {"onnx", "remote"}:
        raise ValueError("knowledge OCR backend must be onnx or remote")
    selected_model = (model or "").strip()
    selected_provider_id = (provider_id or "").strip()
    if enabled and selected_backend == "onnx" and not local_ocr_deps_available():
        raise RuntimeError("knowledge local OCR prerequisites are not satisfied")
    if enabled and selected_backend == "remote":
        config = OcrConfig(True, selected_backend, selected_model, selected_provider_id)
        if not _remote_ready(_provider_for_config(provider_repo, config), selected_model):
            raise ValueError("knowledge remote OCR provider is not ready")
    return OcrConfig(enabled, selected_backend, selected_model, selected_provider_id)


def require_ocr_extractor(services: Any) -> OcrExtractor:
    config = load_ocr_config(services.settings_repo.get)
    if not config.enabled:
        raise RuntimeError("knowledge OCR is not enabled")
    if config.backend == "onnx":
        if not local_ocr_deps_available():
            raise RuntimeError("knowledge local OCR prerequisites are not satisfied")
        return _extract_local
    provider = _provider_for_config(services.provider_repo, config)
    if not _remote_ready(provider, config.model):
        raise RuntimeError("knowledge remote OCR provider is not ready")
    return _RemoteOcr(provider, config.model)


def optional_ocr_extractor(services: Any) -> OcrExtractor | None:
    if not load_ocr_config(services.settings_repo.get).enabled:
        return None

    extractor: OcrExtractor | None = None

    def get_extractor() -> OcrExtractor:
        nonlocal extractor
        if extractor is None:
            extractor = require_ocr_extractor(services)
        return extractor

    def extract(path: Path) -> str:
        return get_extractor()(path)

    def extract_page_object(page: Any) -> str:
        page_ocr = getattr(get_extractor(), "ocr_pdf_page_object", None)
        if not callable(page_ocr):
            raise RuntimeError("knowledge OCR backend does not support page-local OCR")
        return cast(Callable[[Any], str], page_ocr)(page)

    extract.ocr_pdf_page_object = extract_page_object  # type: ignore[attr-defined]
    return extract


def _render_page(page: Any) -> Any:
    """Render one PDF page within the OCR memory and resolution budget."""
    import numpy as np
    import pymupdf

    api: Any = pymupdf

    rect = page.rect
    scale = min(
        2.0, 2000.0 / max(rect.width, rect.height), (4_000_000 / (rect.width * rect.height)) ** 0.5
    )
    pixmap = page.get_pixmap(matrix=api.Matrix(scale, scale), alpha=False)
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )


def _bgr_image(image: Any) -> Any:
    import cv2

    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def _png_bytes(image: Any) -> bytes:
    import cv2

    ok, encoded = cv2.imencode(".png", _bgr_image(image))
    if not ok:
        raise RuntimeError("could not encode rendered PDF page")
    return cast(bytes, encoded.tobytes())


def _image_inputs(path: Path) -> Iterator[tuple[bytes, str]]:
    if path.suffix.lower() != ".pdf":
        media_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "image/png")
        yield path.read_bytes(), media_type
        return
    import pymupdf

    api: Any = pymupdf

    document = api.open(path)
    try:
        for page_number in range(1, document.page_count + 1):
            yield _png_bytes(_render_page(document[page_number - 1])), "image/png"
    finally:
        document.close()


def _rapidocr_engine() -> Any:
    global _local_engine
    with _local_engine_lock:
        if _local_engine is None:
            import cv2
            from rapidocr import RapidOCR

            cv2.setNumThreads(1)
            _local_engine = RapidOCR(
                params={
                    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                }
            )
        return _local_engine


def _extract_local(path: Path) -> str:
    engine = _rapidocr_engine()
    if path.suffix.lower() == ".pdf":
        import pymupdf

        api: Any = pymupdf

        document = api.open(path)
        try:
            return "\n\n".join(
                text
                for page_number in range(1, document.page_count + 1)
                if (text := _local_page(engine, _render_page(document[page_number - 1])))
            )
        finally:
            document.close()
    with _local_engine_lock:
        result = engine(path.read_bytes())
    return _ocr_result_text(result)


def _ocr_result_text(result: Any) -> str:
    text = getattr(result, "txts", None)
    if isinstance(text, (list, tuple)):
        return "\n".join(str(line) for line in text if str(line).strip())
    return str(text or "").strip()


def _local_page(engine: Any, image: Any) -> str:
    with _local_engine_lock:
        return _ocr_result_text(engine(_bgr_image(image)))


def _local_pdf_page_object(page: Any) -> str:
    return _local_page(_rapidocr_engine(), _render_page(page))


_extract_local.ocr_pdf_page_object = _local_pdf_page_object  # type: ignore[attr-defined]


def _remote_pdf_page_object(page: Any) -> bytes:
    return _png_bytes(_render_page(page))


class _RemoteOcr:
    def __init__(self, provider: Any, model_id: str) -> None:
        self._model = build_probe_chat_model(provider, model_id=model_id)

    def __call__(self, path: Path) -> str:
        return self._recognize_inputs(_image_inputs(path))

    def ocr_pdf_page_object(self, page: Any) -> str:
        return self._recognize_inputs(iter([(_remote_pdf_page_object(page), "image/png")]))

    def _recognize_inputs(self, inputs: Iterator[tuple[bytes, str]]) -> str:
        parts: list[str] = []
        for data, media_type in inputs:
            encoded = base64.b64encode(data).decode("ascii")
            message = HumanMessage(
                content=[
                    {"type": "text", "text": _OCR_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                    },
                ]
            )
            text = llm_text_content(self._model.invoke([message]))
            if not text:
                continue
            if _is_no_image_refusal(text):
                logger.warning("remote OCR returned a no-image refusal")
                continue
            parts.append(text)
        return "\n\n".join(parts)
