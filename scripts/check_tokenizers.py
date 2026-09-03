import importlib

for name in ("tiktoken", "voyageai", "anthropic", "transformers", "tokenizers"):
    try:
        module = importlib.import_module(name)
        print(f"{name:<14} {getattr(module, '__version__', 'installed')}")
    except Exception:
        print(f"{name:<14} NOT INSTALLED")
