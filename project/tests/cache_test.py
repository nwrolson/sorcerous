import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cache.cache import ScryfallImageCache

cache = ScryfallImageCache(root_dir="./scryfall-cache/cards", memory_items=512,
                           user_agent="VirtualCardPlayer/1.0")
res = cache.get_images("9ed", "100")
if res.ok:
    for img in res.images:
        print(img.face, img.path)
else:
    print(res.error, res.detail)
