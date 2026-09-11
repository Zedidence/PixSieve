"""
Video-support capability registry for file operations.

Single source of truth for which operations touch video files, and whether
they expose an `include_videos` / `--include-videos` / `includeVideos`
toggle at all. Read by cli/arg_parser.py (whether to add the flag to a
subparser), the API's operations_routes.py (whether to accept the request
field, and to reject it loudly for unsupported ops instead of silently
ignoring it), and the GUI (templates/index.html, rendered server-side, to
decide which operation forms get a video badge/checkbox).

Pure data module - no imports from other pixsieve packages - so it can be
imported anywhere (cli, api, operations, templates rendering) without risking
a circular import.

support levels:
    "full"    - operates on video files exactly like image files today.
    "partial" - operates on video files with a documented limitation (see
                `note`); never silently promote this to "full".
    "none"    - does not support video files.

needs_flag:
    True  - the op filters files by an extensions/include_videos param, so a
            real --include-videos / includeVideos toggle exists and gates
            something. CLI gets the flag; API accepts+validates the field;
            GUI gets a checkbox.
    False - the op has no extension filter at all (it already processes
            every file, video included, unconditionally) - there is nothing
            for a checkbox to toggle. GUI shows only an informational video
            badge; no CLI flag or API field is added.
    Meaningless (None) when support is "none".
"""

from __future__ import annotations

OPERATION_VIDEO_SUPPORT: dict[str, dict[str, object]] = {
    "rename-random": {"support": "full", "needs_flag": True, "note": None},
    "rename-parent": {
        "support": "full", "needs_flag": False,
        "note": "Unfiltered today - already renames every file including video.",
    },
    "move-to-parent": {"support": "full", "needs_flag": True, "note": None},
    "move": {
        "support": "full", "needs_flag": False,
        "note": "move_with_structure is unfiltered today - already moves every file including video.",
    },
    "sort-alpha": {
        "support": "full", "needs_flag": False,
        "note": "Unfiltered today - already sorts every file including video.",
    },
    "strip-ratings": {"support": "full", "needs_flag": True, "note": None},
    "cleanup": {
        "support": "full", "needs_flag": False,
        "note": "delete_empty_folders only checks folder emptiness, not file type.",
    },
    "randomize-dates": {
        "support": "partial", "needs_flag": True,
        "note": "Filesystem dates only - EXIF-tag sync stays image-only.",
    },
    "sort-resolution": {"support": "full", "needs_flag": True, "note": None},
    "sort-color": {"support": "full", "needs_flag": True, "note": None},
    "fix-extensions": {
        "support": "none", "needs_flag": None,
        "note": "No sensible video equivalent; excluded permanently.",
    },
    "convert": {
        "support": "none", "needs_flag": None,
        "note": "No sensible video equivalent; excluded permanently.",
    },
    "repair": {
        "support": "none", "needs_flag": None,
        "note": "Corruption detection/repair is image-format-specific (PIL); deferred.",
    },
    "pipeline": {
        "support": "partial", "needs_flag": True,
        "note": (
            "Applies to the random_rename and randomize_dates steps only "
            "(filesystem dates for the latter); convert_jpg and "
            "repair_corrupt never receive it."
        ),
    },
}


def supports_video(op_name: str) -> bool:
    """True if `op_name` operates on video files at all (full or partial)."""
    entry = OPERATION_VIDEO_SUPPORT.get(op_name)
    return entry is not None and entry["support"] != "none"


def needs_video_flag(op_name: str) -> bool:
    """True if `op_name` has a real include_videos toggle to expose (CLI/API/GUI)."""
    entry = OPERATION_VIDEO_SUPPORT.get(op_name)
    return bool(entry and entry.get("needs_flag"))


__all__ = ["OPERATION_VIDEO_SUPPORT", "supports_video", "needs_video_flag"]
