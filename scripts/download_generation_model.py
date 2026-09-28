"""Download the language model that writes answers (~1.1 GB, once).

The file goes to models/generation/, which is gitignored like every model file.
After this, answers work with no network at all.

From the repository root:
    python scripts/download_generation_model.py
"""

from pathlib import Path

# huggingface_hub downloads files from the Hugging Face model hub. It is already
# installed: fastembed uses it to download the embedding model.
from huggingface_hub import hf_hub_download

# Qwen2.5-1.5B-Instruct, Apache-2.0 licence, compressed to 4 bits (q4_k_m).
# For the smaller 0.5B model (see GENERATION_MODEL_PATH in app/core/config.py):
#   REPOSITORY = "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
#   FILENAME = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
REPOSITORY = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
TARGET = Path("models/generation")


def main() -> None:
    path = Path(hf_hub_download(REPOSITORY, FILENAME, local_dir=TARGET))
    print(f"Saved {path} ({path.stat().st_size // 1_000_000} MB)")
    print("GENERATION_MODEL_PATH defaults to this file; nothing else to configure.")


if __name__ == "__main__":
    main()
