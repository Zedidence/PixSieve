"""
Interactive prompts for the CLI interface.

Provides functions for user interaction including directory selection
and action confirmation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def prompt_for_directories() -> tuple[list[Path], Optional[Path]]:
    """
    Interactively prompt user for one or more directories to scan, then
    optionally which one (if any) is the reference/canonical folder.

    Returns:
        Tuple of (list of validated directory Paths, reference Path or None)

    Notes:
        - Loops asking "add another directory?" until the user declines
        - Handles quoted paths (strips quotes) and validates each directory
          exists before accepting it
        - If more than one directory was entered, asks which (if any) should
          be treated as the reference folder
    """
    print("\n" + "=" * 50)
    print("  PIXSIEVE")
    print("=" * 50)

    directories: list[Path] = []
    first = True
    while True:
        prompt = "\nEnter a directory path to scan: " if first else "\nEnter another directory path to scan (or leave blank to continue): "
        dir_input = input(prompt).strip()

        if not dir_input:
            if first:
                print("Please enter a valid path.")
                continue
            break

        dir_input = dir_input.strip('"\'')
        directory = Path(dir_input)

        if not (directory.exists() and directory.is_dir()):
            print(f"Directory not found: {directory}")
            print("Please try again.")
            continue

        directories.append(directory)
        first = False

    reference_dir = None
    if len(directories) > 1:
        print("\nDirectories to scan:")
        for idx, d in enumerate(directories, start=1):
            print(f"  {idx}. {d}")
        choice = input(
            "\nWhich one (if any) is the reference folder? "
            "Images in it are never deleted/moved. [number, or blank for none]: "
        ).strip()
        if choice.isdigit() and 1 <= int(choice) <= len(directories):
            reference_dir = directories[int(choice) - 1]

    return directories, reference_dir


def confirm_action(action: str, count: int) -> bool:
    """
    Prompt user to confirm a file action.

    Args:
        action: The action to be performed (e.g., 'delete', 'move')
        count: Number of files that will be affected

    Returns:
        True if user confirms (types 'y'), False otherwise

    Examples:
        >>> confirm_action('delete', 42)
        This will delete 42 files. Continue? [y/N]: y
        True
        >>> confirm_action('move', 10)
        This will move 10 files. Continue? [y/N]: n
        False
    """
    confirm = input(f"\nThis will {action} {count:,} files. Continue? [y/N]: ")
    return confirm.lower() == 'y'


__all__ = [
    'prompt_for_directories',
    'confirm_action',
]
