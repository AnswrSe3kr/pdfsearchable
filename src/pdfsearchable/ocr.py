"""
OCR robusto para captura de texto (todas as páginas por padrão).
Usa Tesseract (pytesseract) + renderização PyMuPDF. Cache JSON por (file_id, page_num).

Pipeline de pré-processamento:
  1. Detecção de orientação via OSD (PDFSEARCHABLE_OCR_OSD, padrão: ativo)
  2. Remoção de bordas de scan (PDFSEARCHABLE_OCR_BORDER_REMOVE, padrão: ativo)
  3. Binarização Otsu adaptativa (PDFSEARCHABLE_OCR_BINARIZE, padrão: ativo)
  4. Deskew automático por projeção (PDFSEARCHABLE_OCR_DESKEW, padrão: ativo)
  5. Contraste + nitidez (PDFSEARCHABLE_OCR_PREPROCESS, padrão: ativo; ignorado se Otsu ativo)
  6. OCR Tesseract com retry adaptativo por confiança (PDFSEARCHABLE_OCR_CONFIDENCE_THRESHOLD / RETRY_PSM)

Requer numpy para deskew e Otsu; degrada graciosamente se ausente.
"""

import json
import os
import re
from pathlib import Path

from pdfsearchable.audit import get_logger as _get_logger
from pdfsearchable.store import OCR_CACHE_DIR, _ensure_store

_log = _get_logger("pdfsearchable.ocr")

# Mínimo de caracteres (após strip) para considerar página com texto nativo
MIN_CHARS_FOR_NATIVE = 50

_OCR_AVAILABLE: bool | None = None


def ocr_available() -> bool:
    global _OCR_AVAILABLE
    if _OCR_AVAILABLE is not None:
        return _OCR_AVAILABLE
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        _OCR_AVAILABLE = True
    except Exception:
        _OCR_AVAILABLE = False
    return _OCR_AVAILABLE


# Códigos Tesseract para PDFSEARCHABLE_OCR_LANG (separar por +)
# por=português (BR/PT), eng=inglês, spa=espanhol, fra=francês, ita=italiano,
# rus=russo, deu=alemão, heb=hebraico, nld=holandês, jpn=japonês, chi_sim=chinês simplificado
DEFAULT_OCR_LANGS = "por+eng+spa+fra+ita+rus+deu+heb"

# DPI da renderização da página para OCR (env PDFSEARCHABLE_OCR_DPI)
DEFAULT_OCR_DPI = 300


def get_ocr_dpi() -> int:
    raw = os.environ.get("PDFSEARCHABLE_OCR_DPI", "").strip()
    if not raw:
        return DEFAULT_OCR_DPI
    try:
        dpi = int(raw)
        return max(72, min(600, dpi))
    except ValueError:
        return DEFAULT_OCR_DPI


def get_ocr_lang() -> str:
    return os.environ.get("PDFSEARCHABLE_OCR_LANG", DEFAULT_OCR_LANGS).strip() or DEFAULT_OCR_LANGS


def get_ocr_psm() -> int:
    raw = os.environ.get("PDFSEARCHABLE_OCR_PSM", "").strip()
    if not raw:
        return 3
    try:
        psm = int(raw)
        return max(0, min(13, psm))
    except ValueError:
        return 3


def get_ocr_workers() -> int:
    """
    Número de workers para OCR paralelo por página (PDFSEARCHABLE_OCR_WORKERS).
    0 = auto (3/4 dos CPUs, mínimo 2, máximo 8). 1 = sequencial.

    Tesseract liberta o GIL em C; threads ganham bem. Subimos de CPU/2 para 3*CPU/4
    porque benchmarks mostram ~15-25% de throughput adicional em docs longos sem
    afectar latência noutras tarefas.
    """
    raw = os.environ.get("PDFSEARCHABLE_OCR_WORKERS", "").strip()
    if not raw:
        try:
            cpus = os.cpu_count() or 4
            n = (cpus * 3) // 4
            return max(2, min(8, n))
        except Exception:
            return 4
    try:
        n = int(raw)
        if n == 0:
            # auto
            cpus = os.cpu_count() or 4
            return max(2, min(8, (cpus * 3) // 4))
        return max(1, min(32, n))
    except ValueError:
        return 4


def get_ocr_oem() -> int:
    """
    Motor do Tesseract (OEM). Variável PDFSEARCHABLE_OCR_OEM.
    0 = legado, 1 = LSTM apenas, 2 = legado+LSTM, 3 = padrão (automático). LSTM costuma ser mais preciso.
    """
    raw = os.environ.get("PDFSEARCHABLE_OCR_OEM", "").strip()
    if not raw:
        return 3
    try:
        oem = int(raw)
        return max(0, min(3, oem))
    except ValueError:
        return 3


# ── Env var helpers ───────────────────────────────────────────────────────────


def _ocr_preprocess_enabled() -> bool:
    """True se PDFSEARCHABLE_OCR_PREPROCESS=1 (contraste + nitidez; ignorado se Otsu ativo)."""
    return os.environ.get("PDFSEARCHABLE_OCR_PREPROCESS", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _deskew_enabled() -> bool:
    """True se PDFSEARCHABLE_OCR_DESKEW=1 (correção automática de inclinação, padrão: ativo)."""
    return os.environ.get("PDFSEARCHABLE_OCR_DESKEW", "1").strip().lower() in ("1", "true", "yes")


def _binarize_enabled() -> bool:
    """True se PDFSEARCHABLE_OCR_BINARIZE=1 (binarização Otsu adaptativa, padrão: ativo)."""
    return os.environ.get("PDFSEARCHABLE_OCR_BINARIZE", "1").strip().lower() in ("1", "true", "yes")


def _osd_enabled() -> bool:
    """True se PDFSEARCHABLE_OCR_OSD=1 (detecção automática de orientação de página, padrão: ativo)."""
    return os.environ.get("PDFSEARCHABLE_OCR_OSD", "1").strip().lower() in ("1", "true", "yes")


def _border_remove_enabled() -> bool:
    """True se PDFSEARCHABLE_OCR_BORDER_REMOVE=1 (remoção de bordas escuras de scan, padrão: ativo)."""
    return os.environ.get("PDFSEARCHABLE_OCR_BORDER_REMOVE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _historical_mode() -> str:
    """
    Modo de processamento para documentos históricos (PDFSEARCHABLE_OCR_HISTORICAL).
    Valores: 'off' (padrão), 'on' (forçar), 'auto' (detectar automaticamente).
    Quando ativo, usa Sauvola (local) em vez de Otsu (global), CLAHE para contraste
    adaptativo, limpeza morfológica contra ruído/bleed-through, e modelos HTR maiores.
    """
    raw = os.environ.get("PDFSEARCHABLE_OCR_HISTORICAL", "off").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "on"
    if raw == "auto":
        return "auto"
    return "off"


def _get_confidence_threshold() -> float:
    """Limiar de confiança OCR (0–100). Abaixo disto tenta retry com PSM alternativo."""
    raw = os.environ.get("PDFSEARCHABLE_OCR_CONFIDENCE_THRESHOLD", "40").strip()
    try:
        return max(0.0, min(100.0, float(raw)))
    except ValueError:
        return 40.0


def _get_retry_psm_list() -> list[int]:
    """Lista de PSMs alternativos a tentar quando confiança < limiar. Ex.: '6,4,11'."""
    raw = os.environ.get("PDFSEARCHABLE_OCR_RETRY_PSM", "6,4").strip()
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        try:
            v = int(part)
            if 0 <= v <= 13:
                result.append(v)
        except ValueError:
            pass
    return result if result else [6, 4]


# ── Numpy helper ──────────────────────────────────────────────────────────────

# Flag para avisar apenas uma vez quando numpy não está disponível,
# evitando mensagens repetidas para cada página processada.
_numpy_absent_warned: bool = False


def _numpy_available() -> bool:
    global _numpy_absent_warned
    try:
        import numpy  # noqa: F401

        return True
    except ImportError:
        if not _numpy_absent_warned:
            _numpy_absent_warned = True
            _log.warning(
                "numpy não está instalado — binarização Otsu, deskew automático e "
                "detecção de página em branco ficam desativados. "
                "Instale com: pip install numpy  (ou pip install pdfsearchable[ocr])"
            )
        return False


# ── Pré-processamento de imagem ───────────────────────────────────────────────


def _remove_scan_border(image):
    """
    Remove bordas escuras de scan (sombras de scanner, margens negras).
    Usa projeção de brilho por linha/coluna para encontrar a área de conteúdo.
    Só corta se a borda for > 5% da dimensão. Requer numpy.
    """
    if not _numpy_available():
        return image
    try:
        import numpy as np

        gray = image.convert("L") if image.mode != "L" else image.copy()
        arr = np.array(gray, dtype=np.float32)
        row_mean = arr.mean(axis=1)
        col_mean = arr.mean(axis=0)
        content_threshold = 60.0  # pixels mais claros que 60/255 são "conteúdo"
        rows_content = np.where(row_mean > content_threshold)[0]
        cols_content = np.where(col_mean > content_threshold)[0]
        if len(rows_content) < 10 or len(cols_content) < 10:
            return image  # imagem muito escura, não cortar
        h, w = arr.shape
        top = int(rows_content[0])
        bottom = int(rows_content[-1]) + 1
        left = int(cols_content[0])
        right = int(cols_content[-1]) + 1
        # Só cortar se a margem for significativa (> 5% da dimensão)
        if top > h * 0.05 or bottom < h * 0.95 or left > w * 0.05 or right < w * 0.95:
            return image.crop((left, top, right, bottom))
        return image
    except Exception as _e:
        _log.debug("_remove_scan_border falhou: %s", _e)
        return image


def _otsu_threshold_value(arr) -> int:
    """Calcula limiar de Otsu via variância inter-classes (numpy). Retorna valor 0–255."""
    try:
        import numpy as np

        flat = arr.flatten().astype(np.int32)
        hist, _ = np.histogram(flat, bins=256, range=(0, 256))
        total = float(flat.size)
        if total == 0:
            return 128
        sum_total = float(np.dot(np.arange(256), hist))
        sum_b = 0.0
        w_b = 0.0
        max_var = 0.0
        threshold = 128
        for t in range(256):
            w_b += hist[t]
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += t * hist[t]
            m_b = sum_b / w_b
            m_f = (sum_total - sum_b) / w_f
            var = w_b * w_f * (m_b - m_f) ** 2
            if var > max_var:
                max_var = var
                threshold = t
        return int(threshold)
    except Exception:
        return 128


def _binarize_otsu(image):
    """
    Binarização Otsu: converte para preto/branco com limiar adaptativo global.
    Pixels > threshold → branco (fundo); pixels ≤ threshold → preto (texto).
    Melhora drasticamente scans com fundo não-uniforme. Requer numpy.
    """
    if not _numpy_available():
        return image
    try:
        import numpy as np
        from PIL import Image as PILImage

        gray = image.convert("L") if image.mode != "L" else image.copy()
        arr = np.array(gray)
        t = _otsu_threshold_value(arr)
        # Clamp: evita limiar nos extremos (imagens puras preto/branco)
        t = max(1, min(254, t))
        # pixels > t → branco (background), pixels ≤ t → preto (texto)
        binary = ((arr > t) * 255).astype(np.uint8)
        return PILImage.fromarray(binary, mode="L")
    except Exception as _e:
        _log.debug("_binarize_otsu falhou: %s", _e)
        return image


def _binarize_sauvola(image, window_size: int = 25, k: float = 0.2, R: float = 128.0):
    """
    Binarização Sauvola: limiar local adaptativo baseado em média e desvio padrão
    numa janela deslizante. Superior a Otsu para documentos históricos com:
      - Fundo não-uniforme (papel amarelado, manchas)
      - Variação de iluminação (scan desigual)
      - Bleed-through (tinta do verso)
    Usa integral images para O(1) por pixel. Requer numpy.

    Parâmetros:
      window_size: tamanho da janela local (ímpar, padrão 25 px)
      k: sensibilidade [0.1–0.5]; menor = mais texto detectado (padrão 0.2)
      R: alcance dinâmico do desvio padrão (padrão 128 para 8-bit)
    """
    if not _numpy_available():
        return image
    try:
        import numpy as np
        from PIL import Image as PILImage

        gray = image.convert("L") if image.mode != "L" else image.copy()
        arr = np.array(gray, dtype=np.float64)
        h, w = arr.shape

        # Janela deve ser ímpar e no mínimo 3
        ws = max(3, window_size | 1)
        half = ws // 2

        # Integral images para cálculo eficiente de média e variância local
        integral = np.zeros((h + 1, w + 1), dtype=np.float64)
        integral_sq = np.zeros((h + 1, w + 1), dtype=np.float64)
        np.cumsum(np.cumsum(arr, axis=0), axis=1, out=integral[1:, 1:])
        np.cumsum(np.cumsum(arr ** 2, axis=0), axis=1, out=integral_sq[1:, 1:])

        # Coordenadas das janelas (clipped nas bordas)
        rows = np.arange(h)
        cols = np.arange(w)
        y1 = np.clip(rows - half, 0, h - 1).astype(int)
        y2 = np.clip(rows + half, 0, h - 1).astype(int) + 1
        x1 = np.clip(cols - half, 0, w - 1).astype(int)
        x2 = np.clip(cols + half, 0, w - 1).astype(int) + 1

        # Broadcast para calcular somas e somas de quadrados por janela
        Y1 = y1[:, None]  # (h, 1)
        Y2 = y2[:, None]
        X1 = x1[None, :]  # (1, w)
        X2 = x2[None, :]

        area = (Y2 - Y1) * (X2 - X1)
        area = np.maximum(area, 1)  # evitar divisão por zero

        s = integral[Y2, X2] - integral[Y1, X2] - integral[Y2, X1] + integral[Y1, X1]
        sq = integral_sq[Y2, X2] - integral_sq[Y1, X2] - integral_sq[Y2, X1] + integral_sq[Y1, X1]

        mean = s / area
        variance = np.maximum(sq / area - mean ** 2, 0)
        std = np.sqrt(variance)

        # Limiar Sauvola: T(x,y) = mean * (1 + k * (std / R - 1))
        threshold = mean * (1.0 + k * (std / R - 1.0))

        binary = ((arr > threshold) * 255).astype(np.uint8)
        return PILImage.fromarray(binary, mode="L")
    except Exception as _e:
        _log.debug("_binarize_sauvola falhou: %s — fallback para Otsu", _e)
        return _binarize_otsu(image)


def _clahe(image, clip_limit: float = 2.0, tile_size: int = 8):
    """
    CLAHE — Contrast Limited Adaptive Histogram Equalization.
    Melhora contraste local em documentos com texto desbotado ou iluminação irregular.
    Divide a imagem em tiles, equaliza cada tile com clip de histograma,
    e interpola bilinearmente entre tiles para evitar artefactos.
    Requer numpy.

    Parâmetros:
      clip_limit: limite de amplificação do histograma (padrão 2.0)
      tile_size: número de tiles em cada dimensão (padrão 8×8)
    """
    if not _numpy_available():
        return image
    try:
        import numpy as np
        from PIL import Image as PILImage

        gray = image.convert("L") if image.mode != "L" else image.copy()
        arr = np.array(gray, dtype=np.uint8)
        h, w = arr.shape
        n_bins = 256

        # Tamanho de cada tile
        ty = max(1, h // tile_size)
        tx = max(1, w // tile_size)
        # Número real de tiles (pode ser ligeiramente diferente)
        ny = max(1, (h + ty - 1) // ty)
        nx = max(1, (w + tx - 1) // tx)

        # Calcular LUT (lookup table) equalizada para cada tile
        luts = np.zeros((ny, nx, n_bins), dtype=np.uint8)
        for iy in range(ny):
            for ix in range(nx):
                r0, r1 = iy * ty, min((iy + 1) * ty, h)
                c0, c1 = ix * tx, min((ix + 1) * tx, w)
                tile = arr[r0:r1, c0:c1]
                n_pixels = tile.size
                if n_pixels == 0:
                    luts[iy, ix] = np.arange(n_bins, dtype=np.uint8)
                    continue
                hist, _ = np.histogram(tile, bins=n_bins, range=(0, 256))
                # Clip histogram
                clip = max(1, int(clip_limit * n_pixels / n_bins))
                excess = np.sum(np.maximum(hist - clip, 0))
                hist = np.minimum(hist, clip)
                hist += excess // n_bins  # redistribuir excesso uniformemente
                # CDF
                cdf = np.cumsum(hist).astype(np.float64)
                cdf_min = cdf[cdf > 0].min() if np.any(cdf > 0) else 0
                denom = max(1, n_pixels - cdf_min)
                lut = ((cdf - cdf_min) / denom * 255.0).clip(0, 255).astype(np.uint8)
                luts[iy, ix] = lut

        # Aplicar com interpolação bilinear entre tiles adjacentes (vectorizado)
        ys = np.arange(h, dtype=np.float64)
        xs = np.arange(w, dtype=np.float64)
        fy = np.clip((ys - ty / 2.0) / ty, 0, ny - 1.001)
        fx = np.clip((xs - tx / 2.0) / tx, 0, nx - 1.001)
        iy0 = fy.astype(np.intp)
        ix0 = fx.astype(np.intp)
        iy1 = np.minimum(iy0 + 1, ny - 1)
        ix1 = np.minimum(ix0 + 1, nx - 1)
        dy = fy - iy0  # (h,)
        dx = fx - ix0  # (w,)

        # Broadcast: (h, w)
        IY0 = iy0[:, None].repeat(w, axis=1)
        IY1 = iy1[:, None].repeat(w, axis=1)
        IX0 = ix0[None, :].repeat(h, axis=0)
        IX1 = ix1[None, :].repeat(h, axis=0)
        DY = dy[:, None]  # (h, 1)
        DX = dx[None, :]  # (1, w)

        vals = arr  # (h, w) uint8
        v00 = luts[IY0, IX0, vals].astype(np.float64)
        v01 = luts[IY0, IX1, vals].astype(np.float64)
        v10 = luts[IY1, IX0, vals].astype(np.float64)
        v11 = luts[IY1, IX1, vals].astype(np.float64)
        result = (v00 * (1 - DY) * (1 - DX)
                  + v01 * (1 - DY) * DX
                  + v10 * DY * (1 - DX)
                  + v11 * DY * DX).astype(np.uint8)
        return PILImage.fromarray(result, mode="L")
    except Exception as _e:
        _log.debug("_clahe falhou: %s", _e)
        return image


def _morphological_clean(image, kernel_size: int = 2):
    """
    Limpeza morfológica para documentos históricos:
      1. Opening (erosão + dilatação): remove pontos/ruído pequeno
      2. Closing (dilatação + erosão): preenche pequenas falhas em caracteres
    Remove bleed-through, manchas de tinta e artefactos de scan.
    Opera em imagem binarizada (preto/branco). Requer numpy.
    """
    if not _numpy_available():
        return image
    try:
        import numpy as np
        from PIL import Image as PILImage

        gray = image.convert("L") if image.mode != "L" else image.copy()
        arr = np.array(gray)
        # Trabalhar com binário (texto=0, fundo=255)
        binary = (arr > 127).astype(np.uint8)  # 1=fundo, 0=texto

        ks = max(2, kernel_size)

        def _erode(img, k):
            """Erosão: pixel é 1 apenas se toda a vizinhança k×k for 1."""
            h, w = img.shape
            out = np.ones_like(img)
            for dy in range(k):
                for dx in range(k):
                    out &= img[dy:h - k + dy + 1, dx:w - k + dx + 1] if (
                        h - k + dy + 1 > dy and w - k + dx + 1 > dx
                    ) else img[:1, :1]
            result = np.ones_like(img)
            pad = k // 2
            if pad < h - pad and pad < w - pad:
                result[pad:h - pad, pad:w - pad] = out[:h - 2 * pad, :w - 2 * pad]
            return result

        def _dilate(img, k):
            """Dilatação: pixel é 1 se qualquer vizinho na janela k×k for 1."""
            h, w = img.shape
            out = np.zeros_like(img)
            for dy in range(k):
                for dx in range(k):
                    ey = h - k + dy + 1
                    ex = w - k + dx + 1
                    if ey > dy and ex > dx:
                        out[dy:ey, dx:ex] |= img[dy:ey, dx:ex]
            result = np.zeros_like(img)
            pad = k // 2
            if pad < h - pad and pad < w - pad:
                result[pad:h - pad, pad:w - pad] = out[pad:h - pad, pad:w - pad]
            # Preservar bordas
            result[:pad, :] = img[:pad, :]
            result[h - pad:, :] = img[h - pad:, :]
            result[:, :pad] = img[:, :pad]
            result[:, w - pad:] = img[:, w - pad:]
            return result

        # Opening: erosão → dilatação (remove ruído pequeno)
        opened = _dilate(_erode(binary, ks), ks)
        # Closing suave: dilatação → erosão (preenche falhas em caracteres)
        cleaned = _erode(_dilate(opened, ks), ks)

        result = (cleaned * 255).astype(np.uint8)
        return PILImage.fromarray(result, mode="L")
    except Exception as _e:
        _log.debug("_morphological_clean falhou: %s", _e)
        return image


def _detect_historical_page(image) -> bool:
    """
    Heurística para detectar se uma página é de documento histórico/envelhecido.
    Analisa:
      1. Cor do papel: documentos antigos têm papel amarelado (alto R+G, baixo B)
      2. Variância de contraste: scans históricos têm mais variação local
      3. Nível de ruído: documentos antigos têm mais ruído (manchas, desgaste)
    Retorna True se parece ser documento histórico.
    """
    if not _numpy_available():
        return False
    try:
        import numpy as np

        # Converter para RGB se necessário
        rgb = image.convert("RGB") if image.mode != "RGB" else image
        arr = np.array(rgb, dtype=np.float32)

        # 1. Detecção de papel amarelado: R e G altos, B proporcionalmente baixo
        # Amostrar cantos e bordas (tipicamente fundo/papel)
        h, w = arr.shape[:2]
        margin_y = max(1, h // 10)
        margin_x = max(1, w // 10)
        # Amostrar 4 cantos
        corners = np.concatenate([
            arr[:margin_y, :margin_x].reshape(-1, 3),
            arr[:margin_y, -margin_x:].reshape(-1, 3),
            arr[-margin_y:, :margin_x].reshape(-1, 3),
            arr[-margin_y:, -margin_x:].reshape(-1, 3),
        ])
        mean_rgb = corners.mean(axis=0)
        r, g, b = mean_rgb[0], mean_rgb[1], mean_rgb[2]

        # Papel amarelado: R > 160, G > 140, B < R-30 e B < G-20
        yellowed = (r > 160 and g > 140 and b < r - 30 and b < g - 20)

        # 2. Variância local alta (texto irregular, manchas)
        gray = np.array(image.convert("L"), dtype=np.float32)
        # Dividir em blocos 8×8 e medir variância entre blocos
        bs = 8
        block_h = h // bs
        block_w = w // bs
        if block_h > 2 and block_w > 2:
            blocks = gray[:block_h * bs, :block_w * bs].reshape(block_h, bs, block_w, bs)
            block_means = blocks.mean(axis=(1, 3))
            contrast_var = float(np.std(block_means))
            high_contrast_var = contrast_var > 40  # scans históricos > 40
        else:
            high_contrast_var = False

        # 3. Presença de ruído: proporção de pixels "cinza" (não claramente preto nem branco)
        gray_flat = gray.flatten()
        mid_gray = np.sum((gray_flat > 80) & (gray_flat < 180)) / max(1, len(gray_flat))
        noisy = mid_gray > 0.25  # > 25% de pixels na zona cinzenta

        # Pontuação: 2+ de 3 indicadores → histórico
        score = int(yellowed) + int(high_contrast_var) + int(noisy)
        is_hist = score >= 2
        if is_hist:
            _log.debug(
                "Documento histórico detectado (score=%d/3): amarelado=%s, "
                "var_contraste=%s, ruído=%s",
                score, yellowed, high_contrast_var, noisy,
            )
        return is_hist
    except Exception as _e:
        _log.debug("_detect_historical_page falhou: %s", _e)
        return False


def _is_blank_page(image) -> bool:
    """
    Detecta rapidamente se uma página é em branco ou quase em branco.
    Usa a média de brilho da imagem em escala de cinza.
    Limiar: se mais de 99.5% dos pixels forem claros (>240/255), considera em branco.
    Threshold elevado (99.5%) para evitar falsos positivos em páginas digitalizadas
    com texto esparso (cabeçalhos, carimbos, assinaturas) que seriam descartadas.
    Retorna True se a página estiver em branco, False caso contrário.
    """
    if not _numpy_available():
        return False
    try:
        import numpy as np

        gray = image.convert("L") if image.mode != "L" else image
        arr = np.array(gray)
        # Percentagem de pixels muito claros (fundo branco/quase branco)
        blank_ratio = float(np.mean(arr > 240))
        return blank_ratio > 0.995
    except Exception as _e:
        _log.debug("_is_blank_page falhou: %s", _e)
        return False


def _detect_skew_angle(image) -> float:
    """
    Detecta ângulo de inclinação de página usando busca binária no perfil de projeção.
    Estratégia em duas fases:
      1. Busca grosseira: testa 9 ângulos espaçados de 2.5° (-10° a +10°)
      2. Refinamento: busca binária em torno do melhor candidato com passo 0.5°
    Reduz de 41 rotações para ~13 no caso médio (vs 41 antes), mantendo precisão.
    Faz downsample adaptativo (max 400px) para velocidade. Requer numpy.
    Retorna ângulo em graus (positivo = sentido horário). Retorna 0.0 se página em branco.
    """
    if not _numpy_available():
        return 0.0
    try:
        import numpy as np
        from PIL import Image as PILImage

        gray = image.convert("L") if image.mode != "L" else image.copy()

        # Detectar página em branco antes do deskew (evita rotações desnecessárias)
        arr_check = np.array(gray)
        if float(np.mean(arr_check > 240)) > 0.98:
            return 0.0

        w, h = gray.size
        # Downsample adaptativo: limitar a 400px para velocidade (reduz custo de rotações)
        max_w = 400
        if w > max_w:
            scale = max_w / w
            gray = gray.resize((max_w, max(1, int(h * scale))), PILImage.LANCZOS)
        arr = np.array(gray)
        # Binarizar para realçar linhas de texto
        t = _otsu_threshold_value(arr)
        t = max(1, min(254, t))
        binary_arr = (arr <= t).astype(np.float32)  # 1.0 onde escuro (texto)
        img_bin = PILImage.fromarray((binary_arr * 255).astype(np.uint8))

        def _score(angle: float) -> float:
            rotated = img_bin.rotate(angle, expand=False, fillcolor=0)
            arr_r = np.array(rotated) / 255.0
            return float(np.var(arr_r.sum(axis=1)))

        # Fase 1: busca grosseira com passo de 2.5° (-10° a +10°, 9 pontos)
        coarse_angles = [step * 2.5 for step in range(-4, 5)]
        coarse_scores = [(a, _score(a)) for a in coarse_angles]
        best_coarse = max(coarse_scores, key=lambda x: x[1])
        best_angle = best_coarse[0]

        # Fase 2: refinamento em torno do melhor candidato com passo 0.5°
        # Testa apenas os ângulos dentro de ±2.5° do melhor (max 10 rotações adicionais)
        fine_low = best_angle - 2.0
        fine_high = best_angle + 2.0
        fine_step = 0.5
        fine_angle = fine_low
        best_score = best_coarse[1]
        while fine_angle <= fine_high:
            if abs(fine_angle - best_angle) > 0.01:  # não retestar o já avaliado
                s = _score(fine_angle)
                if s > best_score:
                    best_score = s
                    best_angle = fine_angle
            fine_angle = round(fine_angle + fine_step, 2)

        return best_angle
    except Exception:
        return 0.0


def _deskew_image(image, angle: float):
    """Corrige inclinação da imagem pelo ângulo fornecido (graus). Fundo branco."""
    if abs(angle) < 0.1:
        return image
    try:
        fill = 255 if image.mode == "L" else (255, 255, 255)
        return image.rotate(-angle, expand=True, fillcolor=fill)
    except Exception as _e:
        _log.debug("deskew falhou: %s", _e)
        return image


def _preprocess_image_for_ocr(image, force_historical: bool | None = None):
    """
    Pipeline de pré-processamento robusto para OCR.

    Pipeline padrão (documentos modernos):
      1. Grayscale
      2. Remoção de bordas de scan
      3. Binarização Otsu (global)
      4. Deskew automático
      5. Contraste + nitidez (se Otsu inativo)

    Pipeline histórico (PDFSEARCHABLE_OCR_HISTORICAL=on/auto):
      1. Grayscale
      2. Remoção de bordas de scan
      3. CLAHE — contraste adaptativo local (antes da binarização)
      4. Binarização Sauvola (local, adaptativa) em vez de Otsu
      5. Limpeza morfológica (remove ruído, bleed-through)
      6. Deskew automático (com precisão refinada)
    """
    from PIL import ImageEnhance, ImageFilter

    if not hasattr(image, "convert"):
        return image

    # Determinar se usar pipeline histórico
    hist_mode = _historical_mode()
    if force_historical is not None:
        use_historical = force_historical
    elif hist_mode == "on":
        use_historical = True
    elif hist_mode == "auto":
        use_historical = _detect_historical_page(image)
    else:
        use_historical = False

    # 1. Grayscale (manter cópia RGB para detecção histórica se auto)
    img = image.convert("L") if image.mode != "L" else image.copy()

    # 2. Remoção de bordas de scan
    if _border_remove_enabled():
        img = _remove_scan_border(img)

    if use_historical and _numpy_available():
        # ── Pipeline histórico ──
        _log.debug("A usar pipeline de pré-processamento histórico.")
        # 3h. CLAHE: melhora contraste local antes da binarização
        img = _clahe(img, clip_limit=2.5, tile_size=8)
        # 4h. Binarização Sauvola (adaptativa local — muito melhor que Otsu
        #     para papel envelhecido, manchas, iluminação desigual)
        if _binarize_enabled():
            img = _binarize_sauvola(img, window_size=31, k=0.2)
        # 5h. Limpeza morfológica (remove pontos, bleed-through)
        img = _morphological_clean(img, kernel_size=2)
        # 6h. Deskew automático
        if _deskew_enabled():
            angle = _detect_skew_angle(img)
            if abs(angle) >= 0.1:
                img = _deskew_image(img, angle)
    else:
        # ── Pipeline padrão ──
        # 3. Binarização Otsu (mais eficaz que contraste simples para scans)
        if _binarize_enabled() and _numpy_available():
            img = _binarize_otsu(img)
        # 4. Deskew automático
        if _deskew_enabled() and _numpy_available():
            angle = _detect_skew_angle(img)
            if abs(angle) >= 0.1:
                img = _deskew_image(img, angle)
        # 5. Contraste + nitidez (apenas quando Otsu não ativo — já é B&W)
        if _ocr_preprocess_enabled() and not (_binarize_enabled() and _numpy_available()):
            try:
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(1.3)
            except Exception as _e:
                _log.debug("Contraste de imagem falhou (não crítico): %s", _e)
            try:
                img = img.filter(ImageFilter.SHARPEN)
            except Exception as _e:
                _log.debug("Nitidez de imagem falhou (não crítico): %s", _e)
    return img


def _normalize_ocr_text(text: str) -> str:
    """Normaliza texto retornado pelo OCR: colapsa espaços e quebras de linha excessivas."""
    if not text or not text.strip():
        return text or ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Cache JSON (v2) ───────────────────────────────────────────────────────────
# v1 = texto plano (legado); v2 = JSON {"text": "...", "confidence": 85.3, "version": 2}

_CACHE_VERSION = 2


def _cache_key(file_id: str, content_hash: str | None = None) -> str:
    """Chave de cache: content_hash (reutiliza OCR entre arquivos com mesmo conteúdo) ou file_id."""
    return (content_hash or file_id).strip() if (content_hash or file_id) else file_id


def _htr_cache_suffix() -> str:
    """Retorna o sufixo de cache para o backend HTR activo: '_htr', '_transkribus' ou '_escriptorium'."""
    try:
        from pdfsearchable.htr import get_htr_backend, HTR_BACKEND_TROCR
        backend = get_htr_backend()
        return "_htr" if backend == HTR_BACKEND_TROCR else f"_{backend}"
    except Exception:
        return "_htr"


def _cache_path(cache_key: str, page_num: int, use_htr: bool = False) -> Path:
    _ensure_store()
    suffix = _htr_cache_suffix() if use_htr else ""
    return OCR_CACHE_DIR / f"{cache_key}_p{page_num:04d}{suffix}.txt"


def _get_cached(cache_key: str, page_num: int, use_htr: bool = False) -> tuple[str, float] | None:
    """
    Retorna (texto, confiança) do cache ou None se não encontrado.
    cache_key: content_hash ou file_id (permite reutilizar OCR por conteúdo).
    Compatível com cache v1 (texto plano → confiança -1.0) e v2 (JSON).
    """
    p = _cache_path(cache_key, page_num, use_htr=use_htr)
    if not p.exists():
        return None
    try:
        content = p.read_text(encoding="utf-8")
        if not content:
            return None
        # Tenta JSON (v2)
        try:
            data = json.loads(content)
            if isinstance(data, dict) and "text" in data:
                return data["text"], float(data.get("confidence", -1.0))
        except (json.JSONDecodeError, ValueError):
            pass
        # Formato legado v1: texto plano
        return content, -1.0
    except OSError:
        return None


def _set_cache(
    cache_key: str, page_num: int, text: str, confidence: float = -1.0, use_htr: bool = False
) -> None:
    """Grava cache em formato JSON v2 com texto e confiança. cache_key: content_hash ou file_id."""
    try:
        data = json.dumps(
            {"text": text, "confidence": round(confidence, 1), "version": _CACHE_VERSION},
            ensure_ascii=False,
        )
        _cache_path(cache_key, page_num, use_htr=use_htr).write_text(data, encoding="utf-8")
    except OSError:
        pass


# ── Detecção de orientação (OSD) ─────────────────────────────────────────────


def _detect_page_orientation(img) -> int:
    """
    Detecta rotação da página via Tesseract OSD (--psm 0).
    Retorna ângulo de rotação em graus (0, 90, 180 ou 270).
    Retorna 0 em caso de falha (imagem com pouco texto ou sem suporte a OSD).
    Controlado por PDFSEARCHABLE_OCR_OSD=1 (padrão: ativo).
    """
    if not _osd_enabled() or not ocr_available():
        return 0
    try:
        import pytesseract

        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        rotate = int(osd.get("rotate", 0) or 0)
        return rotate if rotate in (0, 90, 180, 270) else 0
    except Exception:
        return 0


# ── OCR com confiança e retry adaptativo ─────────────────────────────────────


def _ocr_get_confidence(img, lang: str, psm: int, oem: int) -> float:
    """
    Executa Tesseract em modo image_to_data e retorna confiança média (0–100).
    Ignora entradas com confiança -1 (espaços/blocos sem texto).
    Retorna -1.0 se não houver dados suficientes.
    """
    try:
        import pytesseract

        config = f"--psm {psm} --oem {oem}"
        data = pytesseract.image_to_data(
            img, lang=lang, config=config, output_type=pytesseract.Output.DICT
        )
        confs = [c for c in data.get("conf", []) if isinstance(c, (int, float)) and c >= 0]
        if not confs:
            return -1.0
        return round(sum(confs) / len(confs), 1)
    except Exception as _conf_err:
        _log.debug("Falha ao obter confiança OCR: %s", _conf_err)
        return -1.0


def _ocr_with_retry(
    img, lang: str, base_psm: int, oem: int, confidence_threshold: float
) -> tuple[str, float]:
    """
    Executa OCR com o PSM base; se confiança < limiar, tenta PSMs alternativos
    (PDFSEARCHABLE_OCR_RETRY_PSM, padrão: 6,4).
    Retorna (texto, melhor_confiança) do melhor resultado obtido.
    """
    import pytesseract

    def _run_psm(psm_val: int) -> tuple[str, float]:
        config = f"--psm {psm_val} --oem {oem}"
        try:
            raw = pytesseract.image_to_string(img, lang=lang, config=config) or ""
            text = _normalize_ocr_text(raw)
        except Exception as _tess_err:
            _log.warning("Tesseract falhou (psm=%d): %s", psm_val, _tess_err)
            text = ""
        if not text.strip():
            return text, -1.0
        conf = _ocr_get_confidence(img, lang, psm_val, oem)
        return text, conf

    best_text, best_conf = _run_psm(base_psm)
    # Retry se confiança insuficiente
    if best_conf < confidence_threshold:
        for retry_psm in _get_retry_psm_list():
            if retry_psm == base_psm:
                continue
            t, c = _run_psm(retry_psm)
            if c > best_conf and t.strip():
                best_text, best_conf = t, c
                if best_conf >= confidence_threshold:
                    break
    return best_text, best_conf


# ── Pipeline OCR principal ────────────────────────────────────────────────────


def _run_ocr_full(image_bytes: bytes, lang: str | None = None) -> tuple[str, float]:
    """
    Pipeline OCR completo:
      0. Short-circuit: página em branco → retorna ("", -1.0) imediatamente
      1. Abre imagem e detecta orientação (OSD)
      2. Corrige rotação se necessário
      3. Pré-processa (borda, Otsu, deskew, contraste)
      4. Tesseract com retry adaptativo por confiança
    Retorna (texto_normalizado, confiança_média).
    """
    import io
    from PIL import Image

    lang = lang or get_ocr_lang()
    psm = get_ocr_psm()
    oem = get_ocr_oem()
    threshold = _get_confidence_threshold()
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as _img_err:
        _log.warning("Falha ao abrir imagem para OCR: %s — página ignorada", _img_err)
        return "", -1.0
    # 0. Short-circuit: página em branco — evita todo o pipeline OCR
    if _is_blank_page(img):
        _log.debug("Página em branco detectada — OCR ignorado.")
        return "", -1.0
    # 1. Detecção de orientação — antes do pré-processamento
    rotation = _detect_page_orientation(img)
    if rotation:
        img = img.rotate(-rotation, expand=True)
    # 2. Pré-processamento robusto
    img = _preprocess_image_for_ocr(img)
    # 3. OCR com retry adaptativo
    return _ocr_with_retry(img, lang, psm, oem, threshold)


def run_ocr_on_image(image_bytes: bytes, lang: str | None = None) -> str:
    """
    Executa OCR em bytes de imagem (PNG/JPEG). Retorna texto.
    Com PDFSEARCHABLE_OCR_PREPROCESS=1 aplica o pipeline completo de pré-processamento.
    API legada: use run_ocr_on_image_with_confidence para obter confiança.
    """
    text, _ = _run_ocr_full(image_bytes, lang)
    return text


def run_ocr_on_image_with_confidence(
    image_bytes: bytes, lang: str | None = None
) -> tuple[str, float]:
    """
    Executa OCR com pipeline completo (OSD, pré-processamento, retry adaptativo).
    Retorna (texto, confiança_média 0–100; -1.0 se não disponível).
    """
    return _run_ocr_full(image_bytes, lang)


def render_page_to_image(page) -> bytes:
    """Renderiza uma página PyMuPDF (fitz) para PNG em bytes (DPI configurável via PDFSEARCHABLE_OCR_DPI)."""
    import fitz

    dpi = get_ocr_dpi()
    try:
        mat = page.get_pixmap(dpi=dpi, alpha=False)
    except TypeError:
        # fallback para versões antigas do PyMuPDF
        scale = dpi / 72.0
        mat = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return mat.tobytes("png")


# ── HTR ───────────────────────────────────────────────────────────────────────


def _htr_enabled() -> bool:
    """True se HTR estiver disponível e não desativado. Use PDFSEARCHABLE_HTR=0 para forçar Tesseract."""
    raw = (os.environ.get("PDFSEARCHABLE_HTR") or "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    try:
        from pdfsearchable.htr import htr_available

        return htr_available()
    except Exception:
        return False


# Limiar de confiança abaixo do qual tentar HTR como fallback (PDFSEARCHABLE_OCR_HTR_FALLBACK_THRESHOLD)
_HTR_FALLBACK_CONFIDENCE = 25.0


def _get_htr_fallback_threshold() -> float:
    raw = os.environ.get("PDFSEARCHABLE_OCR_HTR_FALLBACK_THRESHOLD", "").strip()
    if not raw:
        return _HTR_FALLBACK_CONFIDENCE
    try:
        return max(0.0, min(100.0, float(raw)))
    except ValueError:
        return _HTR_FALLBACK_CONFIDENCE


def ocr_page_from_image_bytes(
    image_bytes: bytes,
    cache_key: str,
    page_num: int,
    use_cache: bool = True,
    lang: str | None = None,
) -> tuple[str, float]:
    """
    Executa OCR em bytes de imagem (página já renderizada). Usado para OCR paralelo.
    cache_key: content_hash ou file_id (permite cache por conteúdo).
    lang: código de idioma do documento (ex: pt-BR, en, de, ru) para seleção do modelo HTR.
    Retorna (texto, confiança). Tenta cache; senão Tesseract; fallback HTR se vazio ou baixa confiança.
    """
    use_htr_first = _htr_enabled()
    if use_cache:
        cached = _get_cached(cache_key, page_num, use_htr=use_htr_first)
        if cached is not None:
            return cached
        cached = _get_cached(cache_key, page_num, use_htr=False)
        if cached is not None:
            return cached

    if use_htr_first:
        try:
            from pdfsearchable.htr import run_htr_on_image

            text = run_htr_on_image(image_bytes, lang=lang)
            conf = -1.0
            if use_cache and text.strip():
                _set_cache(cache_key, page_num, text, confidence=conf, use_htr=True)
            return text, conf
        except Exception as _htr_err:
            _log.debug("HTR (use_htr_first) falhou na página %d: %s — a usar Tesseract", page_num, _htr_err)

    if not ocr_available():
        return "", -1.0
    text, confidence = _run_ocr_full(image_bytes)
    # Fallback HTR: se Tesseract retornou vazio ou confiança muito baixa, tentar TrOCR (manuscrito)
    threshold = _get_htr_fallback_threshold()
    if _htr_enabled() and (not text.strip() or confidence < threshold):
        try:
            from pdfsearchable.htr import run_htr_on_image

            htr_text = run_htr_on_image(image_bytes, lang=lang)
            if htr_text.strip():
                text = htr_text
                confidence = -1.0
                if use_cache:
                    _set_cache(cache_key, page_num, text, confidence=confidence, use_htr=True)
                return text, confidence
        except Exception as _htr_err:
            _log.debug("HTR fallback falhou na página %d: %s — a usar resultado Tesseract", page_num, _htr_err)
    if use_cache and text.strip():
        _set_cache(cache_key, page_num, text, confidence=confidence, use_htr=False)
    return text, confidence


def ocr_page(
    doc,
    page_num: int,
    file_id: str,
    use_cache: bool = True,
    content_hash: str | None = None,
    lang: str | None = None,
) -> tuple[str, float]:
    """
    Extrai texto por OCR da página (1-based). Retorna (texto, confiança).
    Cache por content_hash (reutiliza entre arquivos com mesmo conteúdo) ou file_id.
    lang: código de idioma do documento para seleção do modelo HTR multilíngue.
    Com HTR ativo usa TrOCR para manuscrito; senão Tesseract; fallback HTR se vazio/baixa confiança.
    """
    cache_key = _cache_key(file_id, content_hash)
    use_htr = _htr_enabled()
    if use_cache:
        cached = _get_cached(cache_key, page_num, use_htr=use_htr)
        if cached is not None:
            return cached
        cached = _get_cached(cache_key, page_num, use_htr=False)
        if cached is not None:
            return cached

    if use_htr:
        try:
            from pdfsearchable.htr import run_htr_on_image

            page = doc[page_num - 1]
            img_bytes = render_page_to_image(page)
            text = run_htr_on_image(img_bytes, lang=lang)
            conf = -1.0
            if use_cache and text.strip():
                _set_cache(cache_key, page_num, text, confidence=conf, use_htr=True)
            return text, conf
        except Exception:
            use_htr = False

    if not ocr_available():
        return "", -1.0
    try:
        page = doc[page_num - 1]
        img_bytes = render_page_to_image(page)
        text, confidence = ocr_page_from_image_bytes(
            img_bytes, cache_key, page_num, use_cache=use_cache, lang=lang
        )
        return text, confidence
    except Exception:
        return "", -1.0
