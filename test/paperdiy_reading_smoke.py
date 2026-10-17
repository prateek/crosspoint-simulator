"""Read, page, sleep and resume an isolated EPUB in the PaperDIY simulator."""
import argparse
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import zipfile


def bitmap(path):
    data = path.read_bytes()
    assert data[:2] == b"BM"
    offset = struct.unpack_from('<I', data, 10)[0]
    width, height = struct.unpack_from('<ii', data, 18)
    bits = struct.unpack_from('<H', data, 28)[0]
    assert (width, abs(height)) == (540, 960), (width, height)
    stride = ((width * bits + 31) // 32) * 4
    pixels = data[offset:offset + stride * abs(height)]
    assert len(set(pixels)) > 1, 'blank framebuffer'
    # Exclude the status footer; elapsed reading time can differ after reboot.
    return pixels[stride * 80:] if height > 0 else pixels[:-stride * 80]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    root = args.artifacts or Path(tempfile.mkdtemp(prefix='paperdiy-reading-'))
    root.mkdir(parents=True, exist_ok=True)
    sd = root / 'fs_'
    (sd / 'books').mkdir(parents=True, exist_ok=True)
    config = sd / '.crosspoint'
    config.mkdir(exist_ok=True)
    assert not (config / 'state.json').exists(), 'use an empty artifact directory'
    body = ''.join(f'<p>Paragraph {i}. PaperDIY reading fixture checks page layout, page turns, and persisted reading position. This text belongs to a synthetic test book.</p>' for i in range(100))
    with zipfile.ZipFile(sd / 'books' / 'fixture.epub', 'w') as epub:
        epub.writestr('mimetype', 'application/epub+zip')
        epub.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        epub.writestr('content.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">paperdiy-test</dc:identifier><dc:title>PaperDIY Test</dc:title><dc:language>en</dc:language></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>')
        epub.writestr('chapter.xhtml', f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Reading</title></head><body>{body}</body></html>')
    (config / 'state.json').write_text(json.dumps({'openEpubPath': '/books/fixture.epub', 'lastSleepFromReader': True, 'showBootScreen': False}))
    env = {k: v for k, v in os.environ.items() if not k.startswith('CROSSPOINT_SIM_')}
    env['SDL_VIDEODRIVER'] = 'dummy'

    def run(name, keys, screens):
        with (root / f'{name}.log').open('w') as log:
            result = subprocess.run([str(binary)], cwd=root, env={**env, 'CROSSPOINT_SIM_INPUT_SCRIPT': keys, 'CROSSPOINT_SIM_SCREENSHOTS': screens}, stdout=log, stderr=log, timeout=30)
        assert result.returncode == 0, (name, result.returncode)
        return (root / f'{name}.log').read_text()

    output = run('reading', '6000:DOWN;10000:SLEEP;13000:QUIT', '4000:first.bmp;8000:second.bmp')
    assert 'Device: paperdiy' in output
    assert re.search(r'Progress saved:.*page=1', output), output[-4000:]
    assert 'Entering deep sleep' in output
    assert bitmap(root / 'first.bmp') != bitmap(root / 'second.bmp'), 'page turn did not change content'
    state = json.loads((config / 'state.json').read_text())
    assert state['openEpubPath'] == '/books/fixture.epub' and state['lastSleepFromReader']
    output = run('resume', '6000:QUIT', '4000:resumed.bmp')
    assert 'Entering activity: EpubReader' in output
    assert bitmap(root / 'second.bmp') == bitmap(root / 'resumed.bmp'), 'resume changed reading location'
    print(f'PASS PaperDIY read/page/sleep/restart/resume; artifacts: {root}')


if __name__ == '__main__':
    main()
