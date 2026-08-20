import fitz  # PyMuPDF
import os
import hashlib
import io
from PIL import Image, ImageStat
from typing import List, Dict, Any, Tuple

MEDIA_DIR = os.path.join(os.path.dirname(__file__), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

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
                
                # Check validity
                valid, reason = is_image_valid_and_non_blank(image_bytes)
                checksum = hashlib.sha256(image_bytes).hexdigest()
                
                if checksum in seen_hashes:
                    continue
                seen_hashes.add(checksum)
                
                # Save locally
                file_name = f"{checksum}.{ext}"
                file_path = os.path.join(MEDIA_DIR, file_name)
                with open(file_path, "wb") as f:
                    f.write(image_bytes)
                    
                extracted_images.append({
                    "page": page_num + 1,
                    "type": "raster",
                    "ext": ext,
                    "checksum": checksum,
                    "url": f"/api/media/{file_name}",
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
            drawings = page.get_drawings()
            if drawings and len(drawings) >= 3:
                # Calculate bounding box encompassing the vector paths
                rects = [d["rect"] for d in drawings if d.get("rect")]
                if rects:
                    min_x0 = min(r.x0 for r in rects)
                    min_y0 = min(r.y0 for r in rects)
                    max_x1 = max(r.x1 for r in rects)
                    max_y1 = max(r.y1 for r in rects)
                    
                    clip_rect = fitz.Rect(min_x0 - 5, min_y0 - 5, max_x1 + 5, max_y1 + 5)
                    # Rasterize at 300 DPI (approx 4.16x zoom)
                    pix = page.get_pixmap(dpi=300, clip=clip_rect)
                    vec_bytes = pix.tobytes("png")
                    
                    valid, reason = is_image_valid_and_non_blank(vec_bytes)
                    checksum = hashlib.sha256(vec_bytes).hexdigest()
                    
                    if checksum not in seen_hashes:
                        seen_hashes.add(checksum)
                        file_name = f"{checksum}.png"
                        file_path = os.path.join(MEDIA_DIR, file_name)
                        with open(file_path, "wb") as f:
                            f.write(vec_bytes)
                            
                        extracted_images.append({
                            "page": page_num + 1,
                            "type": "vector_rasterized",
                            "ext": "png",
                            "checksum": checksum,
                            "url": f"/api/media/{file_name}",
                            "is_valid": valid,
                            "validation_reason": reason,
                            "needs_review": not valid,
                            "bbox": [min_x0, min_y0, max_x1, max_y1]
                        })
        except Exception as e:
            print(f"Error extracting vector drawings on page {page_num+1}: {e}")
            
    return extracted_images
