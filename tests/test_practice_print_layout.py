from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from tools.find_print_image_slices import slices
from tools.make_print_links_portable import make_portable


def test_long_figure_splits_only_between_panels_and_retains_every_row(tmp_path):
    image = Image.new('RGB', (100, 1500), 'white')
    draw = ImageDraw.Draw(image)
    for y in (30, 530, 1030):
        draw.rectangle((10, y, 90, y + 400), fill='black')
    path = tmp_path / 'panels.png'
    image.save(path)
    result = slices(path, 880)
    assert result['status'] == 'whitespace_slices'
    spans = result['segments']
    assert spans[0][0] == 0 and spans[-1][1] == 1500
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))
    assert all(b - a <= 880 for a, b in spans)
    assert all(image.getpixel((50, b)) == (255, 255, 255) for _, b in spans[:-1])


def test_continuous_tall_figure_requires_manual_layout(tmp_path):
    path = tmp_path / 'continuous.png'
    Image.new('RGB', (100, 1500), 'black').save(path)
    assert slices(path, 880)['status'] == 'no_safe_whitespace_cut'


def write_link_pdf(path: Path, target: str):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    annotation = DictionaryObject({
        NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Link'),
        NameObject('/Rect'): ArrayObject([NumberObject(v) for v in (0, 0, 50, 50)]),
        NameObject('/A'): DictionaryObject({NameObject('/S'): NameObject('/URI'), NameObject('/URI'): TextStringObject(target)}),
    })
    page[NameObject('/Annots')] = ArrayObject([writer._add_object(annotation)])
    writer.write(path)
    writer.close()


def test_printed_pdf_links_still_resolve_after_folder_move(tmp_path):
    root = tmp_path / 'course'
    (root / '原卷').mkdir(parents=True)
    (root / '打印').mkdir()
    target = root / '原卷/复变 A.pdf'
    target.write_bytes(b'%PDF-test')
    pdf = root / '打印/题.pdf'
    write_link_pdf(pdf, target.as_uri() + '#page=7')
    result = make_portable(pdf, root)
    assert result['portable_links_rewritten'] == 1
    moved = tmp_path / 'moved course'
    root.rename(moved)
    check = PdfReader(moved / '打印/题.pdf')
    uri = str(check.pages[0]['/Annots'][0].get_object()['/A']['/URI'])
    from urllib.parse import unquote, urlsplit
    parsed = urlsplit(uri)
    assert parsed.scheme == '' and parsed.fragment == 'page=7'
    assert (moved / '打印' / unquote(parsed.path)).resolve().is_file()


def test_printed_pdf_rejects_private_producer_paths(tmp_path):
    root = tmp_path / 'course'
    root.mkdir()
    private = tmp_path / 'private.txt'
    private.write_text('private')
    pdf = root / 'bad.pdf'
    write_link_pdf(pdf, private.as_uri())
    before = pdf.read_bytes()
    with pytest.raises(ValueError):
        make_portable(pdf, root)
    assert pdf.read_bytes() == before
