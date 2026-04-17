"""Testes unitários para pré-processamento OCR — incluindo pipeline histórico."""

import os
import pytest
from unittest.mock import patch

import numpy as np
from PIL import Image

from pdfsearchable.ocr import (
    _binarize_otsu,
    _binarize_sauvola,
    _clahe,
    _morphological_clean,
    _detect_historical_page,
    _is_blank_page,
    _preprocess_image_for_ocr,
    _historical_mode,
    _remove_scan_border,
)


def _make_gray_image(w=100, h=100, fill=200):
    """Cria imagem grayscale com valor uniforme."""
    arr = np.full((h, w), fill, dtype=np.uint8)
    return Image.fromarray(arr, mode="L")


def _make_text_image(w=200, h=100):
    """Cria imagem simulando texto preto sobre fundo branco."""
    arr = np.full((h, w), 240, dtype=np.uint8)  # fundo claro
    # Simular 3 linhas de texto
    for y_start in [20, 45, 70]:
        arr[y_start : y_start + 8, 30:170] = 30  # texto escuro
    return Image.fromarray(arr, mode="L")


def _make_historical_image(w=200, h=200):
    """Cria imagem simulando documento histórico (papel amarelado, contraste baixo)."""
    rng = np.random.RandomState(42)
    # Papel amarelado com variação (simula envelhecimento irregular)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = 210  # R
    arr[:, :, 1] = 190  # G
    arr[:, :, 2] = 140  # B (significativamente menor)
    # Variação no fundo (manchas de envelhecimento)
    noise = rng.randint(-30, 10, (h, w), dtype=np.int16)
    for c in range(3):
        arr[:, :, c] = np.clip(arr[:, :, c].astype(np.int16) + noise, 0, 255).astype(np.uint8)
    # Texto desbotado (muitas linhas, contraste variável)
    for y_start in range(20, 180, 15):
        intensity = rng.randint(60, 120)
        arr[y_start : y_start + 8, 15:185, :] = [intensity, intensity - 10, intensity - 20]
    # Manchas de tinta (bleed-through)
    for _ in range(30):
        y, x = rng.randint(0, h), rng.randint(0, w)
        sz = rng.randint(2, 6)
        y1, y2 = max(0, y - sz), min(h, y + sz)
        x1, x2 = max(0, x - sz), min(w, x + sz)
        arr[y1:y2, x1:x2, :] = [120, 110, 80]
    return Image.fromarray(arr, mode="RGB")


def _make_modern_image(w=200, h=200):
    """Cria imagem simulando documento moderno (papel branco, alto contraste)."""
    arr = np.full((h, w, 3), 250, dtype=np.uint8)  # papel branco
    # Texto preto nítido
    for y_start in [30, 70, 110]:
        arr[y_start : y_start + 8, 20:180, :] = [10, 10, 10]
    return Image.fromarray(arr, mode="RGB")


# ── Sauvola ──────────────────────────────────────────────────────────────────


class TestBinarizeSauvola:
    def test_returns_image(self):
        img = _make_text_image()
        result = _binarize_sauvola(img)
        assert isinstance(result, Image.Image)
        assert result.mode == "L"

    def test_output_is_binary(self):
        img = _make_text_image()
        result = _binarize_sauvola(img)
        arr = np.array(result)
        unique = np.unique(arr)
        assert all(v in (0, 255) for v in unique)

    def test_preserves_dimensions(self):
        img = _make_text_image(w=150, h=80)
        result = _binarize_sauvola(img)
        assert result.size == img.size

    def test_handles_uniform_image(self):
        img = _make_gray_image(fill=128)
        result = _binarize_sauvola(img)
        assert isinstance(result, Image.Image)

    def test_handles_rgb_input(self):
        arr = np.full((50, 50, 3), 200, dtype=np.uint8)
        img = Image.fromarray(arr, mode="RGB")
        result = _binarize_sauvola(img)
        assert result.mode == "L"

    def test_window_size_parameter(self):
        img = _make_text_image()
        r1 = _binarize_sauvola(img, window_size=15)
        r2 = _binarize_sauvola(img, window_size=51)
        assert isinstance(r1, Image.Image)
        assert isinstance(r2, Image.Image)

    def test_k_parameter(self):
        img = _make_text_image()
        r1 = _binarize_sauvola(img, k=0.1)
        r2 = _binarize_sauvola(img, k=0.5)
        # k menor → mais texto detectado (mais pixels pretos)
        black1 = np.sum(np.array(r1) == 0)
        black2 = np.sum(np.array(r2) == 0)
        assert black1 >= black2  # k menor = mais texto

    def test_small_image(self):
        img = _make_gray_image(w=5, h=5, fill=100)
        result = _binarize_sauvola(img)
        assert isinstance(result, Image.Image)


# ── CLAHE ────────────────────────────────────────────────────────────────────


class TestCLAHE:
    def test_returns_image(self):
        img = _make_text_image()
        result = _clahe(img)
        assert isinstance(result, Image.Image)
        assert result.mode == "L"

    def test_preserves_dimensions(self):
        img = _make_text_image(w=120, h=80)
        result = _clahe(img)
        assert result.size == img.size

    def test_improves_contrast(self):
        # Imagem com múltiplos níveis de cinza (contraste moderado)
        rng = np.random.RandomState(42)
        arr = rng.randint(80, 180, (100, 100), dtype=np.uint8)
        arr[30:40, 20:80] = 60  # texto mais escuro
        img = Image.fromarray(arr, mode="L")
        result = _clahe(img, clip_limit=3.0)
        result_arr = np.array(result)
        # CLAHE deve produzir imagem com range >= original
        orig_range = int(np.max(arr)) - int(np.min(arr))
        new_range = int(np.max(result_arr)) - int(np.min(result_arr))
        assert new_range >= orig_range

    def test_handles_uniform_image(self):
        img = _make_gray_image(fill=100)
        result = _clahe(img)
        assert isinstance(result, Image.Image)

    def test_clip_limit_parameter(self):
        img = _make_text_image()
        r1 = _clahe(img, clip_limit=1.0)
        r2 = _clahe(img, clip_limit=4.0)
        assert isinstance(r1, Image.Image)
        assert isinstance(r2, Image.Image)


# ── Morphological Clean ──────────────────────────────────────────────────────


class TestMorphologicalClean:
    def test_returns_image(self):
        img = _make_text_image()
        result = _morphological_clean(img)
        assert isinstance(result, Image.Image)

    def test_preserves_dimensions(self):
        img = _make_text_image(w=120, h=80)
        result = _morphological_clean(img)
        assert result.size == img.size

    def test_removes_noise(self):
        # Imagem com texto + pontos de ruído
        arr = np.full((100, 100), 255, dtype=np.uint8)
        arr[40:50, 20:80] = 0  # linha de texto (grande)
        # Pontos isolados de ruído
        arr[10, 10] = 0
        arr[80, 80] = 0
        arr[20, 90] = 0
        img = Image.fromarray(arr, mode="L")
        result = _morphological_clean(img)
        result_arr = np.array(result)
        # Texto principal deve ser preservado
        assert (
            np.sum(result_arr[40:50, 20:80] == 0) > 0 or np.sum(result_arr[40:50, 20:80] < 128) > 0
        )

    def test_handles_blank_image(self):
        img = _make_gray_image(fill=255)
        result = _morphological_clean(img)
        assert isinstance(result, Image.Image)


# ── Historical Detection ─────────────────────────────────────────────────────


class TestDetectHistoricalPage:
    def test_detects_historical(self):
        img = _make_historical_image()
        assert _detect_historical_page(img) is True

    def test_rejects_modern(self):
        img = _make_modern_image()
        assert _detect_historical_page(img) is False

    def test_handles_grayscale(self):
        # Grayscale deveria não crashar mas pode não detectar (sem info de cor)
        img = _make_gray_image(fill=180)
        result = _detect_historical_page(img)
        assert isinstance(result, bool)

    def test_handles_small_image(self):
        arr = np.full((10, 10, 3), [200, 180, 120], dtype=np.uint8)
        img = Image.fromarray(arr, mode="RGB")
        result = _detect_historical_page(img)
        assert isinstance(result, bool)


# ── Historical Mode Env Var ──────────────────────────────────────────────────


class TestHistoricalMode:
    def test_default_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            assert _historical_mode() == "off"

    def test_on(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "on"}):
            assert _historical_mode() == "on"

    def test_true(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "1"}):
            assert _historical_mode() == "on"

    def test_auto(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "auto"}):
            assert _historical_mode() == "auto"

    def test_invalid_defaults_off(self):
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "invalid"}):
            assert _historical_mode() == "off"


# ── Preprocess Pipeline ──────────────────────────────────────────────────────


class TestPreprocessPipeline:
    def test_standard_pipeline(self):
        img = _make_text_image()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PDFSEARCHABLE_OCR_HISTORICAL", None)
            result = _preprocess_image_for_ocr(img)
            assert isinstance(result, Image.Image)

    def test_historical_forced(self):
        img = _make_text_image()
        result = _preprocess_image_for_ocr(img, force_historical=True)
        assert isinstance(result, Image.Image)

    def test_historical_env_on(self):
        img = _make_text_image()
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "on"}):
            result = _preprocess_image_for_ocr(img)
            assert isinstance(result, Image.Image)

    def test_historical_auto_with_historical_image(self):
        img = _make_historical_image()
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "auto"}):
            result = _preprocess_image_for_ocr(img)
            assert isinstance(result, Image.Image)

    def test_historical_auto_with_modern_image(self):
        img = _make_modern_image()
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "auto"}):
            result = _preprocess_image_for_ocr(img)
            assert isinstance(result, Image.Image)

    def test_force_historical_false_overrides_env(self):
        img = _make_text_image()
        with patch.dict(os.environ, {"PDFSEARCHABLE_OCR_HISTORICAL": "on"}):
            result = _preprocess_image_for_ocr(img, force_historical=False)
            assert isinstance(result, Image.Image)

    def test_non_image_passthrough(self):
        result = _preprocess_image_for_ocr("not an image")
        assert result == "not an image"


# ── Otsu vs Sauvola comparison ───────────────────────────────────────────────


class TestOtsuVsSauvola:
    def test_both_produce_binary(self):
        img = _make_text_image()
        otsu = _binarize_otsu(img)
        sauvola = _binarize_sauvola(img)
        otsu_arr = np.array(otsu)
        sauvola_arr = np.array(sauvola)
        assert set(np.unique(otsu_arr)).issubset({0, 255})
        assert set(np.unique(sauvola_arr)).issubset({0, 255})

    def test_sauvola_handles_uneven_background(self):
        """Sauvola deve lidar melhor com fundos desiguais."""
        arr = np.zeros((100, 200), dtype=np.uint8)
        # Gradiente de fundo (simula iluminação desigual)
        for x in range(200):
            arr[:, x] = int(150 + 100 * (x / 200.0))  # 150-250
        # Texto (sempre escuro relativo ao fundo local)
        arr[30:40, 20:60] = arr[30:40, 20:60] - 80
        arr[30:40, 120:180] = arr[30:40, 120:180] - 80
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr, mode="L")

        sauvola = _binarize_sauvola(img, window_size=25, k=0.2)
        sauvola_arr = np.array(sauvola)
        # Sauvola deve detectar texto em ambos os lados (escuro relativo ao fundo local)
        left_text = np.sum(sauvola_arr[30:40, 20:60] == 0)
        right_text = np.sum(sauvola_arr[30:40, 120:180] == 0)
        # Ambos devem ter pixels pretos (texto detectado)
        assert left_text > 0
        assert right_text > 0
