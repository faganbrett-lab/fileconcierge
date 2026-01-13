import os
import sys
import hashlib
from collections import defaultdict
from pathlib import Path

# ---------- Helpers ----------

def format_size(bytes_val: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return an MD5 hash of the file contents."""
    h = hashlib.md5()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


# ---------- Core logic ----------

def scan_directory(root: Path):
    """
    Walk the directory and collect:
    - all files
    - total size
    - extension stats (global)
    - size index for duplicate detection
    - per-directory stats (file count + size + per-extension breakdown)

    Directory stats are aggregated up the tree so each directory's totals
    include files in nested subfolders.
    """
    all_files = []
    ext_stats = defaultdict(lambda: {"count": 0, "size": 0})
    size_index = defaultdict(list)

    # Each dir entry contains: count, size, ext (defaultdict of ext -> {count,size})
    dir_stats = defaultdict(lambda: {
        "count": 0,
        "size": 0,
        "ext": defaultdict(lambda: {"count": 0, "size": 0})
    })

    total_size = 0
    total_files = 0

    for dirpath, _, filenames in os.walk(root):
        dirpath = Path(dirpath)
        for name in filenames:
            path = dirpath / name
            try:
                size = path.stat().st_size
            except (FileNotFoundError, PermissionError):
                # Skip files we can't access
                continue

            total_files += 1
            total_size += size
            all_files.append((path, size))

            # --- per-extension stats (global) ---
            ext = path.suffix.lower() or "<no_ext>"
            ext_stats[ext]["count"] += 1
            ext_stats[ext]["size"] += size

            # --- per-size index (for duplicates) ---
            size_index[size].append(path)

            # --- per-directory stats (aggregate up the tree) ---
            # Use a path relative to root so output is shorter.
            rel_dir = dirpath.relative_to(root)
            # Build list of this directory and all its ancestors (including root '.')
            if str(rel_dir) == ".":
                ancestors = [Path(".")]
            else:
                # rel_dir.parents yields parent, grandparent, ..., '.'
                ancestors = [rel_dir] + list(rel_dir.parents)

            for anc in ancestors:
                dir_stats[anc]["count"] += 1
                dir_stats[anc]["size"] += size
                dir_stats[anc]["ext"][ext]["count"] += 1
                dir_stats[anc]["ext"][ext]["size"] += size

    return {
        "all_files": all_files,
        "ext_stats": ext_stats,
        "size_index": size_index,
        "dir_stats": dir_stats,
        "total_files": total_files,
        "total_size": total_size,
    }


def find_largest_files(all_files, top_n: int = 10):
    """Return top_n largest files."""
    return sorted(all_files, key=lambda x: x[1], reverse=True)[:top_n]


def find_duplicate_candidates(size_index):
    """
    First pass: group files with same size.
    Second pass: hash only those groups to confirm duplicates.
    """
    # Only sizes with more than one file are worth checking
    candidates = {size: paths for size, paths in size_index.items() if len(paths) > 1}

    duplicates = []  # list of lists of Paths

    for size, paths in candidates.items():
        hash_buckets = defaultdict(list)
        for path in paths:
            try:
                file_hash = hash_file(path)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            hash_buckets[file_hash].append(path)

        # Only keep groups where 2+ files share the same hash
        for hash_value, dup_paths in hash_buckets.items():
            if len(dup_paths) > 1:
                duplicates.append(dup_paths)

    return duplicates


# ---------- Presentation ----------


def print_extension_summary(ext_stats, total_files, total_size):
    print("\n=== File Types Summary (All Subfolders) ===")
    print(f"Total files: {total_files}")
    print(f"Total size:  {format_size(total_size)}\n")

    header = f"{'Extension':<12} {'Count':>8} {'Total Size':>15}"
    print(header)
    print("-" * len(header))

    # Sort by total size (desc)
    for ext, stats in sorted(ext_stats.items(), key=lambda kv: kv[1]["size"], reverse=True):
        print(f"{ext:<12} {stats['count']:>8} {format_size(stats['size']):>15}")


def print_directory_summary(dir_stats, top_ext: int = 8):
    """
    Show a summary for every subfolder (each directory's total size is
    the sum of files in that directory and all nested subdirectories).
    The list is printed in descending order by total size.

    For each directory, show a compact extension breakdown (top N extensions
    by size for that directory).
    """
    print("\n=== Directory Summary (All Subfolders, sorted by total size) ===")
    if not dir_stats:
        print("No files found.")
        return

    header = f"{'Directory':<60} {'Files':>10} {'Total Size':>15}"
    print(header)
    print("-" * len(header))

    # Sort directories by size (desc) and show all
    sorted_dirs = sorted(dir_stats.items(), key=lambda kv: kv[1]["size"], reverse=True)

    for dir_path, stats in sorted_dirs:
        # Show '.' as root, otherwise the relative path
        dir_label = "." if str(dir_path) == "." else str(dir_path)
        print(f"{dir_label:<60} {stats['count']:>10} {format_size(stats['size']):>15}")

        # Extension breakdown (top N by size)
        ext_map = stats.get("ext", {})
        if ext_map:
            sorted_exts = sorted(ext_map.items(), key=lambda kv: kv[1]["size"], reverse=True)
            # Limit how many extensions to show per directory
            for ext, est in sorted_exts[:top_ext]:
                pct = (est["size"] / stats["size"] * 100) if stats["size"] > 0 else 0.0
                print(f"    {ext:<10} {est['count']:>8} {format_size(est['size']):>12}  {pct:5.1f}%")
            # If there are more extensions than we showed, indicate there's more
            if len(sorted_exts) > top_ext:
                more = len(sorted_exts) - top_ext
                print(f"    ... and {more} more extension(s)")
        print()  # blank line between directories


def print_largest_files(largest_files):
    print("\n=== Largest Files ===")
    if not largest_files:
        print("No files found.")
        return

    header = f"{'Size':>15}  Path"
    print(header)
    print("-" * len(header))

    for path, size in largest_files:
        print(f"{format_size(size):>15}  {path}")


def print_duplicates(duplicates, max_groups: int = 5):
    print("\n=== Possible Duplicates ===")
    if not duplicates:
        print("No likely duplicates found (at least by size + content hash).")
        return

    print(f"Showing up to {max_groups} groups of duplicates:\n")

    for i, group in enumerate(duplicates[:max_groups], start=1):
        print(f"Group {i} ({len(group)} files):")
        for path in group:
            print(f"  - {path}")
        print()


# ---------- CLI entrypoint ----------

def main():
    if len(sys.argv) < 2:
        print("Usage: python file_concierge.py /path/to/folder")
        sys.exit(1)

    root = Path(sys.argv[1]).expanduser().resolve()

    if not root.exists() or not root.is_dir():
        print(f"Error: {root} is not a valid directory.")
        sys.exit(1)

    print(f"Scanning: {root} ...")
    data = scan_directory(root)

    # Global summaries
    print_extension_summary(
        data["ext_stats"],
        data["total_files"],
        data["total_size"],
    )

    # Per-directory view: now shows every subfolder (recursive totals), sorted by size
    # and includes an extension breakdown per folder (top 8 by size)
    print_directory_summary(data["dir_stats"], top_ext=8)

    # Largest files
    largest = find_largest_files(data["all_files"], top_n=10)
    print_largest_files(largest)

    # Duplicate detection (can be slow on huge trees)
    duplicates = find_duplicate_candidates(data["size_index"])
    print_duplicates(duplicates, max_groups=5)


if __name__ == "__main__":
    main()
