with open('/root/engineering-flow-platform/src/utils/file_parser/storage.py', 'r') as f:
    content = f.read()

old = '''def init_storage() -> None:
    """Initialize storage directory."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)'''

new = '''METADATA_FILE = UPLOAD_DIR / "metadata.json"

def _load_metadata() -> None:
    """Load metadata from disk if exists."""
    global _file_metadata
    if METADATA_FILE.exists():
        try:
            with open(METADATA_FILE, 'r') as f:
                data = json.load(f)
            _file_metadata = {
                k: FileMetadata(**v) for k, v in data.items()
            }
            logger.info(f"[storage] Loaded {len(_file_metadata)} files from metadata")
        except Exception as e:
            logger.warning(f"[storage] Failed to load metadata: {e}")

def _save_metadata() -> None:
    """Save metadata to disk."""
    with open(METADATA_FILE, 'w') as f:
        json.dump({
            k: v.model_dump() for k, v in _file_metadata.items()
        }, f, indent=2)

def init_storage() -> None:
    """Initialize storage directory."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _load_metadata()'''

content = content.replace(old, new)

with open('/root/engineering-flow-platform/src/utils/file_parser/storage.py', 'w') as f:
    f.write(content)

print('Done')
