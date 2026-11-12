from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("aprsd-chess-extension")
except PackageNotFoundError:
    # aprsd's extension listing reads __version__ and crashes if it is missing.
    __version__ = "0.0.0"
