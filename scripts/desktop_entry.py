"""PyInstaller entry point; imports remain within the installed control-plane package.

No arguments: the legacy one-request sidecar (``desktop.main``), unchanged. Exactly ``--controller``: the persistent
graph controller (``controller_entry.main``, Task 9.4). Any other arguments are refused without reading stdin.
"""
import sys

UNSUPPORTED_ARGUMENTS = "unsupported sidecar arguments"   # = controller_entry.UNSUPPORTED_ARGUMENTS


def run(argv: list[str]) -> int:
    if not argv:
        from tuning_factory.desktop import main
        main()   # legacy one-request mode, unchanged
        return 0
    if argv == ["--controller"]:
        from tuning_factory.controller_entry import main as controller_main
        return controller_main()
    sys.stderr.write(UNSUPPORTED_ARGUMENTS + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
