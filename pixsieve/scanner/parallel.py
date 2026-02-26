"""
Parallel processing module for the scanner package.

Provides parallel image analysis with caching, progress tracking, and callback support.
"""

from __future__ import annotations

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from typing import Optional, Callable, Any, Iterable

from ..config import DEFAULT_WORKERS
from ..database import get_cache, CacheStats
from ..models import ImageInfo
from .analysis import analyze_image
from .dependencies import HAS_TQDM, _tqdm_class


def analyze_images_parallel(
    filepaths: list[str],
    max_workers: int = DEFAULT_WORKERS,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    show_progress: bool = True,
    logger: Optional[logging.Logger] = None,
    use_cache: bool = True,
    calculate_hash: bool = True,
    max_queued_futures: Optional[int] = None,
    stream_to_cache: bool = False,
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
        max_queued_futures: D3 - maximum number of futures held in memory at once.
            Defaults to max_workers * 4. Prevents unbounded memory growth for
            very large file lists (650K+) by backpressuring submission.
        stream_to_cache: I1 - if True and cache is available, each result is
            written to the DB immediately via put_async() instead of accumulating
            in a list. After all futures complete the results are read back in a
            single get_batch() call. This reduces peak RAM usage by ~100–200 MB
            for very large collections (100K+ files) by never holding the full
            result list in memory during the analysis phase.

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
        cached_results = cache.get_batch(filepaths)
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

        # D3: bound in-flight futures to prevent memory exhaustion on large collections
        queue_limit = max_queued_futures if max_queued_futures is not None else max_workers * 4
        semaphore = threading.Semaphore(queue_limit)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Map future -> path for error reporting; use a dict that grows lazily
            future_to_path: dict[Future, str] = {}

            def _submit_bounded(path: str) -> Future:
                """Submit after acquiring a semaphore slot; released on completion."""
                semaphore.acquire()
                fut = executor.submit(analyze_image, path, True, calculate_hash)
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
            freshly_cached = cache.get_batch(to_analyze)
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
    discovered_callback: Optional[Callable[[int], None]] = None,
) -> tuple[list[ImageInfo], CacheStats]:
    """
    Analyze images from a chunked discovery generator, processing each chunk
    as soon as it is discovered rather than waiting for full file enumeration.

    This is the large-library variant of analyze_images_parallel. It accepts
    the generator returned by iter_image_chunks() and feeds chunks to the
    ThreadPoolExecutor as they arrive, so analysis of chunk N begins while
    chunk N+1 is still being discovered.

    Args:
        chunk_generator: Iterable of path-lists, e.g. from iter_image_chunks()
        max_workers: Number of parallel workers
        progress_callback: Optional callback(current, total) — total is 0 until
            discovery is complete, after which it reflects the true count.
        show_progress: Whether to show tqdm progress bar (disabled when total unknown)
        logger: Optional logger for status messages
        use_cache: Whether to use SQLite caching
        calculate_hash: Whether to compute file hash
        discovered_callback: Optional callback(count) called each time a new
            chunk is discovered, with the running total of discovered files.
            Useful for SSE progress events during the discovery stage.

    Returns:
        Tuple of (list of ImageInfo objects, CacheStats)
    """
    results: list[ImageInfo] = []
    stats = CacheStats()
    cache = get_cache() if use_cache else None

    all_newly_analyzed: list[ImageInfo] = []
    total_discovered = 0
    total_processed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for chunk in chunk_generator:
            total_discovered += len(chunk)
            stats.total_files += len(chunk)

            if discovered_callback:
                discovered_callback(total_discovered)

            if logger:
                logger.debug(f"Processing discovery chunk of {len(chunk)} files ({total_discovered} discovered so far)")

            # Cache lookup for this chunk
            to_analyze_chunk: list[str] = []
            if cache:
                cached_results = cache.get_batch(chunk)
                for fp in chunk:
                    cached = cached_results.get(fp)
                    if cached is not None:
                        results.append(cached)
                        stats.cache_hits += 1
                        total_processed += 1
                    else:
                        to_analyze_chunk.append(fp)
                        stats.cache_misses += 1
            else:
                to_analyze_chunk = list(chunk)
                stats.cache_misses += len(chunk)

            if not to_analyze_chunk:
                if progress_callback:
                    progress_callback(total_processed, total_discovered)
                continue

            # Submit chunk to thread pool and collect results
            future_to_path: dict[Future, str] = {}
            for path in to_analyze_chunk:
                fut = executor.submit(analyze_image, path, True, calculate_hash)
                future_to_path[fut] = path

            newly_analyzed_chunk: list[ImageInfo] = []
            for future in as_completed(future_to_path):
                try:
                    info = future.result()
                except Exception as e:
                    path = future_to_path[future]
                    info = ImageInfo(path=path, error=str(e))

                results.append(info)
                newly_analyzed_chunk.append(info)
                total_processed += 1

                if progress_callback:
                    current_time = time.time()
                    if total_processed % 1000 == 0:
                        progress_callback(total_processed, total_discovered)

            if cache and newly_analyzed_chunk:
                cache.put_batch(newly_analyzed_chunk)

            all_newly_analyzed.extend(newly_analyzed_chunk)

    # Final progress callback
    if progress_callback:
        progress_callback(total_processed, total_discovered)

    if logger and stats.cache_hits > 0:
        logger.info(
            f"Cache: {stats.cache_hits:,} hits, {stats.cache_misses:,} misses "
            f"({stats.hit_rate:.1f}% hit rate)"
        )

    return results, stats


__all__ = ['analyze_images_parallel', 'analyze_images_streaming']
