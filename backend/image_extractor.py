import fitz  # PyMuPDF
import hashlib
import io
from PIL import Image, ImageStat
from typing import List, Dict, Any, Tuple

# Hard cap on images extracted per paper - a safety net against runaway
# vision-API cost if the heuristic below still over-triggers on an unusual
# real-world PDF layout.
MAX_IMAGES_PER_PAPER = 20

# Formats every major browser can render in an <img> tag. PDFs commonly
# embed images as JPEG2000 (jpx/jp2) or other formats PyMuPDF happily
# extracts verbatim but that render as a broken image in the browser -
# those get transcoded to PNG before saving.
WEB_SAFE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

def _ensure_web_safe(image_bytes: bytes, ext: str) -> Tuple[bytes, str]:
    if ext.lower() in WEB_SAFE_EXTENSIONS:
        return image_bytes, ext
    try:
        img = Image.open(io.BytesIO(image_bytes))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue(), "png"
    except Exception as e:
        print(f"Failed to transcode non-web-safe image format '{ext}' to PNG: {e}")
        return image_bytes, ext

def _find_diagram_clusters(page) -> List["fitz.Rect"]:
    """
    Real exam PDFs are full of vector drawing primitives that are NOT
    diagrams: page border frames, table/answer-line rulings, tick boxes.
    A naive ">=3 drawings on the page" check (and a bounding box spanning
    ALL of them) locks onto the page border and rasterizes the entire page
    as an "image" - on a 40-page real paper that meant ~1 vision call pair
    per page instead of ~1 per actual diagram.

    This filters out border/frame-sized rects and hairline rulings first,
    then clusters the remaining candidate drawings by spatial proximity,
    and only keeps clusters that look diagram-shaped: several distinct
    primitives, occupying a plausible (not tiny, not near-full-page) area.
    """
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    drawings = page.get_drawings()
    if not drawings:
        return []

    candidates = []
    for d in drawings:
        r = d.get("rect")
        if not r or r.width <= 0 or r.height <= 0:
            continue
        # Drop page border/frame rects (span most of the page in either axis).
        if r.width >= 0.85 * page_rect.width and r.height >= 0.85 * page_rect.height:
            continue
        # Drop hairline rulings (a table border or answer-writing line is a
        # rect that's essentially 1-dimensional).
        if r.width < 3 or r.height < 3:
            continue
        candidates.append(r)

    if len(candidates) < 5:
        return []

    # Merge overlapping/nearby rects into clusters (simple greedy union).
    clusters: List["fitz.Rect"] = []
    counts: List[int] = []
    pad = 15
    for r in candidates:
        expanded = fitz.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
        merged = False
        for i, c in enumerate(clusters):
            if expanded.intersects(c):
                clusters[i] = c | r
                counts[i] += 1
                merged = True
                break
        if not merged:
            clusters.append(fitz.Rect(r))
            counts.append(1)

    results = []
    for cluster_rect, count in zip(clusters, counts):
        area_frac = (cluster_rect.width * cluster_rect.height) / page_area if page_area else 0
        # A real diagram cluster: multiple distinct primitives, and a
        # plausible figure-sized footprint - not a single stray tick box,
        # not a near-full-page region (which is almost certainly page
        # furniture we failed to filter, not one diagram).
        if count >= 5 and 0.01 <= area_frac <= 0.6:
            results.append(fitz.Rect(
                cluster_rect.x0 - 5, cluster_rect.y0 - 5,
                cluster_rect.x1 + 5, cluster_rect.y1 + 5
            ))

    return results

def is_image_valid_and_non_blank(image_bytes: bytes, min_dimension: int = 40) -> Tuple[bool, str]:
    """
    Sanity check for image:
    1. Valid image format
    2. Minimum width/height
    3. Non-uniform single color (detects empty / blank / transparent crops)
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
        
        if width < min_dimension or height < min_dimension:
            return False, f"Image dimensions too small ({width}x{height}px)"
            
        # Convert to RGB to measure pixel variance
        rgb_img = img.convert("RGB")
        stat = ImageStat.Stat(rgb_img)
        # Standard deviation across color channels
        avg_std_dev = sum(stat.stddev) / len(stat.stddev)
        
        # If stddev is almost 0, image is a solid flat color / blank
        if avg_std_dev < 1.5:
            return False, f"Image appears blank or uniform flat color (stddev: {avg_std_dev:.2f})"
            
        return True, "Valid"
    except Exception as e:
        return False, f"Image decoding error: {str(e)}"

def extract_all_visuals_from_pdf(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Dual-mode image extraction:
    1. PyMuPDF embedded raster image extraction (get_images())
    2. Vector drawings fallback: Detects vector drawings (paths, curves, rects)
       and rasterizes the page bounding box at 300 DPI (3x resolution).

    Returns each image's raw bytes under "_bytes" rather than writing
    anything to local disk - this function is sync/CPU-bound (PyMuPDF), so
    it stays that way, but the actual object-storage upload is an async
    network call; the caller (ingestion.py, which does run inside an event
    loop) uploads each "_bytes" payload to S3/MinIO via storage.py and
    replaces it with a real "url" before anything here is persisted. "key"
    is the content-addressed object key (checksum-based, matching the old
    local filename) the caller uploads under.
    """
    extracted_images = []
    seen_hashes = set()
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Path 1: Embedded Raster Images
        image_list = page.get_images(full=True)
        for img_info in image_list:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                ext = base_image.get("ext", "png")
                image_bytes, ext = _ensure_web_safe(image_bytes, ext)

                # Check validity
                valid, reason = is_image_valid_and_non_blank(image_bytes)
                checksum = hashlib.sha256(image_bytes).hexdigest()
                
                if checksum in seen_hashes:
                    continue
                seen_hashes.add(checksum)

                extracted_images.append({
                    "page": page_num + 1,
                    "type": "raster",
                    "ext": ext,
                    "checksum": checksum,
                    "key": f"{checksum}.{ext}",
                    "_bytes": image_bytes,
                    "is_valid": valid,
                    "validation_reason": reason,
                    "needs_review": not valid,
                    "bbox": [0, 0, base_image.get("width", 0), base_image.get("height", 0)]
                })
            except Exception as e:
                print(f"Error extracting raster image on page {page_num+1}: {e}")

        # Path 2: Vector Path Drawings Fallback
        # If page contains vector drawings (graphs, diagrams, electric circuits)
        try:
            for clip_rect in _find_diagram_clusters(page):
                pix = page.get_pixmap(dpi=300, clip=clip_rect)
                vec_bytes = pix.tobytes("png")

                valid, reason = is_image_valid_and_non_blank(vec_bytes)
                checksum = hashlib.sha256(vec_bytes).hexdigest()

                if checksum in seen_hashes:
                    continue
                seen_hashes.add(checksum)

                extracted_images.append({
                    "page": page_num + 1,
                    "type": "vector_rasterized",
                    "ext": "png",
                    "checksum": checksum,
                    "key": f"{checksum}.png",
                    "_bytes": vec_bytes,
                    "is_valid": valid,
                    "validation_reason": reason,
                    "needs_review": not valid,
                    "bbox": [clip_rect.x0, clip_rect.y0, clip_rect.x1, clip_rect.y1]
                })

                if len(extracted_images) >= MAX_IMAGES_PER_PAPER:
                    return extracted_images
        except Exception as e:
            print(f"Error extracting vector drawings on page {page_num+1}: {e}")
            
    return extracted_images
