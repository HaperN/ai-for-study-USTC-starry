"""Scanned directory pixels reach the model unchanged; composite pages stay whole."""
import base64
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageChops

from blocks.parse.toc.toc_review import render_toc_pages


def scan_book(tmp_path, size=(700,1023), *, overlay=False, rotation=0, clip_fraction=None):
    pixels = Image.new('RGB',size,'white')
    # Contrasting individual pixels detect resampling, including nominally equal sizes.
    pixels.putpixel((5,7),(13,29,211))
    buffer = BytesIO(); pixels.save(buffer,format='PNG')
    pdf = tmp_path/'book.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=168,height=245.52)
        xref = page.insert_image(page.rect,stream=buffer.getvalue(),keep_proportion=False)
        if clip_fraction is not None:
            content = page.get_contents()[0]
            doc.update_stream(content,
                f'q\n0 0 {168*clip_fraction} 245.52 re W n\n'.encode()
                + doc.xref_stream(content) + b'\nQ')
        if overlay:
            page.draw_rect(pymupdf.Rect(20,20,40,40),color=(1,0,0),fill=(1,0,0))
        page.set_rotation(rotation)
        doc.save(pdf)
    with pymupdf.open(pdf) as doc:
        extracted = doc.extract_image(xref)['image']
    return pdf,extracted


@pytest.mark.parametrize('size',[(700,1023),(120,180),(4200,700)])
def test_original_scan_dimensions_and_pixels_are_preserved(tmp_path,size):
    pdf,original = scan_book(tmp_path,size)
    shot = render_toc_pages(pdf,[1],tmp_path/'shots')[0]
    assert shot['render_policy']=='embedded-original-v1'
    assert (shot['width'],shot['height'])==size
    assert Path(shot['path']).read_bytes()==original
    with Image.open(shot['path']) as actual, Image.open(BytesIO(original)) as expected:
        assert ImageChops.difference(actual.convert('RGB'),expected.convert('RGB')).getbbox() is None


def test_model_request_preserves_original_image_bytes(tmp_path):
    from aiforstudy.providers.transport import RealTransport
    pdf,original = scan_book(tmp_path)
    shot = render_toc_pages(pdf,[1],tmp_path/'shots')[0]
    transport = RealTransport(model='offline',api_key='offline-key')
    body = transport.request_body(messages=[{'role':'user','content':'Read directory'}],images=[shot['path']])
    encoded = body['messages'][0]['content'][1]['image_url']['url'].split(',',1)[1]
    assert base64.b64decode(encoded)==original


@pytest.mark.parametrize('fraction,policy',[
    (1,'embedded-original-v1'),(.5,'page-composite-native-density-v1')])
def test_clipping_only_allows_unmodified_full_page_scan(tmp_path,fraction,policy):
    pdf,original=scan_book(tmp_path,clip_fraction=fraction)
    shot=render_toc_pages(pdf,[1],tmp_path/'shots')[0]
    assert shot['render_policy']==policy
    if fraction==1:
        assert Path(shot['path']).read_bytes()==original


def test_visible_overlay_is_kept_in_full_page_composition(tmp_path):
    pdf,_ = scan_book(tmp_path,overlay=True)
    shot = render_toc_pages(pdf,[1],tmp_path/'shots')[0]
    assert shot['render_policy']=='page-composite-native-density-v1'
    with Image.open(shot['path']) as image:
        pixel=image.convert('RGB').getpixel((int(30/168*image.width),int(30/245.52*image.height)))
        assert pixel[0]>240 and pixel[1]<20 and pixel[2]<20


def test_page_rotation_keeps_display_orientation(tmp_path):
    pdf,_ = scan_book(tmp_path,rotation=90)
    shot = render_toc_pages(pdf,[1],tmp_path/'shots')[0]
    assert shot['render_policy']=='page-composite-native-density-v1'
    assert shot['width']>shot['height']
    assert abs(shot['width']/shot['height']-245.52/168)<.005


def test_vector_page_has_explicit_rasterization_not_fake_native_pixels(tmp_path):
    pdf=tmp_path/'vector.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=168,height=245.52)
        page.insert_text((10,30),'Chapter 2',fontsize=10)
        doc.save(pdf)
    shot=render_toc_pages(pdf,[1],tmp_path/'shots')[0]
    assert shot['render_policy']=='vector-raster-v1'
    assert shot['source_images']==[]
    assert shot['effective_dpi']==300


def test_jpeg_decode_inversion_preserves_pdf_appearance(tmp_path):
    data=BytesIO(); Image.new('RGB',(40,60),(20,40,60)).save(data,format='JPEG')
    pdf=tmp_path/'inverted.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=40,height=60)
        xref=page.insert_image(page.rect,stream=data.getvalue())
        doc.xref_set_key(xref,'Decode','[1 0 1 0 1 0]')
        expected=page.get_pixmap().pixel(20,30)
        doc.save(pdf)
    shot=render_toc_pages(pdf,[1],tmp_path/'shots')[0]
    with Image.open(shot['path']) as image:
        assert image.convert('RGB').getpixel((20,30))==expected


def test_tiny_dense_image_cannot_cause_unbounded_page_allocation(tmp_path,monkeypatch):
    data=BytesIO(); Image.new('RGB',(2000,20),'black').save(data,format='PNG')
    pdf=tmp_path/'badge.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=595,height=842)
        page.insert_image(pymupdf.Rect(0,0,10,1),stream=data.getvalue(),keep_proportion=False)
        doc.save(pdf)
    def refuse_allocation(*args,**kwargs):
        raise AssertionError('unsafe page allocation reached')
    monkeypatch.setattr(pymupdf.Page,'get_pixmap',refuse_allocation)
    with pytest.raises(ValueError,match='合成.*像素'):
        render_toc_pages(pdf,[1],tmp_path/'shots')
