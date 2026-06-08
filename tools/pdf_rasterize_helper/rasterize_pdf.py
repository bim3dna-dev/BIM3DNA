#!/usr/bin/env python
from __future__ import print_function

import argparse
import json
import os
import sys
import tempfile
import time


def _print_json(data):
    sys.stdout.write(json.dumps(data, indent=2))
    sys.stdout.write("\n")


def _log_progress(message):
    sys.stderr.write(str(message) + "\n")
    sys.stderr.flush()


def _file_size_mb(path):
    try:
        return os.path.getsize(path) / (1024.0 * 1024.0)
    except Exception:
        return None


def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _convert_image(image, color_mode):
    if color_mode == "grayscale":
        return image.convert("L")
    if color_mode == "bw":
        return image.convert("L").point(lambda px: 255 if px > 180 else 0, mode="1")
    return image.convert("RGB")


def _dependency_versions():
    versions = {
        "pypdfium2": "-",
        "pillow": "-",
        "img2pdf": "-",
    }
    import pypdfium2 as pdfium
    import PIL

    versions["pypdfium2"] = getattr(pdfium, "__version__", "available")
    versions["pillow"] = getattr(PIL, "__version__", "available")
    try:
        import img2pdf

        versions["img2pdf"] = getattr(img2pdf, "__version__", "available")
    except Exception:
        versions["img2pdf"] = "missing"
    return versions


def self_test():
    result = {
        "ok": True,
        "python": sys.executable,
        "pypdfium2": "-",
        "pillow": "-",
        "img2pdf": "-",
    }
    try:
        versions = _dependency_versions()
        result.update(versions)
    except Exception as ex:
        return {
            "ok": False,
            "error": "Missing dependency: {0}".format(ex),
            "python": sys.executable,
            "install_hint": "{0} -m pip install pypdfium2 Pillow img2pdf".format(
                sys.executable
            ),
        }
    if result.get("img2pdf") == "missing":
        result["ok"] = False
        result["error"] = "Missing dependency: img2pdf"
        result["install_hint"] = "{0} -m pip install img2pdf".format(sys.executable)
    return result


def _prepare_page_image(image, color_mode, image_codec, threshold, indexed_colors):
    from PIL import Image

    if image_codec == "bw":
        return image.convert("L").point(
            lambda px: 255 if px >= int(threshold) else 0, mode="1"
        )
    if image_codec == "png-indexed":
        return image.convert("RGB").convert(
            "P", palette=Image.ADAPTIVE, colors=int(indexed_colors)
        )
    if color_mode == "grayscale":
        return image.convert("L")
    return image.convert("RGB")


def _save_temp_page_image(image, image_codec, quality, png_compression_level):
    if image_codec == "jpeg":
        suffix = ".jpg"
        save_format = "JPEG"
        save_kwargs = {
            "quality": int(quality),
            "optimize": True,
            "progressive": False,
        }
    elif image_codec == "png":
        suffix = ".png"
        save_format = "PNG"
        save_kwargs = {
            "optimize": True,
            "compress_level": int(png_compression_level),
        }
    elif image_codec == "png-indexed":
        suffix = ".png"
        save_format = "PNG"
        save_kwargs = {
            "optimize": True,
            "compress_level": int(png_compression_level),
        }
    elif image_codec == "bw":
        suffix = ".tif"
        save_format = "TIFF"
        save_kwargs = {"compression": "group4"}
    else:
        raise Exception("Unsupported image codec: {0}".format(image_codec))
    fd, temp_path = tempfile.mkstemp(prefix="bim3dna_flatten_", suffix=suffix)
    os.close(fd)
    image.save(temp_path, save_format, **save_kwargs)
    return temp_path


def _write_pdf_with_img2pdf(temp_paths, output_pdf, dpi):
    import img2pdf

    layout_fun = img2pdf.get_fixed_dpi_layout_fun((int(dpi), int(dpi)))
    with open(output_pdf, "wb") as fp:
        fp.write(img2pdf.convert(temp_paths, layout_fun=layout_fun))


def _write_pdf_with_pillow_fallback(temp_paths, output_pdf, dpi, quality):
    from PIL import Image

    images = []
    try:
        for temp_path in temp_paths:
            loaded = Image.open(temp_path)
            images.append(loaded.copy())
            loaded.close()
        save_images = []
        for img in images:
            if img.mode not in ("RGB", "L", "1"):
                save_images.append(img.convert("RGB"))
            else:
                save_images.append(img)
        first = save_images[0]
        first.save(
            output_pdf,
            "PDF",
            resolution=int(dpi),
            save_all=True,
            append_images=save_images[1:],
            quality=int(quality),
        )
    finally:
        for img in images:
            try:
                img.close()
            except Exception:
                pass


def rasterize_pdf(
    input_pdf,
    output_pdf,
    dpi,
    quality,
    color_mode,
    image_codec,
    threshold,
    png_compression_level,
    indexed_colors,
):
    # This helper is intentionally CPython-only. pyRevit/IronPython should call it
    # through subprocess or a future bundled helper executable.
    try:
        import pypdfium2 as pdfium
        from PIL import Image
        try:
            import img2pdf
        except Exception:
            img2pdf = None
    except Exception as ex:
        return {
            "ok": False,
            "error": (
                "Missing helper dependencies. Install pypdfium2, Pillow, and img2pdf "
                "in the configured CPython runtime. Details: {0}"
            ).format(ex),
            "install_hint": "{0} -m pip install pypdfium2 Pillow img2pdf".format(
                sys.executable
            ),
        }
    if img2pdf is None and image_codec != "jpeg":
        return {
            "ok": False,
            "error": "img2pdf is required for PNG/Indexed/Black-White raster PDF modes.",
            "install_hint": "{0} -m pip install img2pdf".format(sys.executable),
        }

    if not os.path.exists(input_pdf):
        return {"ok": False, "error": "Input PDF not found: {0}".format(input_pdf)}
    output_folder = os.path.dirname(os.path.abspath(output_pdf))
    if output_folder and not os.path.isdir(output_folder):
        os.makedirs(output_folder)

    start = time.time()
    temp_paths = []
    pdf = None
    warnings = []
    try:
        _log_progress("RASTERIZE_PDF_START | input={0}".format(input_pdf))
        pdf = pdfium.PdfDocument(input_pdf)
        page_count = len(pdf)
        if page_count <= 0:
            return {"ok": False, "error": "Input PDF has no pages."}
        scale = float(dpi) / 72.0
        for idx in range(page_count):
            _log_progress("RASTERIZE_PDF_PAGE | page={0}".format(idx + 1))
            page = pdf[idx]
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            converted = _prepare_page_image(
                pil_image, color_mode, image_codec, threshold, indexed_colors
            )
            temp_path = _save_temp_page_image(
                converted, image_codec, quality, png_compression_level
            )
            temp_paths.append(temp_path)

        if img2pdf is not None:
            _write_pdf_with_img2pdf(temp_paths, output_pdf, dpi)
        elif image_codec == "jpeg":
            warnings.append("img2pdf missing; used Pillow PDF fallback for JPEG.")
            _write_pdf_with_pillow_fallback(temp_paths, output_pdf, dpi, quality)
        else:
            return {
                "ok": False,
                "error": "img2pdf is required for selected image codec.",
                "install_hint": "{0} -m pip install img2pdf".format(sys.executable),
            }
        if not os.path.exists(output_pdf) or os.path.getsize(output_pdf) <= 0:
            return {"ok": False, "error": "Output PDF was not created."}
        elapsed = time.time() - start
        input_mb = _file_size_mb(input_pdf)
        output_mb = _file_size_mb(output_pdf)
        reduction = None
        if input_mb and output_mb is not None:
            reduction = ((input_mb - output_mb) / input_mb) * 100.0
        _log_progress("RASTERIZE_PDF_DONE | pages={0}".format(page_count))
        return {
            "ok": True,
            "input": input_pdf,
            "output": output_pdf,
            "pages": page_count,
            "dpi": int(dpi),
            "quality": int(quality),
            "color_mode": color_mode,
            "image_codec": image_codec,
            "png_compression_level": int(png_compression_level),
            "indexed_colors": int(indexed_colors) if image_codec == "png-indexed" else None,
            "threshold": int(threshold) if image_codec == "bw" else None,
            "input_mb": input_mb,
            "output_mb": output_mb,
            "reduction_percent": reduction,
            "elapsed_seconds": elapsed,
            "warnings": warnings,
        }
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    finally:
        try:
            if pdf is not None:
                pdf.close()
        except Exception:
            pass
        for temp_path in temp_paths:
            _safe_remove(temp_path)


def main():
    parser = argparse.ArgumentParser(description="Rasterize/flatten a PDF into an image-based PDF.")
    parser.add_argument("--self-test", action="store_true", dest="self_test")
    parser.add_argument("--input", required=False, dest="input_pdf")
    parser.add_argument("--output", required=False, dest="output_pdf")
    parser.add_argument("--dpi", type=int, default=72)
    parser.add_argument("--quality", type=int, default=50)
    parser.add_argument("--color-mode", choices=["color", "grayscale", "bw"], default="color")
    parser.add_argument("--image-codec", choices=["jpeg", "png", "png-indexed", "bw"], default="jpeg")
    parser.add_argument("--png-compression-level", type=int, default=9)
    parser.add_argument("--indexed-colors", type=int, choices=[16, 32, 64, 128, 256], default=128)
    parser.add_argument("--threshold", type=int, default=200)
    args = parser.parse_args()

    if args.self_test:
        result = self_test()
        _print_json(result)
        return 0 if result.get("ok") else 1
    if not args.input_pdf or not args.output_pdf:
        result = {
            "ok": False,
            "error": "--input and --output are required unless --self-test is used.",
        }
        _print_json(result)
        return 1

    dpi = max(36, min(300, int(args.dpi)))
    quality = max(10, min(95, int(args.quality)))
    result = rasterize_pdf(
        args.input_pdf,
        args.output_pdf,
        dpi,
        quality,
        args.color_mode,
        args.image_codec,
        max(1, min(255, int(args.threshold))),
        max(0, min(9, int(args.png_compression_level))),
        int(args.indexed_colors),
    )
    _print_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
