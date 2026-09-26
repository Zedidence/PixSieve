"""
Parallel processing module for the scanner package.

Provides parallel image analysis with caching, progress tracking, and callback support.
"""

from __future__ import annotations

import logging
import os
import queue
import time
import threading
from concurrent.futures import ThreadPoolExecutor, Future, as_completed, wait as futures_wait
from typing import Optional, Callable, Any, Iterable, Union

from ..config import DEFAULT_WORKERS, SLOW_FILE_WARN_SECONDS, WRITE_BATCH_SIZE, VIDEO_EXTENSIONS
from ..database import get_cache, CacheStats
from ..models import ImageInfo
from ..utils.adaptive import AdaptiveTuner
from .analysis import analyze_image
from .video_analysis import analyze_video
from .dependencies import HAS_TQDM, _tqdm_class

_logger = logging.getLogger(__name__)

# AnalyzeFn signature shared by analyze_image() and analyze_video(): both take
# (filepath, calculate_phash, calculate_hash) and return an ImageInfo. Kept as
# a plain Callable type hint (no Protocol) to match the rest of this module's
# typing style.
AnalyzeFn = Callable[[str, bool, bool], ImageInfo]


def analyze_media(filepath: str, calculate_phash: bool = True, calculate_hash: bool = True) -> ImageInfo:
    """
    Dispatch to analyze_image() or analyze_video() based on file extension.

    For use as the `analyze_fn` passed to analyze_images_streaming() when a
    single chunk generator yields a mix of image and video paths (the API's
    overlapped discovery+analysis path doesn't split paths into separate
    per-type lists up front the way the CLI's sequential path does).
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return analyze_video(filepath, calculate_phash, calculate_hash)
    return analyze_image(filepath, calculate_phash, calculate_hash)


def _analyze_with_slow_warning(
    filepath: str,
    calculate_hash: bool,
    calculate_phash: bool = True,
    analyze_fn: AnalyzeFn = analyze_image,
    tuner: Optional[AdaptiveTuner] = None,
) -> ImageInfo:
    """
    Wraps analyze_image()/analyze_video() with a timer so a single
    pathological file (huge panorama, near-decompression-bomb, flaky
    USB/network read) shows up as a WARNING log instead of just silently
    eating time on a worker with no visible symptom besides an unexplained
    slow scan.

    With a `tuner`, the analysis runs inside one of its adjustable slots
    (the pool itself is sized to the tuner's ceiling) and reports the bytes
    processed so it can steer concurrency toward peak throughput.
    """
    start = time.monotonic()
    if tuner is None:
        info = analyze_fn(filepath, calculate_phash, calculate_hash)
    else:
        with tuner.slot():
            info = analyze_fn(filepath, calculate_phash, calculate_hash)
        tuner.record(info.file_size)
    elapsed = time.monotonic() - start
    if elapsed >= SLOW_FILE_WARN_SECONDS:
        _logger.warning(f"Slow file: {filepath} took {elapsed:.1f}s to analyze")
    return info


def analyze_images_parallel(
    filepaths: list[str],
    max_workers: int = DEFAULT_WORKERS,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    show_progress: bool = True,
    logger: Optional[logging.Logger] = None,
    use_cache: bool = True,
    calculate_hash: bool = True,
    calculate_phash: bool = True,
    max_queued_futures: Optional[int] = None,
    stream_to_cache: bool = False,
    analyze_fn: AnalyzeFn = analyze_image,
    stat_workers: Optional[int] = None,
    tuner: Optional[AdaptiveTuner] = None,
) -> tuple[list[ImageInfo], CacheStats]:
    """
    Analyze multiple images in parallel with optional caching.

    Args:
        filepaths: List of image paths to analyze
        max_workers: Number of parallel workers
        progress_callback: Optional callback(current, total) for progress updates
        show_progress: Whether to show tqdm progress bar
        logger: Optional logger for status messages
        use_cache: Whether to use SQLite caching
        calculate_hash: Whether to compute file hash (for exact duplicate detection)
        calculate_phash: Whether to compute perceptual hash (for similarity
            matching). Pass False when only exact-hash duplicates will be
            searched for (e.g. exact_only mode) to skip the image decode +
            thumbnail + pHash cost entirely — it would otherwise be computed
            and then never used.
        max_queued_futures: D3 - maximum number of futures held in memory at once.
            Defaults to max_workers * 4. Prevents unbounded memory growth for
            very large file lists (650K+) by backpressuring submission.
        stream_to_cache: I1 - if True and cache is available, each result is
            written to the DB immediately via put_async() instead of accumulating
            in a list. After all futures complete the results are read back in a
            single get_batch() call. This reduces peak RAM usage by ~100–200 MB
            for very large collections (100K+ files) by never holding the full
            result list in memory during the analysis phase.
        analyze_fn: Per-file analysis function - analyze_image() (default) or
            analyze_video(). Shares this function's caching/threading/progress
            machinery, which is agnostic to what a single file's analysis does.
        stat_workers: Threads for the cache-validation stat() pass (see
            database/operations.py get_batch()); None uses its default.
        tuner: Optional utils/adaptive.py AdaptiveTuner. When given, the pool
            is sized to its ceiling and the number of files analyzed at once
            is adjusted during the run (starting from its initial limit)
            instead of staying fixed at max_workers.

    Returns:
        Tuple of (list of ImageInfo objects, CacheStats)
    """
    if not filepaths:
        return [], CacheStats()

    results: list[ImageInfo] = []
    stats = CacheStats(total_files=len(filepaths))

    # Try to get cached results first
    cache = get_cache() if use_cache else None
    to_analyze: list[str] = []

    if cache:
        cached_results = cache.get_batch(filepaths, max_workers=stat_workers)
        for filepath in filepaths:
            cached = cached_results.get(filepath)
            if cached is not None:
                results.append(cached)
                stats.cache_hits += 1
            else:
                to_analyze.append(filepath)
                stats.cache_misses += 1

        if logger and stats.cache_hits > 0:
            logger.info(
                f"Cache: {stats.cache_hits:,} hits, {stats.cache_misses:,} misses "
                f"({stats.hit_rate:.1f}% hit rate)"
            )
    else:
        to_analyze = list(filepaths)
        stats.cache_misses = len(filepaths)

    # I1: only stream when cache is actually available
    _streaming = stream_to_cache and cache is not None

    # Analyze uncached files
    if to_analyze:
        pbar: Optional[Any] = None
        if HAS_TQDM and show_progress and _tqdm_class is not None:
            pbar = _tqdm_class(
                total=len(to_analyze),
                desc="Analyzing images",
                unit="img",
                ncols=80,
            )

        # I1 streaming: don't accumulate; normal path: collect for put_batch
        newly_analyzed: list[ImageInfo] = []

        # Batch progress callbacks to reduce overhead (every 1000 files or 1 second)
        last_callback_time = time.time()
        callback_batch_size = 1000
        callback_interval = 1.0  # seconds

        pool_size = tuner.ceiling if tuner is not None else max_workers

        # D3: bound in-flight futures to prevent memory exhaustion on large collections
        queue_limit = max_queued_futures if max_queued_futures is not None else pool_size * 4
        semaphore = threading.Semaphore(queue_limit)

        with ThreadPoolExecutor(max_workers=pool_size) as executor:
            # Map future -> path for error reporting; use a dict that grows lazily
            future_to_path: dict[Future, str] = {}

            def _submit_bounded(path: str) -> Future:
                """Submit after acquiring a semaphore slot; released on completion."""
                semaphore.acquire()
                fut = executor.submit(
                    _analyze_with_slow_warning, path, calculate_hash, calculate_phash, analyze_fn, tuner
                )
                future_to_path[fut] = path
                fut.add_done_callback(lambda _: semaphore.release())
                return fut

            # Submit all tasks (blocks when queue_limit is reached)
            all_futures = [_submit_bounded(path) for path in to_analyze]

            for i, future in enumerate(as_completed(all_futures)):
                try:
                    info = future.result()
                except Exception as e:
                    filepath = future_to_path[future]
                    info = ImageInfo(path=filepath, error=str(e))

                if _streaming:
                    # I1: stream to DB immediately; don't grow the in-memory list
                    cache.put_async(info)
                else:
                    results.append(info)
                    newly_analyzed.append(info)

                if pbar is not None:
                    pbar.update(1)

                if tuner is not None:
                    tuner.maybe_adjust()

                # Batch progress callbacks to reduce overhead
                if progress_callback:
                    current_time = time.time()
                    should_callback = (
                        (i + 1) % callback_batch_size == 0 or
                        current_time - last_callback_time >= callback_interval or
                        i == len(to_analyze) - 1  # Always callback on last item
                    )
                    if should_callback:
                        progress_callback(stats.cache_hits + i + 1, len(filepaths))
                        last_callback_time = current_time

        if pbar is not None:
            pbar.close()

        if _streaming:
            # I1: flush background writes, then read all newly-analyzed results
            # back from DB in one batch. Cache hits are already in `results`.
            cache.flush_writes()
            freshly_cached = cache.get_batch(to_analyze, max_workers=stat_workers)
            results.extend(v for v in freshly_cached.values() if v is not None)
        elif cache and newly_analyzed:
            # Normal path: batch-write at end (F1: uses background writer)
            cache.put_batch(newly_analyzed)

    return results, stats


def analyze_images_streaming(
    chunk_generator: Iterable[list[str]],
    max_workers: int = DEFAULT_WORKERS,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    show_progress: bool = True,
    logger: Optional[logging.Logger] = None,
    use_cache: bool = True,
    calculate_hash: bool = True,
    calculate_phash: Union[bool, Callable[[], bool]] = True,
    discovered_callback: Optional[Callable[[int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    analyze_fn: AnalyzeFn = analyze_image,
    stat_workers: Optional[int] = None,
    tuner: Optional[AdaptiveTuner] = None,
) -> tuple[list[ImageInfo], CacheStats]:
    """
    Analyze images from a chunked discovery generator, overlapping analysis
    with discovery instead of waiting for the full file enumeration to finish.

    This is the large-library variant of analyze_images_parallel. A dedicated
    producer thread walks the generator (e.g. from iter_image_chunks()),
    looks up each chunk in the cache, and submits cache misses to the
    ThreadPoolExecutor - all without waiting for previously-submitted files to
    finish first (only bounded by the in-flight-futures semaphore below).
    Each future's completion is pushed onto a queue that the calling thread
    drains continuously. This means directory-walking I/O (often the
    bottleneck on slow/removable media) and image analysis genuinely run
    concurrently, rather than the previous chunk-at-a-time
    discover/analyze/discover/analyze ordering, which took the same total
    wall time as discovering everything up front.

    Args:
        chunk_generator: Iterable of path-lists, e.g. from iter_image_chunks()
        max_workers: Number of parallel workers
        progress_callback: Optional callback(current, total) — total is 0 until
            discovery is complete, after which it reflects the true count.
        show_progress: Whether to show a tqdm bar (indeterminate until the
            true total is known, since discovery isn't complete up front).
        logger: Optional logger for status messages
        use_cache: Whether to use SQLite caching
        calculate_hash: Whether to compute file hash
        calculate_phash: Whether to compute perceptual hash (see
            analyze_images_parallel for when to disable this). May also be a
            zero-arg callable, re-evaluated once per file right before
            submission - lets a caller flip this mid-scan (e.g. once the
            discovered count crosses a "this collection is huge, stop
            computing pHashes" threshold) without waiting for discovery to
            finish first, which the whole point of this function is to avoid.
        discovered_callback: Optional callback(count) called each time a new
            chunk is discovered, with the running total of discovered files.
            Useful for SSE progress events during the discovery stage.
        cancel_check: Optional callable returning True once the caller wants
            to stop. Checked by the producer thread between chunks so it can
            exit promptly instead of walking the rest of the tree.
        analyze_fn: Per-file analysis function - analyze_image() (default) or
            analyze_video().
        stat_workers: Threads for each chunk's cache-validation stat() pass.
        tuner: Optional AdaptiveTuner (see analyze_images_parallel).

    Returns:
        Tuple of (list of ImageInfo objects, CacheStats)
    """
    results: list[ImageInfo] = []
    stats = CacheStats()
    cache = get_cache() if use_cache else None

    total_discovered = 0
    total_processed = 0

    _SENTINEL = object()
    # ('hit', ImageInfo) for cache hits, ('new', ImageInfo) for freshly
    # analyzed files - tagged so the harvester below only ever writes
    # freshly-analyzed results back to the cache.
    result_queue: "queue.Queue" = queue.Queue()

    # Bounds how many files may be in flight (submitted-but-not-completed) at
    # once, so a discovery walk that runs far ahead of slow analysis (e.g. a
    # cold external drive) can't queue up unbounded memory.
    pool_size = tuner.ceiling if tuner is not None else max_workers
    semaphore = threading.Semaphore(pool_size * 4)

    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        all_futures: list[Future] = []

        def _on_done(fut: Future, path: str) -> None:
            semaphore.release()
            try:
                info = fut.result()
            except Exception as e:
                info = ImageInfo(path=path, error=str(e))
            result_queue.put(('new', info))

        def _produce() -> None:
            nonlocal total_discovered
            try:
                for chunk in chunk_generator:
                    if cancel_check and cancel_check():
                        break

                    total_discovered += len(chunk)
                    stats.total_files += len(chunk)
                    if discovered_callback:
                        discovered_callback(total_discovered)
                    if logger:
                        logger.debug(
                            f"Processing discovery chunk of {len(chunk)} files "
                            f"({total_discovered} discovered so far)"
                        )

                    if cache:
                        cached_results = cache.get_batch(chunk, max_workers=stat_workers)
                        to_submit = []
                        for fp in chunk:
                            cached = cached_results.get(fp)
                            if cached is not None:
                                stats.cache_hits += 1
                                result_queue.put(('hit', cached))
                            else:
                                stats.cache_misses += 1
                                to_submit.append(fp)
                    else:
                        stats.cache_misses += len(chunk)
                        to_submit = list(chunk)

                    for path in to_submit:
                        # This is where a slow/removable source drive naturally
                        # throttles discovery: once pool_size*4 files are
                        # already in flight, we block here until analysis
                        # frees a slot - discovery of the *next* chunk still
                        # overlaps with analysis of files already submitted.
                        semaphore.acquire()
                        phash_flag = calculate_phash() if callable(calculate_phash) else calculate_phash
                        fut = executor.submit(
                            _analyze_with_slow_warning, path, calculate_hash, phash_flag, analyze_fn, tuner
                        )
                        all_futures.append(fut)
                        fut.add_done_callback(lambda f, p=path: _on_done(f, p))

                    if cancel_check and cancel_check():
                        break
            finally:
                # Wait for every future this thread submitted before signalling
                # "no more results" - the callbacks above have already been
                # pushing completions into result_queue concurrently the whole
                # time, so this wait costs nothing beyond the slowest
                # already-running task.
                futures_wait(all_futures)
                result_queue.put(_SENTINEL)

        producer = threading.Thread(target=_produce, daemon=True, name="pixsieve-discovery")
        producer.start()

        pbar: Optional[Any] = None
        if HAS_TQDM and show_progress and _tqdm_class is not None:
            # Indeterminate at first (discovery isn't complete) - .total is
            # updated as the running discovered count comes in below.
            pbar = _tqdm_class(total=None, desc="Analyzing images", unit="img", ncols=80)

        newly_analyzed: list[ImageInfo] = []
        while True:
            item = result_queue.get()
            if item is _SENTINEL:
                break
            kind, info = item
            results.append(info)
            total_processed += 1

            if pbar is not None:
                pbar.total = total_discovered or None
                pbar.update(1)

            if kind == 'new':
                if tuner is not None:
                    tuner.maybe_adjust()
                newly_analyzed.append(info)
                # Flush to cache periodically rather than accumulating
                # everything until the whole (possibly 800K+ file) scan
                # finishes - keeps peak memory bounded and means a
                # cancelled/interrupted scan still has most of its work
                # cached for the next run.
                if cache and len(newly_analyzed) >= WRITE_BATCH_SIZE:
                    cache.put_batch(newly_analyzed)
                    newly_analyzed = []

            if progress_callback and total_processed % 1000 == 0:
                progress_callback(total_processed, total_discovered)

        if pbar is not None:
            pbar.close()

        if cache and newly_analyzed:
            cache.put_batch(newly_analyzed)

        producer.join(timeout=5.0)

    # Final progress callback
    if progress_callback:
        progress_callback(total_processed, total_discovered)

    if logger and stats.cache_hits > 0:
        logger.info(
            f"Cache: {stats.cache_hits:,} hits, {stats.cache_misses:,} misses "
            f"({stats.hit_rate:.1f}% hit rate)"
        )

    return results, stats


def analyze_videos_parallel(filepaths: list[str], **kwargs) -> tuple[list[ImageInfo], CacheStats]:
    """
    analyze_images_parallel(), but for video files - see that function for
    all parameters. Reuses the exact same caching/threading/progress
    machinery, only swapping the per-file analysis function.
    """
    kwargs.pop('analyze_fn', None)
    return analyze_images_parallel(filepaths, analyze_fn=analyze_video, **kwargs)


def analyze_videos_streaming(
    chunk_generator: Iterable[list[str]], **kwargs
) -> tuple[list[ImageInfo], CacheStats]:
    """
    analyze_images_streaming(), but for video files - see that function for
    all parameters.
    """
    kwargs.pop('analyze_fn', None)
    return analyze_images_streaming(chunk_generator, analyze_fn=analyze_video, **kwargs)


__all__ = [
    'analyze_images_parallel',
    'analyze_images_streaming',
    'analyze_videos_parallel',
    'analyze_videos_streaming',
    'analyze_media',
]
