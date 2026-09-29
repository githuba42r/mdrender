import os
import sys


def test_probe():
    print("\nCWD:", os.getcwd())
    for p in sys.path:
        print("PATH:", p)
    import server.app.app  # noqa
    import server
    print("SERVER:", server.__path__)
