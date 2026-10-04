"""Restricted-input parser worker. Stdout is only an integer or 'invalid'."""
import logging
import sys
from io import BytesIO
from pypdf import PdfReader, PdfWriter


def main():
    logging.disable(logging.CRITICAL)
    try:
        # POSIX deployment adds address-space and CPU bounds. Windows parent
        # enforces wall time; Windows memory isolation still requires a Job Object.
        if sys.platform != "win32":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        data = sys.stdin.buffer.read(12 * 1024 * 1024 + 1)
        if not 32 <= len(data) <= 12 * 1024 * 1024:
            raise ValueError()
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError()
        count = len(reader.pages)
        if not 1 <= count <= 1000:
            raise ValueError()
        for page in reader.pages:
            if len(page.mediabox) != 4:
                raise ValueError()
        if len(sys.argv) == 3:
            start, end = map(int, sys.argv[1:])
            if not 1 <= start <= end <= count or end - start > 3:
                raise ValueError()
            writer = PdfWriter()
            for page in reader.pages[start - 1:end]:
                writer.add_page(page)
            output = BytesIO()
            writer.write(output)
            value = output.getvalue()
            if len(value) > 12 * 1024 * 1024:
                raise ValueError()
            sys.stdout.buffer.write(value)
        else:
            print(count)
    except Exception:
        print("invalid")


if __name__ == "__main__":
    main()
