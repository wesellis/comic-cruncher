"""
Comic Cruncher Performance Tuner
================================
Profiles each processing stage against real files and reports:
  - Where time is actually spent (extraction, resize, encode, ZIP write)
  - Optimal thread pool size for this machine
  - PIL vs OpenCV speed comparison
  - In-memory vs disk I/O overhead
  - Recommended settings

Usage:
    python tune_cruncher.py <path_to_comic_file.cbz|cbr|pdf>

Outputs a report and optionally patches comic_cruncher.py with tuned values.
"""

import sys
import os
import time
import zipfile
import tempfile
import statistics
import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Optional imports
try:
    import rarfile
    RAR_AVAILABLE = True
except ImportError:
    RAR_AVAILABLE = False

try:
    import cv2
    import numpy as np
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False

try:
    import pdf2image
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp')
TARGET_SIZE = 2500
QUALITY = 85


def is_image_file(filename):
    return filename.lower().endswith(IMAGE_EXTENSIONS)


# ---------------------------------------------------------------------------
# Stage benchmarks
# ---------------------------------------------------------------------------

def bench_extraction_disk(archive_path):
    """Extract to disk (old method) and return (elapsed, file_count, temp_dir)."""
    ext = archive_path.suffix.lower()
    temp_dir = tempfile.mkdtemp(prefix="tune_disk_")
    paths = []
    t0 = time.perf_counter()
    if ext == '.cbz':
        with zipfile.ZipFile(archive_path, 'r') as z:
            for name in sorted(z.namelist()):
                if is_image_file(name):
                    z.extract(name, path=temp_dir)
                    paths.append(os.path.join(temp_dir, name))
    elif ext == '.cbr' and RAR_AVAILABLE:
        with rarfile.RarFile(archive_path, 'r') as r:
            for name in sorted(r.namelist()):
                if is_image_file(name):
                    r.extract(name, path=temp_dir)
                    paths.append(os.path.join(temp_dir, name))
    elapsed = time.perf_counter() - t0
    return elapsed, paths, temp_dir


def bench_extraction_memory(archive_path):
    """Extract to memory (new method) and return (elapsed, [(name, bytes)])."""
    ext = archive_path.suffix.lower()
    results = []
    t0 = time.perf_counter()
    if ext == '.cbz':
        with zipfile.ZipFile(archive_path, 'r') as z:
            for name in sorted(z.namelist()):
                if is_image_file(name):
                    results.append((os.path.basename(name), z.read(name)))
    elif ext == '.cbr' and RAR_AVAILABLE:
        with rarfile.RarFile(archive_path, 'r') as r:
            for name in sorted(r.namelist()):
                if is_image_file(name):
                    results.append((os.path.basename(name), r.read(name)))
    elapsed = time.perf_counter() - t0
    return elapsed, results


def process_pil_from_bytes(img_bytes):
    """PIL resize + WebP encode from bytes, return (elapsed, output_bytes)."""
    t0 = time.perf_counter()
    img = Image.open(io.BytesIO(img_bytes))
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGB')
    w, h = img.size
    if max(w, h) > TARGET_SIZE:
        if w > h:
            nw, nh = TARGET_SIZE, int((h * TARGET_SIZE) / w)
        else:
            nh, nw = TARGET_SIZE, int((w * TARGET_SIZE) / h)
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'WEBP', quality=QUALITY, optimize=True)
    elapsed = time.perf_counter() - t0
    return elapsed, buf.getvalue()


def process_pil_from_disk(img_path):
    """PIL resize + WebP encode from disk path, return elapsed."""
    t0 = time.perf_counter()
    with Image.open(img_path) as img:
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        w, h = img.size
        if max(w, h) > TARGET_SIZE:
            if w > h:
                nw, nh = TARGET_SIZE, int((h * TARGET_SIZE) / w)
            else:
                nh, nw = TARGET_SIZE, int((w * TARGET_SIZE) / h)
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, 'WEBP', quality=QUALITY, optimize=True)
    elapsed = time.perf_counter() - t0
    return elapsed


def process_opencv_from_bytes(img_bytes):
    """OpenCV resize + PIL WebP encode from bytes, return elapsed."""
    t0 = time.perf_counter()
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        return None
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = img_rgb.shape[:2]
    if max(w, h) > TARGET_SIZE:
        if w > h:
            nw, nh = TARGET_SIZE, int((h * TARGET_SIZE) / w)
        else:
            nh, nw = TARGET_SIZE, int((w * TARGET_SIZE) / h)
        img_rgb = cv2.resize(img_rgb, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
    pil_img = Image.fromarray(img_rgb)
    buf = io.BytesIO()
    pil_img.save(buf, 'WEBP', quality=QUALITY, optimize=True)
    elapsed = time.perf_counter() - t0
    return elapsed


def bench_zip_write(data_list):
    """Benchmark writing pre-compressed data to ZIP_STORED vs ZIP_DEFLATED."""
    results = {}
    for method_name, method in [("ZIP_STORED", zipfile.ZIP_STORED), ("ZIP_DEFLATED", zipfile.ZIP_DEFLATED)]:
        with tempfile.NamedTemporaryFile(suffix='.cbz', delete=False) as tmp:
            tmp_path = tmp.name
        t0 = time.perf_counter()
        with zipfile.ZipFile(tmp_path, 'w', method) as zf:
            for i, data in enumerate(data_list):
                zf.writestr(f"page_{i:04d}.webp", data)
        elapsed = time.perf_counter() - t0
        size = os.path.getsize(tmp_path)
        os.remove(tmp_path)
        results[method_name] = (elapsed, size)
    return results


def bench_thread_scaling(image_data_list, process_func, max_threads=None):
    """Test throughput at different thread counts. Returns {n_threads: elapsed}."""
    if max_threads is None:
        max_threads = min(os.cpu_count() * 2, 32)

    # Test: 1, 2, 4, 6, 8, ... up to max_threads
    thread_counts = sorted(set([1, 2, 4] + list(range(4, max_threads + 1, 2))))
    thread_counts = [t for t in thread_counts if t <= max_threads]

    results = {}
    # Warm up
    process_func(image_data_list[0])

    for n in thread_counts:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n) as executor:
            futs = [executor.submit(process_func, d) for d in image_data_list]
            for f in futs:
                f.result()
        elapsed = time.perf_counter() - t0
        results[n] = elapsed
        # Early exit if we're not getting faster
        if n > 4 and len(results) >= 3:
            recent = list(results.values())[-3:]
            if recent[-1] >= recent[-2] >= recent[-3]:
                break

    return results


def bench_pdf_extraction(pdf_path, max_pages=10):
    """Benchmark PDF page extraction speed."""
    if not PDF_AVAILABLE:
        return None
    try:
        info = pdf2image.pdfinfo_from_path(pdf_path)
        total_pages = info["Pages"]
        pages_to_test = min(total_pages, max_pages)

        t0 = time.perf_counter()
        images = pdf2image.convert_from_path(
            pdf_path, dpi=300,
            first_page=1,
            last_page=pages_to_test
        )
        elapsed = time.perf_counter() - t0
        return elapsed, pages_to_test, total_pages, images
    except Exception as e:
        print(f"  PDF extraction failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def format_size(b):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if b < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}TB"


def main():
    if len(sys.argv) < 2:
        print("Usage: python tune_cruncher.py <comic_file>")
        print("Supported: .cbz, .cbr, .pdf")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"File not found: {file_path}")
        sys.exit(1)

    ext = file_path.suffix.lower()
    print(f"\n{'='*60}")
    print(f"  Comic Cruncher Performance Tuner")
    print(f"{'='*60}")
    print(f"  File: {file_path.name}")
    print(f"  Size: {format_size(file_path.stat().st_size)}")
    print(f"  CPUs: {os.cpu_count()}")
    print(f"  OpenCV: {'Yes' if OPENCV_AVAILABLE else 'No'}")
    print(f"{'='*60}\n")

    is_pdf = ext == '.pdf'
    sample_bytes_list = []
    webp_outputs = []

    # -----------------------------------------------------------------------
    # 1. Extraction benchmark
    # -----------------------------------------------------------------------
    if is_pdf:
        print("[1/5] PDF Extraction")
        result = bench_pdf_extraction(file_path)
        if result is None:
            print("  SKIPPED (pdf2image not available or failed)")
            sys.exit(1)
        elapsed, tested, total, pil_images = result
        per_page = elapsed / tested
        print(f"  {tested}/{total} pages in {elapsed:.2f}s ({per_page:.2f}s/page)")
        print(f"  Estimated full file: {per_page * total:.1f}s")

        # Convert first N PIL images to bytes for processing benchmarks
        for img in pil_images[:20]:
            buf = io.BytesIO()
            img.save(buf, 'PNG')
            sample_bytes_list.append(buf.getvalue())
        print(f"  Sample images for benchmarks: {len(sample_bytes_list)}")
    else:
        print("[1/5] Extraction: Disk vs Memory")
        disk_time, disk_paths, disk_tmp = bench_extraction_disk(file_path)
        mem_time, mem_results = bench_extraction_memory(file_path)
        n_images = len(mem_results)

        speedup = disk_time / mem_time if mem_time > 0 else float('inf')
        print(f"  Images found: {n_images}")
        print(f"  Disk extraction: {disk_time:.3f}s")
        print(f"  Memory extraction: {mem_time:.3f}s")
        print(f"  Speedup: {speedup:.1f}x {'(memory wins)' if speedup > 1 else '(disk wins)'}")

        # Use a sample for processing benchmarks
        sample_bytes_list = [data for _, data in mem_results[:20]]

        # Clean up disk temp
        import shutil
        shutil.rmtree(disk_tmp, ignore_errors=True)

    if not sample_bytes_list:
        print("\nNo images found to benchmark. Exiting.")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # 2. Single-image processing: PIL vs OpenCV
    # -----------------------------------------------------------------------
    print(f"\n[2/5] Single-Image Processing (sample of {len(sample_bytes_list)} images)")

    pil_times = []
    for data in sample_bytes_list:
        elapsed, webp_data = process_pil_from_bytes(data)
        pil_times.append(elapsed)
        webp_outputs.append(webp_data)

    pil_avg = statistics.mean(pil_times)
    pil_med = statistics.median(pil_times)
    print(f"  PIL:    avg={pil_avg:.3f}s  median={pil_med:.3f}s")

    if OPENCV_AVAILABLE:
        cv_times = []
        for data in sample_bytes_list:
            elapsed = process_opencv_from_bytes(data)
            if elapsed is not None:
                cv_times.append(elapsed)

        if cv_times:
            cv_avg = statistics.mean(cv_times)
            cv_med = statistics.median(cv_times)
            speedup = pil_avg / cv_avg if cv_avg > 0 else float('inf')
            print(f"  OpenCV: avg={cv_avg:.3f}s  median={cv_med:.3f}s")
            print(f"  OpenCV speedup: {speedup:.2f}x {'(OpenCV wins)' if speedup > 1 else '(PIL wins)'}")
            best_processor = "opencv" if speedup > 1.05 else "pil"
        else:
            print("  OpenCV: all images failed to decode")
            best_processor = "pil"
    else:
        print("  OpenCV: not installed")
        best_processor = "pil"

    # -----------------------------------------------------------------------
    # 3. Thread scaling
    # -----------------------------------------------------------------------
    print(f"\n[3/5] Thread Pool Scaling")

    # Use the faster processor for thread scaling
    if best_processor == "opencv" and OPENCV_AVAILABLE:
        test_func = lambda d: process_opencv_from_bytes(d)
        proc_label = "OpenCV"
    else:
        test_func = lambda d: process_pil_from_bytes(d)
        proc_label = "PIL"

    scaling = bench_thread_scaling(sample_bytes_list, test_func)
    best_threads = min(scaling, key=scaling.get)
    best_time = scaling[best_threads]
    single_time = scaling.get(1, best_time)

    print(f"  Processor: {proc_label}")
    print(f"  {'Threads':>8} | {'Time':>8} | {'Speedup':>8} | {'Per-img':>8}")
    print(f"  {'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
    for n, t in sorted(scaling.items()):
        sp = single_time / t if t > 0 else 0
        per = t / len(sample_bytes_list)
        marker = " <-- best" if n == best_threads else ""
        print(f"  {n:>8} | {t:>7.2f}s | {sp:>7.2f}x | {per:>7.3f}s{marker}")

    # -----------------------------------------------------------------------
    # 4. ZIP write benchmark
    # -----------------------------------------------------------------------
    print(f"\n[4/5] ZIP Write: STORED vs DEFLATED")
    if webp_outputs:
        zip_results = bench_zip_write(webp_outputs)
        for method, (elapsed, size) in zip_results.items():
            print(f"  {method:>12}: {elapsed:.3f}s  size={format_size(size)}")

        stored_time, stored_size = zip_results["ZIP_STORED"]
        deflated_time, deflated_size = zip_results["ZIP_DEFLATED"]
        size_diff_pct = ((deflated_size - stored_size) / stored_size * 100) if stored_size > 0 else 0
        time_saved = deflated_time - stored_time
        print(f"  DEFLATED saves {abs(size_diff_pct):.1f}% space but costs {time_saved:.3f}s extra")
        best_zip = "ZIP_STORED" if abs(size_diff_pct) < 3 else "ZIP_DEFLATED"
        print(f"  Recommendation: {best_zip}")
    else:
        best_zip = "ZIP_STORED"

    # -----------------------------------------------------------------------
    # 5. Batch parallelism estimate
    # -----------------------------------------------------------------------
    print(f"\n[5/5] Batch Parallelism Estimate")
    cpu_count = os.cpu_count()
    max_concurrent_files = max(1, min(3, cpu_count // 4))
    inner_threads = max(2, cpu_count // max_concurrent_files)
    print(f"  CPU cores: {cpu_count}")
    print(f"  Recommended concurrent files: {max_concurrent_files}")
    print(f"  Inner thread pool per file: {inner_threads}")
    print(f"  Total threads at peak: {max_concurrent_files * inner_threads}")

    # -----------------------------------------------------------------------
    # Summary & Recommendations
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  RECOMMENDATIONS")
    print(f"{'='*60}")
    print(f"  Image processor:     {proc_label} ({'process_image_opencv' if best_processor == 'opencv' else 'process_image'})")
    print(f"  Thread pool size:    {best_threads} threads (single file)")
    print(f"  ZIP method:          {best_zip}")
    print(f"  Batch concurrency:   {max_concurrent_files} files x {inner_threads} threads")
    if not is_pdf:
        print(f"  Extraction method:   In-memory (no disk I/O)")

    # Estimate total processing time for the file
    n_total = len(sample_bytes_list)
    if not is_pdf:
        n_total = len(mem_results)
    est_per_image = best_time / len(sample_bytes_list)
    est_total = est_per_image * n_total
    print(f"\n  Estimated processing time for this file:")
    print(f"    {n_total} images x {est_per_image:.3f}s = {est_total:.1f}s ({est_total/60:.1f}min)")

    # -----------------------------------------------------------------------
    # Optional: patch comic_cruncher.py
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    cruncher_path = Path(__file__).parent / "comic_cruncher.py"
    if cruncher_path.exists():
        print(f"  comic_cruncher.py found at: {cruncher_path}")
        # Check if --apply flag was passed
        if '--apply' in sys.argv:
            import re
            content = cruncher_path.read_text(encoding='utf-8')
            old_pattern = r'ThreadPoolExecutor\(max_workers=os\.cpu_count\(\)(?:\s*\*\s*\d+)?\)'
            new_value = f'ThreadPoolExecutor(max_workers={best_threads})'
            patched = re.sub(old_pattern, new_value, content)
            if patched != content:
                cruncher_path.write_text(patched, encoding='utf-8')
                print(f"  PATCHED: all ThreadPoolExecutor set to {best_threads} threads")
            else:
                print("  No matching patterns found to patch (already tuned?)")
        else:
            print(f"  To apply: python tune_cruncher.py <file> --apply")
    else:
        print(f"  comic_cruncher.py not found in {Path(__file__).parent}")

    print()


if __name__ == "__main__":
    main()
