from __future__ import annotations

import sys


GIF_PREVIEW_WORKER_ARG = "--lumen-hub-gif-preview-worker"
KEEPALIVE_WORKER_ARG = "--lumen-hub-keepalive-worker"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == [GIF_PREVIEW_WORKER_ARG]:
        from usb9_lcd.gui.gif_preview import main as gif_preview_main

        return gif_preview_main(args[1:])

    if args[:1] == [KEEPALIVE_WORKER_ARG]:
        from usb9_lcd.keepalive import main as keepalive_main

        return keepalive_main(args[1:])

    from usb9_lcd.gui.app import main as gui_main

    return gui_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
