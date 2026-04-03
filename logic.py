  
"""
logic.py — Plagiarism Detection Engine
=======================================

OCR ENGINE PRIORITY (best → fallback):
  1. EasyOCR      — Deep learning CRAFT text detector + CRNN recogniser.
                    Best for real-world photos, low-light, skewed, curved text.
                    Install: pip install easyocr
  2. PaddleOCR    — PaddlePaddle OCR, excellent for dense/printed documents.
                    Install: pip install paddlepaddle paddleocr
  3. TrOCR        — Microsoft Transformer-based OCR (HuggingFace).
                    Best for handwritten text.
                    Install: pip install transformers torch
  4. Tesseract 5  — Classic LSTM OCR engine. Always available baseline.
                    Install: apt install tesseract-ocr  (already required)

All engines feed through the same OpenCV preprocessing pipeline. The system
automatically picks the best available engine and fuses results when multiple
engines are present.

SIMILARITY ENGINE:
  - Primary:  SentenceTransformer (all-mpnet-base-v2) semantic embeddings
  - Fallback: TF-IDF word n-gram cosine similarity
  - Structural: 3-gram Jaccard on stemmed tokens
  - NO fuzzy floor — content must actually match, not just share English chars

INSTALL ALL OCR ENGINES:
  pip install easyocr paddlepaddle paddleocr transformers torch torchvision
  apt install tesseract-ocr tesseract-ocr-eng
"""

import os, re, gc, json, hashlib, math, string, difflib, tempfile
import numpy as np
from PIL import Image, ImageOps, ImageFilter, ImageEnhance

# ══════════════════════════════════════════════════════════════════════════════
# DEPENDENCY IMPORTS — all optional, graceful fallback
# ══════════════════════════════════════════════════════════════════════════════

# ── Tesseract (baseline, always try first) ────────────────────────────────────
try:
    import pytesseract
    pytesseract.get_tesseract_version()
    _HAS_TESS = True
except Exception:
    _HAS_TESS = False

# ── OpenCV (image preprocessing) ─────────────────────────────────────────────
try:
    import cv2 as _cv2
    _HAS_CV2 = True
except Exception:
    _HAS_CV2 = False

# ── EasyOCR (deep learning, best for real photos & handwriting) ───────────────
try:
    import easyocr as _easyocr
    _HAS_EASYOCR = True
except Exception:
    _HAS_EASYOCR = False

# ── PaddleOCR (excellent for printed documents) ───────────────────────────────
try:
    from paddleocr import PaddleOCR as _PaddleOCR
    _HAS_PADDLE = True
except Exception:
    _HAS_PADDLE = False

# ── TrOCR (Microsoft Transformer OCR — best for handwriting) ─────────────────
try:
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    import torch as _torch
    _HAS_TROCR = True
except Exception:
    _HAS_TROCR = False

# ── PyMuPDF — fast digital + scanned PDF rendering (preferred) ───────────────
try:
    import fitz as _fitz               # pip install pymupdf
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

# ── PDF conversion (fallback for scanned when fitz unavailable) ───────────────
try:
    from pdf2image import convert_from_path as _cfp
    _HAS_PDF2IMG = True
except Exception:
    _HAS_PDF2IMG = False

# ── PDF text extraction ───────────────────────────────────────────────────────
try:
    import pdfplumber as _pdfplumber
    _HAS_PDFPLUMBER = True
except Exception:
    _HAS_PDFPLUMBER = False

try:
    from pypdf import PdfReader as _PdfReader
    _HAS_PYPDF = True
except Exception:
    try:
        from PyPDF2 import PdfReader as _PdfReader
        _HAS_PYPDF = True
    except Exception:
        _HAS_PYPDF = False

# ── Word documents ────────────────────────────────────────────────────────────
try:
    from docx import Document as _DocxDoc
    _HAS_DOCX = True
except ImportError:
    _HAS_DOCX = False

# ── Similarity / ML ───────────────────────────────────────────────────────────
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity as _cos_sim
    _HAS_SKLEARN = True
except Exception:
    _HAS_SKLEARN = False

try:
    from sentence_transformers import SentenceTransformer
    _HAS_ST = True
except Exception:
    _HAS_ST = False

try:
    from sentence_transformers import CrossEncoder
    _HAS_CROSS = True
except Exception:
    _HAS_CROSS = False

try:
    import faiss as _faiss
    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False

try:
    import nltk
    from nltk.tokenize import sent_tokenize as _nltk_sent, word_tokenize as _nltk_word
    from nltk.stem import PorterStemmer
    from nltk.corpus import stopwords as _sw
    for _pkg in ['punkt', 'punkt_tab', 'stopwords']:
        try:
            nltk.data.find(f'tokenizers/{_pkg}' if 'punkt' in _pkg else f'corpora/{_pkg}')
        except LookupError:
            nltk.download(_pkg, quiet=True)
    _HAS_NLTK = True
except Exception:
    _HAS_NLTK = False

try:
    from rapidfuzz.fuzz import ratio as _rf_ratio
    _HAS_RF = True
except Exception:
    _HAS_RF = False

# ── Startup log ───────────────────────────────────────────────────────────────
_OCR_ENGINES = []
if _HAS_EASYOCR:  _OCR_ENGINES.append("EasyOCR")
if _HAS_PADDLE:   _OCR_ENGINES.append("PaddleOCR")
if _HAS_TROCR:    _OCR_ENGINES.append("TrOCR")
if _HAS_TESS:     _OCR_ENGINES.append("Tesseract5")

print(f"[logic] OCR engines: {_OCR_ENGINES or ['NONE — install tesseract!']}")
print(f"[logic] PDF:  fitz={_HAS_FITZ} pdfplumber={_HAS_PDFPLUMBER} pypdf={_HAS_PYPDF} pdf2img={_HAS_PDF2IMG}")
print(f"[logic] ML:   sklearn={_HAS_SKLEARN} ST={_HAS_ST} nltk={_HAS_NLTK} rf={_HAS_RF}")

# ── Model cache ───────────────────────────────────────────────────────────────
_st_model       = None
_cross_model    = None
_easyocr_reader = None
_paddle_ocr     = None
_trocr_proc     = None
_trocr_model    = None


# ══════════════════════════════════════════════════════════════════════════════
# TEXT UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9.,!?;: ]", " ", text)
    return text.strip()


def generate_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sent_tokenize(text: str) -> list:
    if _HAS_NLTK:
        try:
            return _nltk_sent(text) or [text]
        except Exception:
            pass
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()] or [text]


def _word_tokenize(text: str) -> list:
    if _HAS_NLTK:
        try:
            return _nltk_word(text)
        except Exception:
            pass
    return re.findall(r'\b[a-z]+\b', text.lower())


def _fuzzy_ratio(a: str, b: str) -> float:
    """Character-level similarity — supporting signal only, never a score floor."""
    if _HAS_RF:
        try:
            return _rf_ratio(a, b) / 100.0
        except Exception:
            pass
    return difflib.SequenceMatcher(None, a, b).ratio()


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE PREPROCESSING PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def _preprocess_image_cv2(pil_img: Image.Image) -> Image.Image:
    """
    Full OpenCV preprocessing:
      1. Grayscale conversion
      2. Upscale if < 1000px shortest side (OCR needs ~150+ DPI)
      3. Deskew via Hough line detection
      4. Unsharp masking for blurry scans
      5. Gamma correction for brightness normalisation
      6. CLAHE adaptive histogram equalisation
      7. NLM denoising
      8. Otsu binarisation (adaptive fallback for extreme contrast)
    """
    if not _HAS_CV2:
        return _preprocess_image_pil(pil_img)

    import cv2
    img_rgb = np.array(pil_img.convert("RGB"))
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)

    # 1. Upscale — OCR accuracy drops badly below ~150 DPI
    h, w = gray.shape
    if min(h, w) < 1000:
        scale = 1000 / min(h, w)
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)

    # 2. Deskew via Hough lines
    try:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, 100)
        if lines is not None:
            angles = []
            for line in lines[:50]:
                rho, theta = line[0]
                angle = (theta * 180 / np.pi) - 90
                if -45 <= angle <= 45:
                    angles.append(angle)
            if angles:
                med = float(np.median(angles))
                if abs(med) > 0.5:
                    ch, cw = gray.shape
                    M = cv2.getRotationMatrix2D((cw//2, ch//2), med, 1.0)
                    gray = cv2.warpAffine(gray, M, (cw, ch),
                                          flags=cv2.INTER_CUBIC,
                                          borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        pass

    # 3. Unsharp mask for blurry images
    try:
        if cv2.Laplacian(gray, cv2.CV_64F).var() < 100:
            blurred = cv2.GaussianBlur(gray, (0, 0), 3)
            gray = cv2.addWeighted(gray, 1.5, blurred, -0.5, 0)
    except Exception:
        pass

    # 4. Gamma correction
    try:
        mean_b = np.mean(gray)
        if mean_b > 0:
            gamma = math.log(128/255) / math.log(max(mean_b/255, 1e-7))
            gamma = max(0.3, min(gamma, 3.0))
            lut = np.array([((i/255.0)**gamma)*255 for i in range(256)], dtype=np.uint8)
            gray = cv2.LUT(gray, lut)
    except Exception:
        pass

    # 5. CLAHE
    try:
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    except Exception:
        pass

    # 6. Denoising
    try:
        gray = cv2.fastNlMeansDenoising(gray, h=10)
    except Exception:
        pass

    # 7. Otsu binarisation
    try:
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        wr = np.sum(otsu == 255) / otsu.size
        if wr < 0.1 or wr > 0.95:
            otsu = cv2.adaptiveThreshold(gray, 255,
                                         cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                         cv2.THRESH_BINARY, 11, 2)
        gray = otsu
    except Exception:
        pass

    return Image.fromarray(gray)


def _preprocess_image_pil(pil_img: Image.Image) -> Image.Image:
    """PIL-only fallback when OpenCV is unavailable."""
    img = pil_img.convert("L")
    img = ImageOps.autocontrast(img, cutoff=2)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    img = img.filter(ImageFilter.MedianFilter(3))
    w, h = img.size
    if min(w, h) < 1000:
        scale = 1000 / min(w, h)
        img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
    return img


def _preprocess_variants(pil_img: Image.Image) -> list:
    """
    Generate multiple preprocessing variants.
    Returns list of (PIL.Image, variant_name).
    """
    variants = []

    # Raw grayscale — fastest, good baseline
    variants.append((ImageOps.grayscale(pil_img), "gray_raw"))

    # Full OpenCV pipeline — best for clean document scans
    try:
        variants.append((_preprocess_image_cv2(pil_img), "cv2_full"))
    except Exception:
        pass

    # High contrast — good for faded/low-contrast originals
    try:
        img = pil_img.convert("L")
        img = ImageOps.autocontrast(img, cutoff=5)
        img = ImageEnhance.Contrast(img).enhance(2.5)
        variants.append((img, "high_contrast"))
    except Exception:
        pass

    # Inverted — for dark-background light-text images
    try:
        gray = ImageOps.grayscale(pil_img)
        if np.mean(np.array(gray)) < 100:
            variants.append((ImageOps.invert(gray), "inverted"))
    except Exception:
        pass

    return variants


# ══════════════════════════════════════════════════════════════════════════════
# OCR ENGINE IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════════

def _ocr_tesseract(pil_img: Image.Image) -> tuple:
    """
    Tesseract 5 LSTM engine.
    Tries multiple PSM modes. Returns (text, confidence 0-100).

    PSM guide:
      3  = Fully automatic page segmentation (default)
      4  = Single column of text of variable sizes
      6  = Uniform block of text (best for plain paragraphs)
      11 = Sparse text — finds text wherever it is
    """
    if not _HAS_TESS:
        return "", 0.0

    best_text, best_conf, best_wc = "", 0.0, 0

    for cfg in ["--psm 6 --oem 1", "--psm 3 --oem 1",
                "--psm 4 --oem 1", "--psm 11 --oem 1"]:
        try:
            data = pytesseract.image_to_data(
                pil_img, config=cfg,
                output_type=pytesseract.Output.DICT)
            confs = [c for c in data["conf"] if isinstance(c, (int, float)) and c >= 0]
            words = [str(w).strip() for w in data["text"] if str(w).strip()]
            text  = " ".join(words)
            avg_conf = float(np.mean(confs)) if confs else 0.0

            if len(words) > best_wc or (len(words) == best_wc and avg_conf > best_conf):
                best_wc   = len(words)
                best_conf = avg_conf
                best_text = text

            if best_wc > 50 and best_conf >= 60:
                break
        except Exception as e:
            print(f"[Tesseract] {cfg}: {e}")

    return best_text, best_conf


def _ocr_tesseract_fast(pil_img: Image.Image) -> tuple:
    """
    Single-pass Tesseract (PSM 6 only) — 4× faster than _ocr_tesseract.
    Used exclusively in the bulk pipeline where speed matters more than
    squeezing the last few words out of a difficult scan.
    Returns (text, confidence 0-100).
    """
    if not _HAS_TESS:
        return "", 0.0
    try:
        data = pytesseract.image_to_data(
            pil_img, config="--psm 6 --oem 1",
            output_type=pytesseract.Output.DICT)
        confs = [c for c in data["conf"] if isinstance(c, (int, float)) and c >= 0]
        words = [str(w).strip() for w in data["text"] if str(w).strip()]
        return " ".join(words), float(np.mean(confs)) if confs else 0.0
    except Exception as e:
        print(f"[Tess-fast] {e}")
        return "", 0.0


def _tess_cli_ocr_page(gray_array: np.ndarray, timeout: int = 20) -> str:
    """
    Run Tesseract as a CLI subprocess on a grayscale numpy image array.

    WHY subprocess (not pytesseract API):
      • Tesseract runs in its OWN OS process — OOM-kill in the subprocess
        cannot terminate Flask. Previously the Flask worker itself was being
        killed when a large scanned PDF exhausted RAM.
      • Hard timeout: no hunging process if a page is pathological.
      • Zero shared-memory risk between concurrent web workers.

    Writes a temp PNG → calls  tesseract <file> stdout  → reads stdout.
    Returns extracted text, or empty string on failure/timeout.
    """
    import subprocess, tempfile
    tmp_path = None
    try:
        # Write grayscale array to temp PNG (no compression for speed)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            tmp_path = f.name
        Image.fromarray(gray_array).save(tmp_path, format='PNG', optimize=False)

        result = subprocess.run(
            ['tesseract', tmp_path, 'stdout',
             '--psm', '6', '--oem', '1', '-l', 'eng'],
            capture_output=True, text=True,
            timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    except subprocess.TimeoutExpired:
        print(f"[Tess-CLI] Timeout ({timeout}s) — skipping page")
        return ""
    except FileNotFoundError:
        # tesseract binary not in PATH — degrade to pytesseract API
        try:
            return _ocr_tesseract_fast(Image.fromarray(gray_array))[0]
        except Exception:
            return ""
    except Exception as e:
        print(f"[Tess-CLI] {e}")
        return ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _ocr_easyocr(pil_img: Image.Image) -> tuple:
    """
    EasyOCR — CRAFT text detector + CRNN recogniser.

    Why it's better than Tesseract for photos:
    - Works on arbitrary orientations without manual deskew
    - Handles curved, perspective-distorted, shadowed text
    - Trained on real-world scene text, not just document scans
    - No need for binarisation preprocessing

    Returns (text, confidence 0-100).
    """
    if not _HAS_EASYOCR:
        return "", 0.0

    global _easyocr_reader
    try:
        if _easyocr_reader is None:
            print("[EasyOCR] Loading deep learning model (first use ~5s)…")
            _easyocr_reader = _easyocr.Reader(
                ['en'],
                gpu=False,          # set True if CUDA GPU available
                verbose=False,
                model_storage_directory=os.path.expanduser("~/.EasyOCR/model"),
            )
            print("[EasyOCR] Ready.")

        img_array = np.array(pil_img.convert("RGB"))
        results   = _easyocr_reader.readtext(img_array, detail=1, paragraph=False)

        if not results:
            return "", 0.0

        texts, confs = [], []
        for (bbox, text, conf) in results:
            if text.strip():
                texts.append(text.strip())
                confs.append(conf * 100)

        return " ".join(texts), float(np.mean(confs)) if confs else 0.0

    except Exception as e:
        print(f"[EasyOCR] {e}")
        return "", 0.0


def _ocr_paddleocr(pil_img: Image.Image) -> tuple:
    """
    PaddleOCR — state-of-the-art printed document OCR.

    Why it's useful:
    - Very accurate on structured, multi-column layouts
    - Built-in angle classifier handles rotated pages
    - Excellent on Chinese/multilingual documents too

    Returns (text, confidence 0-100).
    """
    if not _HAS_PADDLE:
        return "", 0.0

    global _paddle_ocr
    try:
        if _paddle_ocr is None:
            print("[PaddleOCR] Loading model (first use)…")
            _paddle_ocr = _PaddleOCR(
                use_angle_cls=True,
                lang='en',
                use_gpu=False,
                show_log=False,
            )
            print("[PaddleOCR] Ready.")

        img_array = np.array(pil_img.convert("RGB"))
        result    = _paddle_ocr.ocr(img_array, cls=True)

        if not result or not result[0]:
            return "", 0.0

        texts, confs = [], []
        for line in result[0]:
            if line and len(line) >= 2:
                tc = line[1]
                if tc and len(tc) >= 2 and str(tc[0]).strip():
                    texts.append(str(tc[0]).strip())
                    confs.append(float(tc[1]) * 100)

        return " ".join(texts), float(np.mean(confs)) if confs else 0.0

    except Exception as e:
        print(f"[PaddleOCR] {e}")
        return "", 0.0


def _ocr_trocr(pil_img: Image.Image) -> tuple:
    """
    TrOCR — Vision Transformer encoder + language model decoder.

    Why it's useful:
    - Purpose-built for handwritten text recognition
    - Processes image patches directly, no explicit text detection needed
    - Works on degraded, historical, or cursive handwriting

    Processes image as horizontal line strips (~64px each).
    Returns (text, confidence 0-100).
    """
    if not _HAS_TROCR:
        return "", 0.0

    global _trocr_proc, _trocr_model
    try:
        if _trocr_proc is None:
            print("[TrOCR] Loading model (first use, downloads ~500MB)…")
            # 'printed' for typed text, 'handwritten' for cursive/handwriting
            model_name   = "microsoft/trocr-base-printed"
            _trocr_proc  = TrOCRProcessor.from_pretrained(model_name)
            _trocr_model = VisionEncoderDecoderModel.from_pretrained(model_name)
            _trocr_model.eval()
            print("[TrOCR] Ready.")

        img_rgb = pil_img.convert("RGB")
        w, h    = img_rgb.size
        strip_h = 64
        texts   = []

        for i in range(max(1, h // strip_h)):
            y0 = i * strip_h
            y1 = min(y0 + strip_h, h)
            strip = img_rgb.crop((0, y0, w, y1))
            pv = _trocr_proc(images=strip, return_tensors="pt").pixel_values
            with _torch.no_grad():
                ids = _trocr_model.generate(pv)
            text = _trocr_proc.batch_decode(ids, skip_special_tokens=True)[0]
            if text.strip():
                texts.append(text.strip())

        full_text = " ".join(texts)
        conf = 75.0 if len(full_text.split()) > 5 else 30.0
        return full_text, conf

    except Exception as e:
        print(f"[TrOCR] {e}")
        return "", 0.0


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-ENGINE OCR FUSION
# ══════════════════════════════════════════════════════════════════════════════

def _score_ocr_result(text: str, conf: float) -> float:
    """
    Quality score combining: confidence (60%), word validity (30%), word count (10%).
    'valid' word = 3+ alphabetic characters — filters out OCR garbage like "l|".
    """
    if not text:
        return 0.0
    words = text.split()
    if not words:
        return 0.0
    valid = sum(1 for w in words if re.match(r'^[a-zA-Z]{3,}$', w))
    validity = valid / len(words)
    return (conf * 0.6) + (validity * 100 * 0.3) + (min(len(words), 200) / 200 * 10)


def ocr_image(pil_img: Image.Image, check_handwritten: bool = True,
              engine: str = "auto") -> tuple:
    """
    Main OCR entry point. Runs all available engines across preprocessing
    variants and returns the highest-scoring result.

    Args:
        pil_img:           PIL Image.
        check_handwritten: If True, try extra preprocessing variants.
        engine:            "auto" | "easyocr" | "paddle" | "trocr" | "tesseract"

    Returns:
        (text: str, confidence: float 0-100, engine_used: str)
    """
    if pil_img is None:
        return "", 0.0, "none"

    # Cap image size — very large images give no OCR benefit
    MAX_DIM = 3500
    w, h = pil_img.size
    if max(w, h) > MAX_DIM:
        scale   = MAX_DIM / max(w, h)
        pil_img = pil_img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)

    variants = (_preprocess_variants(pil_img) if check_handwritten
                else [(ImageOps.grayscale(pil_img), "gray_raw"),
                      (_preprocess_image_cv2(pil_img), "cv2_full")])

    candidates = []  # (text, conf, engine_label, quality_score)

    def _try(eng_name, fn, img, vname):
        try:
            text, conf = fn(img)
            if text and text.strip():
                score = _score_ocr_result(text, conf)
                candidates.append((text, conf, f"{eng_name}/{vname}", score))
                print(f"[OCR] {eng_name}/{vname}: {len(text.split())} words, "
                      f"conf={conf:.1f}, score={score:.1f}")
        except Exception as e:
            print(f"[OCR] {eng_name}/{vname} error: {e}")

    # EasyOCR — runs on colour image (no need for grayscale)
    if engine in ("auto", "easyocr") and _HAS_EASYOCR:
        _try("EasyOCR", _ocr_easyocr, pil_img, "color")
        if check_handwritten:
            try:
                cv2_gray = _preprocess_image_cv2(pil_img)
                rgb_from_gray = Image.merge("RGB", [cv2_gray]*3)
                _try("EasyOCR", _ocr_easyocr, rgb_from_gray, "cv2_preprocessed")
            except Exception:
                pass

    # PaddleOCR
    if engine in ("auto", "paddle") and _HAS_PADDLE:
        for img, vname in variants[:2]:
            _try("PaddleOCR", _ocr_paddleocr, img, vname)

    # TrOCR — only for handwritten content
    if engine in ("auto", "trocr") and _HAS_TROCR and check_handwritten:
        _try("TrOCR", _ocr_trocr, pil_img.convert("RGB"), "raw")

    # Tesseract — run on all preprocessing variants
    if engine in ("auto", "tesseract") and _HAS_TESS:
        for img, vname in variants:
            _try("Tesseract", _ocr_tesseract, img, vname)
            if candidates and max(c[3] for c in candidates) > 90:
                break  # excellent result already found

    if not candidates:
        print("[OCR] All engines produced no output.")
        return "", 0.0, "none"

    candidates.sort(key=lambda x: x[3], reverse=True)
    best_text, best_conf, best_engine, best_score = candidates[0]
    print(f"[OCR] Winner: {best_engine} | words={len(best_text.split())} "
          f"conf={best_conf:.1f} score={best_score:.1f}")

    return best_text, best_conf, best_engine


# ══════════════════════════════════════════════════════════════════════════════
# FILE TEXT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _extract_pdf_text(path: str, check_handwritten: bool = True) -> tuple:
    """
    Extract text from PDF.
    1. pdfplumber  → best for complex text-layer PDFs
    2. pypdf       → fallback text-layer
    3. Multi-engine OCR → for scanned/image-only PDFs
    Returns (text, ocr_confidence).
    """
    text = ""

    if _HAS_PDFPLUMBER:
        try:
            with _pdfplumber.open(path) as pdf:
                for page in pdf.pages[:15]:
                    t = page.extract_text() or ""
                    text += t + " "
        except Exception as e:
            print(f"[PDF] pdfplumber: {e}")

    if len(text.split()) < 10 and _HAS_PYPDF:
        text = ""
        try:
            reader = _PdfReader(path)
            for page in reader.pages[:15]:
                t = page.extract_text() or ""
                text += t + " "
        except Exception as e:
            print(f"[PDF] pypdf: {e}")

    if len(text.split()) >= 10:
        print(f"[PDF] Digital text layer: {len(text.split())} words")
        return clean_text(text), 100.0

    # Scanned PDF — Tesseract CLI subprocess (fast, memory-safe, no heavy models)
    can_render = _HAS_FITZ or _HAS_PDF2IMG
    if not can_render or not _HAS_TESS:
        print("[PDF] No renderer or Tesseract — returning empty for scanned PDF")
        return clean_text(text), 0.0

    # Memory guard before starting
    try:
        import psutil as _psutil
        free_mb = _psutil.virtual_memory().available / (1024 * 1024)
        if free_mb < 300:
            print(f"[PDF] Only {free_mb:.0f} MB free — skipping OCR")
            return "", 0.0
    except Exception:
        pass

    _MAX_PAGES = 10   # up to 10 pages for individual (more thorough than bulk's 2)
    _DPI       = 150  # good quality, less memory than 200 DPI

    print(f"[PDF] No text layer — Tesseract CLI subprocess on ≤{_MAX_PAGES} pages @ {_DPI} DPI…")
    page_texts, page_confs = [], []

    try:
        # Render all pages at once with fitz (preferred) or pdf2image
        if _HAS_FITZ:
            doc = _fitz.open(path)
            page_arrays = []
            for i, page in enumerate(doc):
                if i >= _MAX_PAGES:
                    break
                mat = _fitz.Matrix(_DPI / 72.0, _DPI / 72.0)
                pix = page.get_pixmap(matrix=mat, colorspace=_fitz.csGRAY)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w).copy()
                page_arrays.append(arr)
                pix = None
            doc.close()
        else:
            pil_imgs = _cfp(path, first_page=1, last_page=_MAX_PAGES, dpi=_DPI)
            page_arrays = [np.array(ImageOps.grayscale(img)) for img in pil_imgs]
            pil_imgs = None
        gc.collect()

        for idx, gray_arr in enumerate(page_arrays, start=1):
            try:
                import psutil as _psutil
                if _psutil.virtual_memory().available < 250 * 1024 * 1024:
                    print(f"[PDF] Low memory at page {idx} — stopping")
                    break
            except Exception:
                pass

            text_out = _tess_cli_ocr_page(gray_arr, timeout=20)
            gray_arr = None; gc.collect()

            if text_out:
                words = text_out.split()
                valid = sum(1 for w in words if re.match(r'^[a-zA-Z]{3,}$', w))
                est_conf = (valid / len(words) * 100) if words else 0.0
                page_texts.append(text_out)
                page_confs.append(est_conf)
                print(f"[PDF] Page {idx}: {len(words)} words (conf~{est_conf:.0f}%)")

        page_arrays = None; gc.collect()

    except Exception as e:
        print(f"[PDF] render/OCR error: {e}")

    combined = " ".join(page_texts)
    avg_conf  = float(np.mean(page_confs)) if page_confs else 0.0
    print(f"[PDF] OCR done: {len(combined.split())} words, avg conf={avg_conf:.1f}%")
    return clean_text(combined), avg_conf


def _extract_pdf_text_bulk(path: str) -> tuple:
    """
    Production-grade bulk PDF extractor. Never OOM-kills Flask.

    Pipeline (fastest → fallback):
      ① PyMuPDF (fitz)     — digital text, instant, zero OCR
      ② pdfplumber / pypdf — digital text fallback
      ③ Scanned fallback   — fitz page render → Tesseract CLI subprocess
                             (subprocess OOM cannot kill Flask)
                             Max 2 pages, DPI 120, early-exit at 150 words.

    Returns (text, ocr_confidence).
    """
    text = ""

    # ── ① PyMuPDF — fastest digital extraction (preferred) ───────────────────
    if _HAS_FITZ:
        try:
            doc = _fitz.open(path)
            parts = []
            for page in doc:
                t = page.get_text("text") or ""
                if t.strip():
                    parts.append(t.strip())
            doc.close()
            text = " ".join(parts)
            if len(text.split()) >= 10:
                print(f"[PDF-bulk] fitz digital: {len(text.split())} words")
                return clean_text(text), 100.0
            text = ""   # fitz found nothing — try OCR path
        except Exception as e:
            print(f"[PDF-bulk] fitz read: {e}")
            text = ""

    # ── ② pdfplumber → pypdf fallback ───────────────────────────────────────
    if not text:
        if _HAS_PDFPLUMBER:
            try:
                with _pdfplumber.open(path) as pdf:
                    for page in pdf.pages[:15]:
                        t = page.extract_text() or ""
                        text += t + " "
            except Exception as e:
                print(f"[PDF-bulk] pdfplumber: {e}")

        if len(text.split()) < 10 and _HAS_PYPDF:
            text = ""
            try:
                reader = _PdfReader(path)
                for page in reader.pages[:15]:
                    t = page.extract_text() or ""
                    text += t + " "
            except Exception as e:
                print(f"[PDF-bulk] pypdf: {e}")

        if len(text.split()) >= 10:
            print(f"[PDF-bulk] Digital: {len(text.split())} words")
            return clean_text(text), 100.0

    # ── ③ Scanned — fitz/pdf2image render + Tesseract CLI subprocess ─────────
    # Tesseract runs in a child process: OOM there cannot kill Flask.
    can_render = _HAS_FITZ or _HAS_PDF2IMG
    if not can_render or not _HAS_TESS:
        print("[PDF-bulk] Scanned PDF: no renderer or Tesseract — returning empty")
        return "", 0.0

    # Upfront memory guard
    try:
        import psutil as _psutil
        free_mb = _psutil.virtual_memory().available / (1024 * 1024)
        if free_mb < 300:
            print(f"[PDF-bulk] Only {free_mb:.0f} MB free — skipping OCR to protect stability")
            return "", 0.0
    except Exception:
        pass

    _MAX_PAGES       = 2    # 2 pages is ample for student assignments
    _DPI             = 120  # 120 DPI → ~40% less memory than 200 DPI; Tesseract still accurate
    _EARLY_EXIT_WDS  = 150  # stop early once we have enough comparison text

    print(f"[PDF-bulk] Scanned — rendering ≤{_MAX_PAGES} pages @ {_DPI} DPI via subprocess OCR…")
    page_texts, page_confs = [], []

    try:
        # Render with fitz (no external dependency) or fall back to pdf2image
        if _HAS_FITZ:
            doc = _fitz.open(path)
            page_arrays = []
            for i, page in enumerate(doc):
                if i >= _MAX_PAGES:
                    break
                mat = _fitz.Matrix(_DPI / 72.0, _DPI / 72.0)
                pix = page.get_pixmap(matrix=mat, colorspace=_fitz.csGRAY)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w).copy()
                page_arrays.append(arr)
                pix = None
            doc.close()
        else:
            # Single pdf2image call — one poppler startup for all pages
            pil_imgs = _cfp(path, first_page=1, last_page=_MAX_PAGES, dpi=_DPI)
            page_arrays = [np.array(ImageOps.grayscale(img)) for img in pil_imgs]
            pil_imgs = None
        gc.collect()

        for idx, gray_arr in enumerate(page_arrays, start=1):
            # Per-page memory guard
            try:
                import psutil as _psutil
                if _psutil.virtual_memory().available < 250 * 1024 * 1024:
                    print(f"[PDF-bulk] Low memory at page {idx} — stopping")
                    gray_arr = None; gc.collect()
                    break
            except Exception:
                pass

            # Subprocess OCR — memory-isolated, timeout-protected
            text_out = _tess_cli_ocr_page(gray_arr, timeout=20)
            gray_arr = None; gc.collect()

            if text_out:
                wds   = text_out.split()
                valid = sum(1 for w in wds if re.match(r'^[a-zA-Z]{3,}$', w))
                est_conf = (valid / len(wds) * 100) if wds else 0.0
                page_texts.append(text_out)
                page_confs.append(est_conf)
                total = sum(len(t.split()) for t in page_texts)
                print(f"[PDF-bulk] Page {idx}: {len(wds)} words "
                      f"(total={total}, conf~{est_conf:.0f}%)")
                if total >= _EARLY_EXIT_WDS:
                    print(f"[PDF-bulk] Early exit after {idx} page(s)")
                    break

        page_arrays = None; gc.collect()

    except Exception as e:
        print(f"[PDF-bulk] render error: {e}")

    combined = " ".join(page_texts)
    avg_conf  = float(np.mean(page_confs)) if page_confs else 0.0
    print(f"[PDF-bulk] Done: {len(combined.split())} words, conf~{avg_conf:.0f}%")
    return clean_text(combined), avg_conf


def _extract_image_text(path: str, check_handwritten: bool = True) -> tuple:
    """Extract text from a single image via Tesseract CLI subprocess (fast, memory-safe)."""
    try:
        pil_img = Image.open(path)
        pil_img.load()
        # Cap to 2000px — sufficient for OCR, prevents OOM on large images
        w, h = pil_img.size
        if max(w, h) > 2000:
            scale = 2000 / max(w, h)
            pil_img = pil_img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        gray_arr = np.array(pil_img.convert("L"))
        del pil_img; gc.collect()
    except Exception as e:
        print(f"[Image] Cannot open {path}: {e}")
        return "", 0.0

    text = _tess_cli_ocr_page(gray_arr, timeout=25)
    gray_arr = None; gc.collect()

    words = text.split() if text else []
    valid = sum(1 for w in words if re.match(r'^[a-zA-Z]{3,}$', w))
    conf  = (valid / len(words) * 100) if words else 0.0

    print(f"[Image] {os.path.basename(path)}: {len(words)} words, conf~{conf:.1f}%")
    return clean_text(text), conf


def extract_text(file_path: str, check_handwritten: bool = True) -> tuple:
    """
    Universal text extractor. Handles txt, pdf, images, docx.
    Returns (text, binary_content, file_hash, ocr_confidence). Never raises.
    """
    if not os.path.exists(file_path):
        print(f"[extract_text] Not found: {file_path}")
        return "", None, None, 0.0

    try:
        with open(file_path, "rb") as f:
            content = f.read()
        file_hash = generate_hash(content)
    except Exception as e:
        print(f"[extract_text] Read error: {e}")
        return "", None, None, 0.0

    name = file_path.lower()
    text, conf = "", 100.0

    try:
        if name.endswith(".txt"):
            text = clean_text(open(file_path, encoding="utf-8", errors="ignore").read())

        elif name.endswith(".pdf"):
            text, conf = _extract_pdf_text(file_path, check_handwritten)

        elif name.endswith((".png", ".jpg", ".jpeg", ".tiff", ".tif",
                            ".gif", ".webp", ".bmp")):
            text, conf = _extract_image_text(file_path, check_handwritten)

        elif name.endswith(".docx"):
            if _HAS_DOCX:
                doc  = _DocxDoc(file_path)
                paras = [p.text for p in doc.paragraphs if p.text.strip()]
                text  = clean_text(" ".join(paras))
            else:
                print("[extract_text] python-docx not installed")

        elif name.endswith(".doc"):
            try:
                import subprocess
                r = subprocess.run(["antiword", file_path],
                                   capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    text = clean_text(r.stdout)
            except FileNotFoundError:
                print("[extract_text] antiword not installed for .doc")

        else:
            text = clean_text(open(file_path, encoding="utf-8", errors="ignore").read())

    except Exception as e:
        print(f"[extract_text] {file_path}: {e}")

    print(f"[extract_text] '{os.path.basename(file_path)}' → "
          f"{len(text.split())} words, conf={round(conf,1)}%")
    return text, content, file_hash, conf



def extract_text_bulk(file_path: str) -> tuple:
    """
    Bulk-safe text extractor — never loads heavy OCR engines (EasyOCR/PaddleOCR).
    Uses Tesseract only for scanned PDFs, at low DPI with a page cap.
    Returns (text, binary_content, file_hash, ocr_confidence). Never raises.
    """
    if not os.path.exists(file_path):
        print(f"[extract_text_bulk] Not found: {file_path}")
        return "", None, None, 0.0

    try:
        with open(file_path, "rb") as f:
            content = f.read()
        file_hash = generate_hash(content)
    except Exception as e:
        print(f"[extract_text_bulk] Read error: {e}")
        return "", None, None, 0.0

    name = file_path.lower()
    text, conf = "", 100.0

    try:
        if name.endswith(".txt"):
            text = clean_text(open(file_path, encoding="utf-8", errors="ignore").read())

        elif name.endswith(".pdf"):
            # Use the lightweight bulk extractor (Tesseract-only for scanned)
            text, conf = _extract_pdf_text_bulk(file_path)

        elif name.endswith(".docx"):
            if _HAS_DOCX:
                doc   = _DocxDoc(file_path)
                paras = [p.text for p in doc.paragraphs if p.text.strip()]
                text  = clean_text(" ".join(paras))
            else:
                print("[extract_text_bulk] python-docx not installed")

        elif name.endswith(".doc"):
            try:
                import subprocess
                r = subprocess.run(["antiword", file_path],
                                   capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    text = clean_text(r.stdout)
            except FileNotFoundError:
                print("[extract_text_bulk] antiword not installed for .doc")

        elif name.endswith((".png", ".jpg", ".jpeg", ".tiff", ".tif",
                            ".gif", ".webp", ".bmp")):
            # Images: Tesseract CLI subprocess (memory-isolated)
            try:
                import psutil as _psutil
                if _psutil.virtual_memory().available < 300 * 1024 * 1024:
                    print("[extract_text_bulk] Low memory — skipping image OCR")
                else:
                    pil_img = Image.open(file_path)
                    # Cap large images at 2000px to keep memory reasonable
                    w, h = pil_img.size
                    if max(w, h) > 2000:
                        scale = 2000 / max(w, h)
                        pil_img = pil_img.resize(
                            (int(w * scale), int(h * scale)), Image.LANCZOS)
                    gray_arr = np.array(ImageOps.grayscale(pil_img))
                    pil_img = None; gc.collect()
                    text_out = _tess_cli_ocr_page(gray_arr, timeout=20)
                    gray_arr = None; gc.collect()
                    text = clean_text(text_out)
                    conf = 75.0 if text else 0.0
            except ImportError:
                # psutil not installed — run without guard
                try:
                    pil_img = Image.open(file_path)
                    gray_arr = np.array(ImageOps.grayscale(pil_img))
                    pil_img = None; gc.collect()
                    text_out = _tess_cli_ocr_page(gray_arr, timeout=20)
                    gray_arr = None; gc.collect()
                    text = clean_text(text_out)
                    conf = 75.0 if text else 0.0
                except Exception as e:
                    print(f"[extract_text_bulk] Image OCR: {e}")
            except Exception as e:
                print(f"[extract_text_bulk] Image OCR error: {e}")

        else:
            text = clean_text(open(file_path, encoding="utf-8", errors="ignore").read())

    except Exception as e:
        print(f"[extract_text_bulk] {file_path}: {e}")

    print(f"[extract_text_bulk] '{os.path.basename(file_path)}' → "
          f"{len(text.split())} words, conf={round(conf,1)}%")
    return text, content, file_hash, conf


# ══════════════════════════════════════════════════════════════════════════════
# EXTERNAL SOURCE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_AI_PATTERNS = [
    r"it is worth noting", r"delve into", r"rapidly evolving",
    r"it is important to note", r"in conclusion,", r"furthermore\b",
    r"moreover\b", r"additionally\b", r"leverage\b", r"paradigm shift",
    r"cutting.edge", r"in the realm of", r"moving forward",
    r"synergy\b", r"game.changer", r"deep dive", r"takeaway\b",
]
_WIKI_PATTERNS = [r"\[\d+\]", r"\bis a \w+ (that|which)\b",
                  r"\d{4}[–\-]\d{4}", r"\balso known as\b"]
_WEB_PATTERNS  = [r"\bstudies have shown\b", r"\bresearch suggests\b",
                  r"\bevidence indicates\b"]


def detect_external_sources(text: str) -> dict:
    if not text or len(text) < 30:
        return {"overall_external_score": 0, "sources": []}

    words = text.split(); wc = max(len(words), 1)
    sources = []

    ai_hits  = sum(1 for p in _AI_PATTERNS if re.search(p, text, re.I))
    ai_score = min(ai_hits / max(len(_AI_PATTERNS), 1), 1.0)
    if ai_score > 0.1:
        sources.append({"type": "ai_generated",
                        "confidence": round(ai_score*100, 1),
                        "detail": f"{ai_hits} AI phrase(s) detected"})

    wiki_hits  = sum(1 for p in _WIKI_PATTERNS if re.search(p, text, re.I))
    wiki_score = min(wiki_hits / max(len(_WIKI_PATTERNS), 1), 1.0)
    if wiki_score > 0.1:
        sources.append({"type": "wikipedia_encyclopedic",
                        "confidence": round(wiki_score*100, 1),
                        "detail": f"{wiki_hits} encyclopedic pattern(s)"})

    web_hits  = sum(1 for p in _WEB_PATTERNS if re.search(p, text, re.I))
    passive   = len(re.findall(r'\b(is|are|was|were)\s+\w+ed\b', text, re.I))
    web_score = min((web_hits/max(len(_WEB_PATTERNS),1))*0.6 +
                    min(passive/(wc/20),1.0)*0.4, 1.0)
    if web_score > 0.15:
        sources.append({"type": "web_copy",
                        "confidence": round(web_score*100, 1),
                        "detail": f"{web_hits} academic phrase(s), {passive} passive"})

    overall = max(ai_score, wiki_score, web_score) * 100
    return {"overall_external_score": round(overall, 1), "sources": sources}


# ══════════════════════════════════════════════════════════════════════════════
# SIMILARITY ENGINES
# ══════════════════════════════════════════════════════════════════════════════

def _tfidf_similarity(text1: str, text2: str) -> float:
    """Word-level TF-IDF (unigrams + bigrams). Falls back to Jaccard."""
    if not text1 or not text2:
        return 0.0
    if not _HAS_SKLEARN:
        w1 = set(re.findall(r'\b[a-z]{3,}\b', text1))
        w2 = set(re.findall(r'\b[a-z]{3,}\b', text2))
        return len(w1 & w2) / len(w1 | w2) if w1 | w2 else 0.0
    try:
        vec   = TfidfVectorizer(analyzer='word', ngram_range=(1, 2),
                                max_features=20000, sublinear_tf=True, min_df=1)
        tfidf = vec.fit_transform([text1, text2])
        return float(_cos_sim(tfidf[0], tfidf[1])[0][0])
    except Exception as e:
        print(f"[tfidf] {e}")
        w1 = set(re.findall(r'\b[a-z]{3,}\b', text1))
        w2 = set(re.findall(r'\b[a-z]{3,}\b', text2))
        return len(w1 & w2) / len(w1 | w2) if w1 | w2 else 0.0


def _semantic_similarity(text1: str, text2: str,
                          precomputed_embeddings: dict = None) -> float:
    """SentenceTransformer semantic similarity. Falls back to TF-IDF."""
    if not _HAS_ST:
        return _tfidf_similarity(text1, text2)

    if precomputed_embeddings is not None:
        e1 = precomputed_embeddings.get(clean_text(text1))
        e2 = precomputed_embeddings.get(clean_text(text2))
        if e1 is not None and e2 is not None:
            return float(np.dot(e1, e2))
        # Cache provided but chunk not in cache -> TF-IDF (no live model inference)
        return _tfidf_similarity(text1, text2)

    try:
        global _st_model
        if _st_model is None:
            print("[ST] Loading SentenceTransformer…")
            _st_model = SentenceTransformer("all-mpnet-base-v2")
        emb = _st_model.encode([text1, text2], convert_to_numpy=True).astype("float32")
        if _HAS_FAISS:
            _faiss.normalize_L2(emb)
        else:
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            emb   = emb / np.maximum(norms, 1e-10)
        return float(np.dot(emb[0], emb[1]))
    except Exception as e:
        print(f"[ST] {e} → TF-IDF fallback")
        return _tfidf_similarity(text1, text2)


def _structural_similarity(text1: str, text2: str) -> float:
    """3-gram Jaccard on stemmed stopword-filtered tokens."""
    try:
        def stem_tokens(t):
            words = re.findall(r'\b[a-z]+\b', t.lower())
            if _HAS_NLTK:
                try:
                    stemmer = PorterStemmer()
                    sw = set(_sw.words('english'))
                    words = [stemmer.stem(w) for w in words if w not in sw]
                except Exception:
                    pass
            return words

        tok1, tok2 = stem_tokens(text1), stem_tokens(text2)
        if len(tok1) < 5 or len(tok2) < 5:
            w1, w2 = set(tok1), set(tok2)
            return len(w1 & w2) / len(w1 | w2) if w1 | w2 else 0.0

        n   = 3
        ng1 = set(tuple(tok1[i:i+n]) for i in range(len(tok1)-n+1))
        ng2 = set(tuple(tok2[i:i+n]) for i in range(len(tok2)-n+1))
        if not ng1 or not ng2:
            return 0.0
        inter = ng1 & ng2
        return round(max(len(inter)/len(ng1|ng2), len(inter)/len(ng1)*0.8), 4)
    except Exception as e:
        print(f"[structural] {e}"); return 0.0


def _stylometric_similarity(text1: str, text2: str) -> float:
    """Writing-style vector cosine. Weight kept ≤ 0.08 to avoid false positives."""
    try:
        def feats(t):
            words = re.findall(r'\b[a-z]+\b', t.lower())
            sents = [s.strip() for s in re.split(r'[.!?]+', t) if s.strip()]
            sl    = [len(s.split()) for s in sents] if sents else [0]
            vocab = set(words)
            punct = sum(1 for c in t if c in string.punctuation)
            return np.array([
                np.mean(sl), np.std(sl),
                len(vocab)/max(len(words),1),
                np.mean([len(w) for w in words]) if words else 0,
                punct/max(len(t),1),
                len(words)/max(len(sents),1),
            ], dtype=float)
        v1, v2 = feats(text1), feats(text2)
        n1, n2  = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 == 0 or n2 == 0: return 0.0
        return round(float(np.dot(v1,v2)/(n1*n2)), 4)
    except Exception as e:
        print(f"[stylometric] {e}"); return 0.0


def split_into_chunks(text: str, chunk_size: int = 200, overlap: int = 50) -> list:
    words = text.split()
    if len(words) <= chunk_size:
        return [text]
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i:i+chunk_size]))
        i += chunk_size - overlap
    return chunks[:30]


def get_dynamic_weights(ocr_confidence: float) -> tuple:
    """(w_semantic, w_structural, w_stylometric). Stylometric capped at 0.08."""
    if ocr_confidence is None or ocr_confidence >= 95:
        return 0.55, 0.37, 0.08
    elif ocr_confidence >= 70:
        return 0.65, 0.27, 0.08
    else:
        return 0.78, 0.14, 0.08


def compute_fused_score(text1: str, text2: str,
                         ocr_conf: float = 100,
                         precomputed_embeddings: dict = None) -> tuple:
    """Fuse semantic + structural + stylometric. No fuzzy floor."""
    sem = _semantic_similarity(text1, text2, precomputed_embeddings)
    stt = _structural_similarity(text1, text2)
    sty = _stylometric_similarity(text1, text2)
    w_sem, w_stt, w_sty = get_dynamic_weights(ocr_conf)
    fused = sem*w_sem + stt*w_stt + sty*w_sty
    if ocr_conf < 95:
        fuzz  = _fuzzy_ratio(text1, text2)
        fused = 0.75*fused + 0.25*fuzz  # light blend to handle OCR noise
    return round(fused, 4), sem, stt, sty


# ══════════════════════════════════════════════════════════════════════════════
# MODEL WARMUP
# ══════════════════════════════════════════════════════════════════════════════

def warmup_models():
    """
    Pre-load ML similarity models at startup.

    EasyOCR and PaddleOCR are intentionally NOT warmed up here:
      - They are never used in the bulk plagiarism pipeline
        (bulk uses Tesseract CLI subprocess instead)
      - Each consumes 1.5–2 GB RAM; loading both at startup on a low-RAM
        server is the primary cause of the OOM/SIGTERM that was crashing jobs
      - They load lazily on first individual-file check if actually needed
    """
    global _st_model, _cross_model

    if _HAS_ST and _st_model is None:
        try:
            print("[warmup] SentenceTransformer…")
            _st_model = SentenceTransformer("all-mpnet-base-v2")
            print("[warmup] SentenceTransformer ready.")
        except Exception as e:
            print(f"[warmup] ST: {e}")

    if _HAS_CROSS and _cross_model is None:
        try:
            print("[warmup] CrossEncoder…")
            _cross_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            print("[warmup] CrossEncoder ready.")
        except Exception as e:
            print(f"[warmup] CrossEncoder: {e}")

    print("[warmup] Done. EasyOCR/PaddleOCR will load lazily if needed.")


def _cross_encoder_score(text1: str, text2: str) -> float:
    if not _HAS_CROSS: return 0.0
    global _cross_model
    try:
        if _cross_model is None:
            _cross_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        return float(1 / (1 + np.exp(-_cross_model.predict([(text1, text2)])[0])))
    except Exception as e:
        print(f"[CrossEncoder] {e}"); return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# PEER COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

def peer_comparison(text: str, other_texts: list,
                    ocr_confidence: float = 100,
                    precomputed_embeddings: dict = None,
                    skip_cross_encoder: bool = False) -> dict:
    best = {
        "peer_score": 0.0, "matched_author": None,
        "matched_submission_id": None, "matched_filename": None,
        "semantic_score": 0.0, "structural_score": 0.0,
        "stylometric_score": 0.0, "top_matched_passages": [],
        "all_matches": [],
    }

    if not other_texts or not text:
        return best

    base_chunks  = split_into_chunks(text)
    all_matches  = []
    curr_cleaned = clean_text(text)
    curr_emb     = (precomputed_embeddings or {}).get(curr_cleaned)

    for other in other_texts:
        ot = other.get("text", "")
        if not ot or len(ot.split()) < 10:
            continue

        if curr_emb is not None and precomputed_embeddings:
            oc = clean_text(ot)
            oe = precomputed_embeddings.get(oc)
            if oe is not None and float(np.dot(curr_emb, oe)) < 0.45:
                continue

        other_chunks = split_into_chunks(ot)
        chunk_scores = []
        best_local   = 0
        best_pair    = ("", "")
        best_sem = best_stt = best_sty = 0.0

        for c1 in base_chunks:
            if len(c1.split()) < 20: continue
            max_cs, best_c2 = 0, ""
            _s = _t = _y = 0.0

            for c2 in other_chunks:
                if len(c2.split()) < 20: continue
                fused, sem, stt, sty = compute_fused_score(
                    c1, c2, ocr_confidence, precomputed_embeddings)
                if _HAS_CROSS and fused > 0.60 and not skip_cross_encoder:
                    fused = 0.85*fused + 0.15*_cross_encoder_score(c1[:512], c2[:512])
                if fused > max_cs:
                    max_cs, best_c2, _s, _t, _y = fused, c2, sem, stt, sty

            if max_cs >= 0.45:
                chunk_scores.append(max_cs)
            if max_cs > best_local:
                best_local = max_cs
                best_pair  = (c1, best_c2)
                best_sem, best_stt, best_sty = _s, _t, _y

        if not chunk_scores:
            continue

        chunk_scores.sort(reverse=True)
        avg_top = sum(chunk_scores[:3]) / len(chunk_scores[:3])
        fused_final = round(0.75*avg_top + 0.25*max(chunk_scores), 4)

        if fused_final < 0.40:
            continue

        passages = []
        for s1 in _sent_tokenize(best_pair[0])[:10]:
            for s2 in _sent_tokenize(best_pair[1])[:10]:
                r = _fuzzy_ratio(s1, s2)
                if r > 0.75:
                    passages.append({
                        "text_a": s1, "text_b": s2,
                        "score": round(r, 4),
                        "match_type": "exact" if r > 0.92 else "paraphrase",
                    })
        passages.sort(key=lambda x: x["score"], reverse=True)

        all_matches.append({
            "author": other.get("author_username", "Unknown"),
            "submission_id": other.get("submission_id"),
            "filename": other.get("filename", ""),
            "original_filename": other.get("original_filename", ""),
            "fused_score": round(fused_final*100, 1),
            "top_passages": passages[:5],
        })

        if fused_final > best["peer_score"]:
            best.update({
                "peer_score": fused_final,
                "matched_author": other.get("author_username"),
                "matched_submission_id": other.get("submission_id"),
                "matched_filename": other.get("filename", ""),
                "semantic_score": best_sem,
                "structural_score": best_stt,
                "stylometric_score": best_sty,
                "top_matched_passages": passages[:10],
            })

    all_matches.sort(key=lambda x: x["fused_score"], reverse=True)
    best["all_matches"] = all_matches
    return best


# ══════════════════════════════════════════════════════════════════════════════
# VERDICT + ANALYSIS TEXT
# ══════════════════════════════════════════════════════════════════════════════

def decide_verdict(peer_score: float, external_score: float, threshold: int) -> str:
    if threshold >= 100: return "accepted"
    if external_score >= threshold: return "rejected"
    if peer_score * 100 >= threshold: return "rejected"
    return "accepted"


def _risk_label(pct: float) -> str:
    if pct < 20: return "Clean"
    if pct < 40: return "Low"
    if pct < 65: return "Medium"
    return "High"


def generate_analysis_text(verdict, peer_score, external_score,
                            peer_details, external_details,
                            ocr_confidence, threshold,
                            is_image_submission=False) -> str:
    overall = round(max(peer_score*100, external_score), 1)
    risk    = _risk_label(overall)
    lines   = [f"Overall Risk: {risk} ({overall}%)",
               f"Verdict: {verdict.upper()} (threshold: {threshold}%)"]
    if is_image_submission:
        lines.append(f"OCR Confidence: {round(ocr_confidence,1)}%")
    if peer_score > 0:
        lines.append(
            f"Peer Similarity: {round(peer_score*100,1)}% "
            f"(semantic {round(peer_details.get('semantic_score',0)*100,1)}%, "
            f"structural {round(peer_details.get('structural_score',0)*100,1)}%, "
            f"stylometric {round(peer_details.get('stylometric_score',0)*100,1)}%)")
        if peer_details.get("matched_author"):
            lines.append(f"Closest match: '{peer_details['matched_author']}'")
        passages = peer_details.get("top_matched_passages", [])
        if passages:
            b = passages[0]
            lines.append(f"Strongest passage ({b['match_type']}, {round(b['score']*100)}%):")
            lines.append(f"  Sub:   \"{b['text_a'][:100]}\"")
            lines.append(f"  Match: \"{b['text_b'][:100]}\"")
    else:
        lines.append("Peer Similarity: None found.")
    if external_score > 0:
        lines.append(f"External Score: {round(external_score,1)}%")
        for s in external_details.get("sources", []):
            lines.append(f"  [{s['type']}] {s['confidence']}% — {s['detail']}")
    if verdict == "manual_review":
        lines.append("Action: Manual review required.")
    elif verdict == "rejected":
        lines.append(f"Action: Rejected — exceeded {threshold}% threshold.")
    else:
        lines.append("Action: Accept.")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════

def run_plagiarism_check(file_path: str, other_submissions: list,
                         threshold: int = 40,
                         check_handwritten: bool = True,
                         existing_hash: str = None,
                         precomputed_embeddings: dict = None,
                         skip_cross_encoder: bool = False) -> dict:
    """Full plagiarism pipeline. Never raises."""
    is_image = file_path.lower().endswith(
        (".png",".jpg",".jpeg",".tiff",".tif",".gif",".webp",".bmp"))
    is_pdf   = file_path.lower().endswith(".pdf")

    text, content, file_hash, ocr_confidence = extract_text(
        file_path, check_handwritten=check_handwritten)

    result = {
        "text": text, "file_hash": file_hash, "ocr_confidence": ocr_confidence,
        "verdict": "accepted", "peer_score": 0.0, "external_score": 0.0,
        "peer_details": {},
        "external_details": {"overall_external_score": 0, "sources": []},
        "analysis_text": "", "is_exact_duplicate": False, "reason": "Original Work",
    }

    ocr_was_used = is_image or (is_pdf and ocr_confidence < 99.0)

    if not text or len(text.split()) < 3:
        result["verdict"]       = "manual_review"
        result["reason"]        = "Could not extract any readable text"
        result["analysis_text"] = "No text extracted. Manual review required."
        return result

    ext       = detect_external_sources(text)
    ext_score = ext["overall_external_score"]
    result["external_details"] = ext
    result["external_score"]   = ext_score

    peer       = peer_comparison(text, other_submissions, ocr_confidence,
                                 precomputed_embeddings=precomputed_embeddings,
                                 skip_cross_encoder=skip_cross_encoder)
    peer_score = peer["peer_score"]
    result["peer_score"]   = peer_score
    result["peer_details"] = peer

    verdict = decide_verdict(peer_score, ext_score, threshold)
    result["verdict"] = verdict

    if verdict == "rejected":
        if ext_score >= threshold:
            result["reason"] = f"External source detected ({round(ext_score,1)}%)"
        else:
            author = peer.get("matched_author", "another student")
            result["reason"] = f"High similarity with {author} ({round(peer_score*100,1)}%)"
    else:
        result["reason"] = "Original Work"

    result["analysis_text"] = generate_analysis_text(
        verdict, peer_score, ext_score, peer, ext,
        ocr_confidence, threshold, ocr_was_used)
    return result




def _bulk_peer_comparison(text, other_submissions, precomputed_embeddings=None):
    best = {
        'peer_score': 0.0, 'matched_author': None,
        'matched_submission_id': None, 'matched_filename': None,
        'semantic_score': 0.0, 'structural_score': 0.0,
        'stylometric_score': 0.0, 'top_matched_passages': [],
        'all_matches': [],
    }
    if not other_submissions or not text:
        return best
    curr_cl  = clean_text(text)
    curr_emb = (precomputed_embeddings or {}).get(curr_cl)
    all_matches = []
    for other in other_submissions:
        ot = other.get('text', '')
        if not ot or len(ot.split()) < 10:
            continue
        oc = clean_text(ot)
        if curr_emb is not None and precomputed_embeddings is not None:
            oe = precomputed_embeddings.get(oc)
            sem = float(np.dot(curr_emb, oe)) if oe is not None else _tfidf_similarity(curr_cl, oc)
        else:
            sem = _tfidf_similarity(curr_cl, oc)
        if sem < 0.38:
            continue
        stt   = _structural_similarity(text[:3000], ot[:3000])
        sty   = _stylometric_similarity(text[:1500], ot[:1500])
        fused = round(sem * 0.60 + stt * 0.32 + sty * 0.08, 4)
        if fused < 0.38:
            continue
        all_matches.append({
            'author': other.get('author_username', 'Unknown'),
            'submission_id': other.get('submission_id'),
            'filename': other.get('filename', ''),
            'original_filename': other.get('original_filename', ''),
            'fused_score': round(fused * 100, 1),
            'top_passages': [],
        })
        if fused > best['peer_score']:
            best.update({
                'peer_score': fused,
                'matched_author': other.get('author_username'),
                'matched_submission_id': other.get('submission_id'),
                'matched_filename': other.get('filename', ''),
                'semantic_score': sem, 'structural_score': stt,
                'stylometric_score': sty, 'top_matched_passages': [],
            })
    all_matches.sort(key=lambda x: x['fused_score'], reverse=True)
    best['all_matches'] = all_matches
    return best

def bulk_run_plagiarism_check(file_path: str, other_submissions: list,
                              threshold: int = 40,
                              check_handwritten: bool = True,
                              precomputed_embeddings: dict = None) -> dict:
    """Bulk optimised entry point — disables CrossEncoder for speed."""
    return run_plagiarism_check(
        file_path, other_submissions,
        threshold=threshold, check_handwritten=check_handwritten,
        precomputed_embeddings=precomputed_embeddings,
        skip_cross_encoder=True)


def bulk_run_plagiarism_check_preextracted(text: str, file_hash: str,
                                            ocr_confidence: float,
                                            other_submissions: list,
                                            threshold: int = 40,
                                            precomputed_embeddings: dict = None,
                                            filename: str = "") -> dict:
    """
    Bulk-optimised entry point that accepts PRE-EXTRACTED text.
    Eliminates redundant file I/O and OCR during batch processing.
    Uses the identical scoring pipeline as individual checks for consistency.
    """
    is_image = filename.lower().endswith(
        (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".gif", ".webp", ".bmp"))
    is_pdf = filename.lower().endswith(".pdf")

    result = {
        "text": text, "file_hash": file_hash, "ocr_confidence": ocr_confidence,
        "verdict": "accepted", "peer_score": 0.0, "external_score": 0.0,
        "peer_details": {},
        "external_details": {"overall_external_score": 0, "sources": []},
        "analysis_text": "", "is_exact_duplicate": False, "reason": "Original Work",
    }

    ocr_was_used = is_image or (is_pdf and (ocr_confidence or 100) < 99.0)

    if not text or len(text.split()) < 3:
        result["verdict"] = "manual_review"
        result["reason"] = "Could not extract any readable text"
        result["analysis_text"] = "No text extracted. Manual review required."
        return result

    # Same pipeline as run_plagiarism_check — identical scoring
    ext = detect_external_sources(text)
    ext_score = ext["overall_external_score"]
    result["external_details"] = ext
    result["external_score"] = ext_score

    peer = _bulk_peer_comparison(text, other_submissions,
                                   precomputed_embeddings=precomputed_embeddings)
    peer_score = peer["peer_score"]
    result["peer_score"] = peer_score
    result["peer_details"] = peer

    verdict = decide_verdict(peer_score, ext_score, threshold)
    result["verdict"] = verdict

    if verdict == "rejected":
        if ext_score >= threshold:
            result["reason"] = f"External source detected ({round(ext_score, 1)}%)"
        else:
            author = peer.get("matched_author", "another student")
            result["reason"] = f"High similarity with {author} ({round(peer_score * 100, 1)}%)"
    else:
        result["reason"] = "Original Work"

    result["analysis_text"] = generate_analysis_text(
        verdict, peer_score, ext_score, peer, ext,
        ocr_confidence or 100, threshold, ocr_was_used)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# LEGACY COMPAT
# ══════════════════════════════════════════════════════════════════════════════

faiss_index    = None
stored_chunks  = []
chunk_to_doc   = []
document_texts = []


def hybrid_similarity(text1: str, text2: str) -> float:
    sem = _semantic_similarity(clean_text(text1), clean_text(text2))
    fuz = _fuzzy_ratio(clean_text(text1)[:2000], clean_text(text2)[:2000])
    return round(0.80*sem + 0.20*fuz, 4)


def build_index(all_documents: list):
    global document_texts
    document_texts = all_documents
    print(f"[logic] build_index: {len(all_documents)} docs")


def search(query: str, top_k: int = 5) -> list:
    return []
